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

"""Integration tests for the auth library against a real Postgres.

No mocks (PRD §7e). These run against a migrated database — the auth tables of
``alembic upgrade head`` — and exercise the canonical-store side of the library:

* ``user_account`` creation and the Argon2id hash round-trip through the
  ``bytea`` column;
* ``user_session`` lifecycle — create, observe, revoke;
* ``auth_audit_event`` append via :class:`AuditTrail`, including the
  ``auth.audit`` mirror seam;
* ``user_role_assignment`` grant, revoke and live-role resolution via
  :class:`RoleStore`;
* a representative end-to-end :class:`AuthService` flow.

Every test runs inside the wrapping transaction from ``tests/conftest.py`` and
is rolled back at teardown, so the suite is hermetic without truncating tables.
"""

from __future__ import annotations

import datetime
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection

from trustlist_auth.audit import AuditTrail, AuthEventType
from trustlist_auth.errors import AuthenticationFailed, RoleScopeError, UnknownRole
from trustlist_auth.passwords import hash_password, verify_password
from trustlist_auth.rbac import Archetype, Permission
from trustlist_auth.role_store import RoleStore
from trustlist_auth.testing import InMemoryHarness, RecordingAuditEventSink

pytestmark = pytest.mark.integration


# --- helpers ----------------------------------------------------------------


def _create_user_account(
    connection: Connection,
    archetype: Archetype,
    *,
    password: str = "integration-pw",
) -> str:
    """Insert a ``user_account`` row and return its generated ``user_id``.

    The password is Argon2id-hashed by the library and stored as ``bytea`` —
    exactly the path a per-archetype registration surface (issues 17–19) takes.
    """
    digest = hash_password(password)
    user_id = connection.execute(
        text(
            "INSERT INTO user_account (archetype, email, password_hash) "
            "VALUES (:archetype, :email, :hash) RETURNING user_id"
        ),
        {
            "archetype": archetype.value,
            "email": f"{archetype.value}-{uuid.uuid4()}@example.com",
            "hash": digest.encode("utf-8"),
        },
    ).scalar_one()
    return str(user_id)


# --- user_account -----------------------------------------------------------


def test_user_account_password_hash_round_trips_through_bytea(
    connection: Connection,
) -> None:
    """An Argon2id hash stored as bytea reads back and verifies the password."""
    user_id = _create_user_account(connection, Archetype.OPERATOR, password="round-trip-pw")
    stored = connection.execute(
        text("SELECT password_hash FROM user_account WHERE user_id = :id"),
        {"id": user_id},
    ).scalar_one()
    # The bytea column yields bytes; decode back to the PHC string and verify.
    assert verify_password(bytes(stored).decode("utf-8"), "round-trip-pw") is True
    assert verify_password(bytes(stored).decode("utf-8"), "wrong-pw") is False


def test_user_account_starts_unverified_and_without_totp(
    connection: Connection,
) -> None:
    """A freshly created user_account row has no email verification or TOTP."""
    user_id = _create_user_account(connection, Archetype.FOUNDATION_INTERNAL)
    row = connection.execute(
        text(
            "SELECT email_verified_at, totp_secret, totp_enrolled_at, disabled_at "
            "FROM user_account WHERE user_id = :id"
        ),
        {"id": user_id},
    ).one()
    assert row.email_verified_at is None
    assert row.totp_secret is None
    assert row.totp_enrolled_at is None
    assert row.disabled_at is None


# --- user_session -----------------------------------------------------------


def test_user_session_lifecycle(connection: Connection) -> None:
    """A user_session row is created, observed live, then revoked."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    session_id = connection.execute(
        text(
            "INSERT INTO user_session (user_id, device_fingerprint) "
            "VALUES (:user_id, :fp) RETURNING session_id"
        ),
        {"user_id": user_id, "fp": "device-abc"},
    ).scalar_one()

    # Live: revoked_at is NULL; the active-session index query finds it.
    active = connection.execute(
        text(
            "SELECT count(*) FROM user_session "
            "WHERE user_id = :user_id AND revoked_at IS NULL"
        ),
        {"user_id": user_id},
    ).scalar_one()
    assert active == 1

    # Revoke and confirm it leaves the active set.
    connection.execute(
        text("UPDATE user_session SET revoked_at = now() WHERE session_id = :id"),
        {"id": session_id},
    )
    active_after = connection.execute(
        text(
            "SELECT count(*) FROM user_session "
            "WHERE user_id = :user_id AND revoked_at IS NULL"
        ),
        {"user_id": user_id},
    ).scalar_one()
    assert active_after == 0


# --- auth_audit_event -------------------------------------------------------


def test_audit_trail_appends_a_row(connection: Connection) -> None:
    """AuditTrail.record writes a row to auth_audit_event."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    sink = RecordingAuditEventSink()
    trail = AuditTrail(sink)

    event = trail.record(
        connection,
        AuthEventType.LOGIN_SUCCESS,
        user_id=user_id,
        ip_address_observed="203.0.113.7",
        event_details={"archetype": "operator"},
    )

    row = connection.execute(
        text(
            "SELECT user_id, event_type, ip_address_observed, event_details "
            "FROM auth_audit_event WHERE auth_audit_event_id = :id"
        ),
        {"id": event.event_id},
    ).one()
    assert str(row.user_id) == user_id
    assert row.event_type == "login_success"
    assert str(row.ip_address_observed) == "203.0.113.7"
    assert row.event_details == {"archetype": "operator"}


def test_audit_trail_mirrors_to_the_event_sink(connection: Connection) -> None:
    """Every persisted audit row is also mirrored to the auth.audit sink."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    sink = RecordingAuditEventSink()
    trail = AuditTrail(sink)

    trail.record(connection, AuthEventType.LOGIN_SUCCESS, user_id=user_id)
    trail.record(connection, AuthEventType.SESSION_REVOKE, user_id=user_id)

    # The DB row count and the mirrored-event count agree — "writes a row AND
    # emits a matching event" (issue 16 acceptance criterion).
    db_count = connection.execute(
        text("SELECT count(*) FROM auth_audit_event WHERE user_id = :id"),
        {"id": user_id},
    ).scalar_one()
    assert db_count == 2
    assert sink.event_types() == ["login_success", "session_revoke"]


def test_audit_trail_allows_null_user_for_failed_login(
    connection: Connection,
) -> None:
    """A pre-identification event (login failure) persists with a NULL user_id."""
    sink = RecordingAuditEventSink()
    trail = AuditTrail(sink)
    event = trail.record(
        connection,
        AuthEventType.LOGIN_FAILURE,
        user_id=None,
        event_details={"email": "unknown@example.com"},
    )
    user_id = connection.execute(
        text("SELECT user_id FROM auth_audit_event WHERE auth_audit_event_id = :id"),
        {"id": event.event_id},
    ).scalar_one()
    assert user_id is None


def test_audit_trail_rejects_credential_material(connection: Connection) -> None:
    """AuditTrail refuses to persist a credential in the detail object."""
    trail = AuditTrail()
    with pytest.raises(ValueError, match="credential material"):
        trail.record(
            connection,
            AuthEventType.PASSWORD_CHANGE,
            user_id=None,
            event_details={"password": "should-never-be-here"},
        )


def test_audit_trail_per_user_index_query(connection: Connection) -> None:
    """Per-user audit events read back most-recent-first (the §7a index)."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    trail = AuditTrail()
    base = datetime.datetime(2026, 5, 20, 9, 0, tzinfo=datetime.UTC)
    for offset, kind in enumerate(
        (AuthEventType.LOGIN_SUCCESS, AuthEventType.MFA_CHALLENGE_SUCCESS)
    ):
        trail.record(
            connection,
            kind,
            user_id=user_id,
            occurred_at=base + datetime.timedelta(minutes=offset),
        )
    ordered = connection.execute(
        text(
            "SELECT event_type FROM auth_audit_event "
            "WHERE user_id = :id ORDER BY occurred_at DESC"
        ),
        {"id": user_id},
    ).scalars().all()
    assert ordered == ["mfa_challenge_success", "login_success"]


# --- user_role_assignment ---------------------------------------------------


def test_role_store_grants_and_resolves_an_operator_role(
    connection: Connection,
) -> None:
    """RoleStore grants an operator role and resolves it as a live assignment."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    store = RoleStore()
    store.grant_role(
        connection,
        user_id=user_id,
        archetype=Archetype.OPERATOR,
        role="admin",
        granted_by_user_id=None,
    )
    live = store.live_role_identifiers(connection, user_id)
    assert live == frozenset({"admin"})


def test_role_store_rejects_unknown_role(connection: Connection) -> None:
    """Granting a role outside the archetype catalogue raises UnknownRole."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    store = RoleStore()
    with pytest.raises(UnknownRole):
        store.grant_role(
            connection,
            user_id=user_id,
            archetype=Archetype.OPERATOR,
            role="analyst",  # a brand-customer role, not an operator one
            granted_by_user_id=None,
        )


def test_role_store_enforces_brand_customer_customer_id(
    connection: Connection,
) -> None:
    """A brand-customer grant without a customer_id raises RoleScopeError."""
    user_id = _create_user_account(connection, Archetype.BRAND_CUSTOMER)
    store = RoleStore()
    with pytest.raises(RoleScopeError):
        store.grant_role(
            connection,
            user_id=user_id,
            archetype=Archetype.BRAND_CUSTOMER,
            role="analyst",
            granted_by_user_id=None,
            customer_id=None,
        )


def test_role_store_rejects_customer_id_on_operator_grant(
    connection: Connection,
) -> None:
    """An operator grant carrying a customer_id raises RoleScopeError."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    store = RoleStore()
    with pytest.raises(RoleScopeError):
        store.grant_role(
            connection,
            user_id=user_id,
            archetype=Archetype.OPERATOR,
            role="admin",
            granted_by_user_id=None,
            customer_id=str(uuid.uuid4()),
        )


def test_role_store_brand_customer_grant_is_customer_scoped(
    connection: Connection,
) -> None:
    """A brand-customer grant is visible only under its own customer scope."""
    user_id = _create_user_account(connection, Archetype.BRAND_CUSTOMER)
    customer_id = str(uuid.uuid4())
    other_customer = str(uuid.uuid4())
    store = RoleStore()
    store.grant_role(
        connection,
        user_id=user_id,
        archetype=Archetype.BRAND_CUSTOMER,
        role="analyst",
        granted_by_user_id=None,
        customer_id=customer_id,
    )
    # Visible under the granting customer's scope ...
    assert store.live_role_identifiers(
        connection, user_id, customer_id=customer_id
    ) == frozenset({"analyst"})
    # ... and not under a different customer's scope.
    assert store.live_role_identifiers(
        connection, user_id, customer_id=other_customer
    ) == frozenset()


def test_role_store_revoke(connection: Connection) -> None:
    """A revoked role leaves the live-assignment set."""
    user_id = _create_user_account(connection, Archetype.OPERATOR)
    store = RoleStore()
    assignment = store.grant_role(
        connection,
        user_id=user_id,
        archetype=Archetype.OPERATOR,
        role="operator",
        granted_by_user_id=None,
    )
    assert store.revoke_role(
        connection,
        user_id=user_id,
        role="operator",
        granted_at=assignment.granted_at,
    ) is True
    assert store.live_role_identifiers(connection, user_id) == frozenset()
    # A second revoke of the same grant reports no change.
    assert store.revoke_role(
        connection,
        user_id=user_id,
        role="operator",
        granted_at=assignment.granted_at,
    ) is False


# --- end-to-end AuthService flow -------------------------------------------


def test_auth_service_end_to_end(
    connection: Connection, harness: InMemoryHarness
) -> None:
    """A representative registration → 2FA → login → RBAC flow.

    Exercises the composed AuthService: the in-memory provider drives the
    credential and session side, while the audit trail and role store write to
    the real Postgres. This is the shape the per-archetype surfaces (issues
    17–19) will build on.
    """
    service = harness.service
    sink = harness.sink

    # Register and verify email (tier-0) with the provider.
    user = service.register(Archetype.OPERATOR, "e2e@example.com", "e2e-strong-pw")
    service.mark_email_verified(user.user_id)

    # The audit trail needs a real user_account row to satisfy the FK; create
    # one carrying the same user_id the provider minted (the per-archetype
    # surface keeps the two in step).
    connection.execute(
        text(
            "INSERT INTO user_account (user_id, archetype, email, password_hash, "
            " email_verified_at) "
            "VALUES (:id, 'operator', :email, :hash, now())"
        ),
        {
            "id": user.user_id,
            "email": "e2e@example.com",
            "hash": hash_password("e2e-strong-pw").encode("utf-8"),
        },
    )

    # Enrol TOTP and complete it with a current code.
    enrolment = service.enrol_totp(connection, user.user_id)
    from trustlist_auth.totp import current_totp

    assert service.verify_totp(
        connection, user.user_id, current_totp(enrolment.secret)
    ) is True

    # First factor: authenticate.
    authed = service.authenticate(
        connection, Archetype.OPERATOR, "e2e@example.com", "e2e-strong-pw"
    )
    assert authed.user_id == user.user_id

    # Issue a session.
    session = service.issue_session(connection, user.user_id)
    assert service.get_session(session.session_id).user_id == user.user_id

    # Grant a role and check a permission resolves through the canonical store.
    service.assign_role(
        connection,
        user_id=user.user_id,
        archetype=Archetype.OPERATOR,
        role="admin",
        granted_by=None,
    )
    assert service.has_permission(
        connection, user.user_id, Archetype.OPERATOR, Permission.TEAM_MANAGE
    ) is True
    assert service.has_permission(
        connection, user.user_id, Archetype.OPERATOR, Permission.ACCOUNT_MANAGE
    ) is False

    # The audit trail recorded every event in both Postgres and the mirror.
    db_event_types = connection.execute(
        text(
            "SELECT event_type FROM auth_audit_event "
            "WHERE user_id = :id ORDER BY occurred_at"
        ),
        {"id": user.user_id},
    ).scalars().all()
    assert "mfa_challenge_issued" in db_event_types
    assert "mfa_challenge_success" in db_event_types
    assert "login_success" in db_event_types
    assert "role_grant" in db_event_types
    # The mirror seam saw the same set of events as the database.
    assert sorted(sink.event_types()) == sorted(db_event_types)


def test_auth_service_failed_login_is_audited(
    connection: Connection, harness: InMemoryHarness
) -> None:
    """A failed authentication records a login_failure with a NULL user_id."""
    service = harness.service
    with pytest.raises(AuthenticationFailed):
        service.authenticate(
            connection, Archetype.OPERATOR, "ghost@example.com", "bad-pw"
        )
    failures = connection.execute(
        text(
            "SELECT user_id, event_type FROM auth_audit_event "
            "WHERE event_type = 'login_failure'"
        )
    ).all()
    assert len(failures) == 1
    assert failures[0].user_id is None
