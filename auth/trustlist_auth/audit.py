# Copyright 2026 The TrustList Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Authentication audit-trail emission.

Every authentication event — login, MFA challenge, role grant, account disable —
writes a row to the canonical ``auth_audit_event`` table and is mirrored to the
``auth.audit`` event-bus topic (PRD §7a, §7b, §7e).

This module owns the *write* side of that requirement:

* :class:`AuditTrail` writes a row to ``auth_audit_event`` over a SQLAlchemy
  connection, and then hands the same event to an :class:`AuditEventSink` for
  the event-bus mirror.

The event-bus seam — read carefully.
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

PRD §7b routes ``auth_audit_event`` writes onto the ``auth.audit`` topic. The
Python event-bus SDK that publishes there is issue 13, which is **not yet
merged into ``main``**. To avoid taking a hard dependency on unmerged code,
this module mirrors through an :class:`AuditEventSink` *protocol* rather than
importing the SDK:

* :class:`NullAuditEventSink` — the Stage-0 default. Discards the event. The
  database row is still the system of record, so dropping the mirror loses no
  audit data; it only defers the downstream observability feed.
* :class:`LoggingAuditEventSink` — mirrors the event to the structured logger,
  useful in local development and as a visible placeholder.
* the event-bus sink — *to be added when issue 13 lands*. It will be a thin
  adapter: construct the §7b event envelope and call the SDK producer. The
  seam is :class:`AuditEventSink`; wiring it is a one-class change, not a
  rewrite. The ``# EVENT-BUS SEAM`` comments below mark every spot that adapter
  touches.

The :class:`AuditEvent` value object already carries everything the §7b
envelope needs (``event_id``, ``event_type``, ``occurred_at``, the payload), so
the future adapter maps fields rather than inventing them.
"""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from sqlalchemy import text
from sqlalchemy.engine import Connection

from observability.logging import get_logger

# The event-bus topic that auth audit events are mirrored to (PRD §7b). Defined
# here as the contract constant the future event-bus sink will publish to.
AUTH_AUDIT_TOPIC = "auth.audit"

_logger = get_logger("auth")


class AuthEventType(StrEnum):
    """The audit-event taxonomy — mirrors ``auth_audit_event_type`` in the model.

    The string values are exactly the Postgres enum labels, so an
    :class:`AuthEventType` can be bound straight into the INSERT.
    """

    LOGIN_SUCCESS = "login_success"
    LOGIN_FAILURE = "login_failure"
    MFA_CHALLENGE_ISSUED = "mfa_challenge_issued"
    MFA_CHALLENGE_SUCCESS = "mfa_challenge_success"
    MFA_CHALLENGE_FAILURE = "mfa_challenge_failure"
    PASSWORD_CHANGE = "password_change"
    ROLE_GRANT = "role_grant"
    ROLE_REVOKE = "role_revoke"
    ACCOUNT_DISABLE = "account_disable"
    SESSION_REVOKE = "session_revoke"


@dataclass(frozen=True)
class AuditEvent:
    """One authentication-audit event.

    Constructed by :class:`AuditTrail`, persisted to ``auth_audit_event`` and
    handed to the :class:`AuditEventSink` for the ``auth.audit`` mirror. The
    field set is a superset of what the §7b event envelope needs, so the future
    event-bus sink maps rather than recomputes.

    :param event_id: a fresh UUID — the ``auth_audit_event_id`` primary key and
        the envelope's ``event_id`` for idempotency.
    :param event_type: the audit-event kind.
    :param occurred_at: when the event occurred (UTC).
    :param user_id: the account concerned, or ``None`` for a pre-identification
        event such as a login failure on an unknown address.
    :param ip_address_observed: the observed client IP, or ``None``.
    :param user_agent_observed: the observed client user agent, or ``None``.
    :param event_details: a JSON-serialisable detail object. Must never carry a
        password, a TOTP secret or any other credential material — see
        :meth:`AuditTrail.record`.
    """

    event_id: str
    event_type: AuthEventType
    occurred_at: datetime.datetime
    user_id: str | None = None
    ip_address_observed: str | None = None
    user_agent_observed: str | None = None
    event_details: dict[str, object] = field(default_factory=dict)


# Detail keys that must never appear in an audit event — a defence-in-depth
# guard against a caller accidentally threading a credential into the trail.
_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "password",
        "plaintext",
        "new_password",
        "old_password",
        "totp_secret",
        "secret",
        "password_hash",
        "code",
        "totp_code",
    }
)


@runtime_checkable
class AuditEventSink(Protocol):
    """The mirror seam — where an :class:`AuditEvent` goes after the DB write.

    A sink receives every event that was persisted to ``auth_audit_event``.
    The Stage-0 default sink discards; the event-bus sink (issue 13) will
    publish to the ``auth.audit`` topic. Implementations must not raise: a sink
    failure must not undo a persisted audit row.
    """

    def emit(self, event: AuditEvent) -> None:
        """Mirror ``event`` downstream. Must not raise."""
        ...


class NullAuditEventSink:
    """An :class:`AuditEventSink` that discards every event.

    The Stage-0 default. The ``auth_audit_event`` row is the system of record,
    so discarding the mirror loses no audit data — it only defers the
    downstream ``auth.audit`` feed until the event-bus sink is wired (issue 13).
    """

    def emit(self, event: AuditEvent) -> None:
        """Discard ``event``."""
        # EVENT-BUS SEAM: replaced by the event-bus sink when issue 13 lands.


class LoggingAuditEventSink:
    """An :class:`AuditEventSink` that mirrors each event to the structured log.

    Useful in local development and as a visible placeholder for the event-bus
    mirror. Not a substitute for the real ``auth.audit`` feed.
    """

    def emit(self, event: AuditEvent) -> None:
        """Mirror ``event`` to the structured logger at INFO."""
        # EVENT-BUS SEAM: in production this becomes a publish to AUTH_AUDIT_TOPIC.
        _logger.info(
            "auth audit event",
            audit_event_id=event.event_id,
            audit_event_type=event.event_type.value,
            audit_user_id=event.user_id,
            audit_topic=AUTH_AUDIT_TOPIC,
        )


class AuditTrail:
    """Writes authentication events to ``auth_audit_event`` and mirrors them.

    A single :class:`AuditTrail` is constructed once at composition time with
    the chosen :class:`AuditEventSink` and reused. It does not own a database
    connection: each :meth:`record` call is given the connection of the
    surrounding unit of work, so the audit row commits in the same transaction
    as the action it records.
    """

    def __init__(self, sink: AuditEventSink | None = None) -> None:
        """Bind the audit trail to ``sink``.

        :param sink: where persisted events are mirrored. Defaults to
            :class:`NullAuditEventSink` — the Stage-0 default until the
            event-bus sink lands (issue 13).
        """
        self._sink: AuditEventSink = sink or NullAuditEventSink()

    def record(
        self,
        connection: Connection,
        event_type: AuthEventType,
        *,
        user_id: str | None = None,
        ip_address_observed: str | None = None,
        user_agent_observed: str | None = None,
        event_details: dict[str, object] | None = None,
        occurred_at: datetime.datetime | None = None,
    ) -> AuditEvent:
        """Persist an audit event and mirror it to the sink.

        The row is INSERTed on ``connection`` — the caller's transaction — so
        it commits atomically with the action being audited. After a successful
        INSERT the event is handed to the :class:`AuditEventSink`; a sink that
        misbehaves cannot undo the persisted row, which is the system of record.

        :param connection: the SQLAlchemy connection of the current unit of
            work.
        :param event_type: the kind of event.
        :param user_id: the account concerned, or ``None`` for a
            pre-identification event.
        :param event_details: a JSON-serialisable detail object.
        :returns: the persisted :class:`AuditEvent`.
        :raises ValueError: if ``event_details`` carries a forbidden key — a
            defence-in-depth check against logging a credential.
        """
        details = dict(event_details or {})
        forbidden = _FORBIDDEN_DETAIL_KEYS.intersection(
            key.lower() for key in details
        )
        if forbidden:
            offending = ", ".join(sorted(forbidden))
            raise ValueError(
                f"audit event_details must never carry credential material; "
                f"forbidden key(s): {offending}"
            )

        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            occurred_at=occurred_at or datetime.datetime.now(tz=datetime.UTC),
            user_id=user_id,
            ip_address_observed=ip_address_observed,
            user_agent_observed=user_agent_observed,
            event_details=details,
        )

        connection.execute(
            text(
                "INSERT INTO auth_audit_event "
                "(auth_audit_event_id, user_id, event_type, occurred_at, "
                " ip_address_observed, user_agent_observed, event_details) "
                "VALUES (:event_id, :user_id, :event_type, :occurred_at, "
                "        :ip, :user_agent, CAST(:details AS jsonb))"
            ),
            {
                "event_id": event.event_id,
                "user_id": event.user_id,
                "event_type": event.event_type.value,
                "occurred_at": event.occurred_at,
                "ip": event.ip_address_observed,
                "user_agent": event.user_agent_observed,
                "details": json.dumps(event.event_details),
            },
        )

        # EVENT-BUS SEAM: the DB row is the system of record; this hands the
        # event to the mirror. With the Stage-0 NullAuditEventSink this is a
        # no-op; the event-bus sink (issue 13) publishes to AUTH_AUDIT_TOPIC.
        self._sink.emit(event)
        return event
