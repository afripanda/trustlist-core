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

"""The typed event envelope (Stage 0 PRD §7b).

Every event on the TrustList bus, regardless of payload, carries the same
envelope. This module defines that envelope as typed Python objects and the
JSON (de)serialisation that ferries it through Kafka.

The envelope fields, exactly per §7b:

- ``event_id`` — UUID, for idempotency and deduplication.
- ``event_type`` — text, the topic plus a payload-type qualifier.
- ``event_version`` — semver, for schema evolution.
- ``produced_at`` — timestamptz (an aware :class:`~datetime.datetime`).
- ``producer_id`` — text, identifies the emitting component instance.
- ``trace_context`` — the W3C ``traceparent`` / ``tracestate`` carrier,
  written by :func:`observability.inject_trace_context` on the producer side
  and read by :func:`observability.extract_trace_context` on the consumer
  side, so a signal's collector → bus → consumer flow is a single trace.
- ``idempotency_key`` — text, derived from payload-specific fields; consumers
  deduplicate on it.
- ``provenance`` — the source / method / contributor-identity object of §7a.
- ``payload`` — the event-type-specific body, schema-validated separately.

The envelope is deliberately a plain frozen dataclass: it has no dependency on
the Kafka client or the schema registry, so it can be unit-tested in complete
isolation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# The envelope schema version. Bumped only when the *envelope* shape changes,
# independently of any payload's ``event_version``.
ENVELOPE_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class Provenance:
    """The origin of an observation (Stage 0 PRD §7a `provenance`).

    :ivar source: the origin class — ``system``, ``human``, ``contributor`` or
        ``cti_partner`` per the §7a ``evidence.source`` enum.
    :ivar method: how the observation was made — a free-text method label such
        as ``dns-lookup`` or ``http-probe``.
    :ivar contributor_identity: the contributor's identity, when the source is
        a human or a partner; ``None`` for fully automated system signals.
    """

    source: str
    method: str
    contributor_identity: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Render the provenance as a JSON-serialisable dict."""
        return {
            "source": self.source,
            "method": self.method,
            "contributor_identity": self.contributor_identity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Provenance:
        """Rebuild a :class:`Provenance` from its dict form."""
        return cls(
            source=data["source"],
            method=data["method"],
            contributor_identity=data.get("contributor_identity"),
        )


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    """A fully-constructed event envelope ready to publish or just consumed.

    Instances are immutable. Producers build one via :func:`new_envelope`,
    which fills the generated fields (``event_id``, ``produced_at``); a
    consumer rebuilds one from the wire via :meth:`from_bytes`.
    """

    event_type: str
    event_version: str
    producer_id: str
    idempotency_key: str
    provenance: Provenance
    payload: dict[str, Any]
    trace_context: dict[str, str] = field(default_factory=dict)
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    produced_at: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )

    def to_dict(self) -> dict[str, Any]:
        """Render the envelope as a JSON-serialisable dict.

        ``produced_at`` is emitted as an ISO-8601 string and ``event_id`` as
        its canonical UUID string, so the result round-trips losslessly
        through :func:`json.dumps`.
        """
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type,
            "event_version": self.event_version,
            "produced_at": self.produced_at.isoformat(),
            "producer_id": self.producer_id,
            "trace_context": dict(self.trace_context),
            "idempotency_key": self.idempotency_key,
            "provenance": self.provenance.to_dict(),
            "payload": self.payload,
        }

    def to_bytes(self) -> bytes:
        """Serialise the envelope to the UTF-8 JSON bytes put on the wire."""
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventEnvelope:
        """Rebuild an :class:`EventEnvelope` from its dict form.

        :raises KeyError: when a required envelope field is absent — a
            malformed envelope is a hard error, not a silently-defaulted one.
        """
        return cls(
            event_id=uuid.UUID(data["event_id"]),
            event_type=data["event_type"],
            event_version=data["event_version"],
            produced_at=datetime.fromisoformat(data["produced_at"]),
            producer_id=data["producer_id"],
            trace_context=dict(data.get("trace_context") or {}),
            idempotency_key=data["idempotency_key"],
            provenance=Provenance.from_dict(data["provenance"]),
            payload=data["payload"],
        )

    @classmethod
    def from_bytes(cls, raw: bytes) -> EventEnvelope:
        """Rebuild an :class:`EventEnvelope` from on-the-wire JSON bytes."""
        return cls.from_dict(json.loads(raw.decode("utf-8")))


def new_envelope(
    *,
    event_type: str,
    payload: dict[str, Any],
    producer_id: str,
    idempotency_key: str,
    provenance: Provenance,
    trace_context: dict[str, str] | None = None,
    event_version: str = ENVELOPE_VERSION,
    event_id: uuid.UUID | None = None,
    produced_at: datetime | None = None,
) -> EventEnvelope:
    """Construct an :class:`EventEnvelope`, filling the generated fields.

    This is the single typed constructor producers use. ``event_id`` and
    ``produced_at`` are generated when not supplied; an explicit value for
    either is honoured, which is what makes a produce call reproducible in a
    test fixture.

    :param event_type: the topic plus payload-type qualifier.
    :param payload: the event-type-specific body. Validated against the
        schema registry by the producer — *not* here, so the envelope stays
        dependency-free.
    :param producer_id: identifies the emitting component instance.
    :param idempotency_key: the deduplication key — derive it with
        :func:`trustlist_event_bus.idempotency.derive_idempotency_key`.
    :param provenance: the observation's origin (§7a).
    :param trace_context: a W3C ``traceparent`` / ``tracestate`` carrier; when
        ``None`` an empty carrier is used and the producer fills it at
        publish time from the active span.
    :param event_version: the payload schema version; defaults to the envelope
        version.
    :param event_id: an explicit event id; generated when ``None``.
    :param produced_at: an explicit timestamp; ``datetime.now(UTC)`` when
        ``None``.
    :raises ValueError: when ``produced_at`` is supplied but is timezone-naive
        — the envelope's timestamp is always an aware UTC instant.
    """
    if produced_at is not None and produced_at.tzinfo is None:
        raise ValueError(
            "produced_at must be timezone-aware; the envelope timestamp is "
            "an aware UTC instant (PRD §7b `produced_at` is a timestamptz)."
        )
    kwargs: dict[str, Any] = {
        "event_type": event_type,
        "event_version": event_version,
        "producer_id": producer_id,
        "idempotency_key": idempotency_key,
        "provenance": provenance,
        "payload": payload,
        "trace_context": dict(trace_context) if trace_context else {},
    }
    if event_id is not None:
        kwargs["event_id"] = event_id
    if produced_at is not None:
        kwargs["produced_at"] = produced_at
    return EventEnvelope(**kwargs)
