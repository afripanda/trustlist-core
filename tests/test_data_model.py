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

"""Integration tests for the Stage 0 canonical data model (PRD §7a).

These tests run against a real, migrated Postgres — no mocks (PRD §7a
discipline). They verify, against ``alembic upgrade head`` output:

* all fifteen tables exist with the specified shape;
* every index from the §7a minimum index set exists;
* the ``domain.normalised_url`` CHECK rejects un-normalised input;
* the ``user_role_assignment`` archetype/customer_id CHECK behaves both ways;
* append-only discipline is enforced — UPDATE/DELETE on an append-only table
  from the application role is refused;
* ``evidence_current`` returns the latest evidence row per natural key;
* a basic CRUD cycle works on every table.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError, IntegrityError

from trustlist_data_model import APPEND_ONLY_TABLES

pytestmark = pytest.mark.integration

# The fifteen canonical tables of PRD §7a.
EXPECTED_TABLES = {
    # domain-side
    "domain",
    "pool",
    "domain_pool_membership",
    "attestation",
    "evidence",
    "provenance",
    "score",
    "score_history",
    # authentication-side
    "user_account",
    "user_session",
    "user_role_assignment",
    "auth_audit_event",
    "operator_account_extension",
    "brand_customer_account_extension",
    "foundation_user_account_extension",
}

# The §7a minimum index set, keyed by table. Names follow the metadata naming
# convention in trustlist_data_model.models.
EXPECTED_INDEXES = {
    "domain": {
        "uq_domain_normalised_url",
        "ix_domain_current_status",
        "ix_domain_last_scored_at",
    },
    "pool": {"uq_pool_canonical_handle"},
    "domain_pool_membership": {"ix_domain_pool_membership_domain_id_valid_until"},
    "evidence": {"ix_evidence_domain_id_signal_class_observed_at"},
    "score_history": {"ix_score_history_domain_id_computed_at"},
    "user_account": {"uq_user_account_archetype_email"},
    "user_session": {"ix_user_session_user_id_revoked_at"},
    "auth_audit_event": {"ix_auth_audit_event_user_id_occurred_at"},
}


# --- Helpers ----------------------------------------------------------------


def _insert_domain(conn: Connection, normalised_url: str, status: str = "grey") -> str:
    """Insert a domain and return its generated UUID."""
    row = conn.execute(
        text(
            "INSERT INTO domain (normalised_url, current_status) "
            "VALUES (:url, :status) RETURNING domain_id"
        ),
        {"url": normalised_url, "status": status},
    ).one()
    return str(row[0])


def _insert_provenance(conn: Connection, method: str = "dns-lookup") -> str:
    """Insert a provenance row and return its generated UUID."""
    row = conn.execute(
        text(
            "INSERT INTO provenance (source, method, observed_at) "
            "VALUES ('system', :method, now()) RETURNING provenance_id"
        ),
        {"method": method},
    ).one()
    return str(row[0])


def _insert_evidence(
    conn: Connection,
    domain_id: str,
    provenance_id: str,
    *,
    signal_class: str,
    source_url: str,
    observed_at: str,
    value: str,
) -> str:
    """Insert an evidence row and return its generated UUID."""
    row = conn.execute(
        text(
            "INSERT INTO evidence "
            "(domain_id, signal_class, source, method, source_url, "
            " observed_at, observed_value, provenance_id) "
            "VALUES (:domain_id, :signal_class, 'system', 'probe', :source_url, "
            "        :observed_at, :value, :provenance_id) "
            "RETURNING evidence_id"
        ),
        {
            "domain_id": domain_id,
            "signal_class": signal_class,
            "source_url": source_url,
            "observed_at": observed_at,
            "value": value,
            "provenance_id": provenance_id,
        },
    ).one()
    return str(row[0])


# --- Schema shape -----------------------------------------------------------


def test_all_fifteen_tables_exist(connection: Connection) -> None:
    """All fifteen §7a tables are present in the migrated database."""
    present = set(inspect(connection).get_table_names())
    missing = EXPECTED_TABLES - present
    assert not missing, f"missing canonical tables: {sorted(missing)}"


def test_minimum_index_set_exists(connection: Connection) -> None:
    """Every index from the §7a minimum index set is present."""
    inspector = inspect(connection)
    for table, expected in EXPECTED_INDEXES.items():
        names: set[str] = set()
        names.update(ix["name"] for ix in inspector.get_indexes(table) if ix["name"])
        names.update(
            uc["name"] for uc in inspector.get_unique_constraints(table) if uc["name"]
        )
        missing = expected - names
        assert not missing, f"{table}: missing indexes {sorted(missing)}"


def test_evidence_current_materialised_view_exists(connection: Connection) -> None:
    """The evidence_current materialised view exists and is keyed correctly."""
    matview = connection.execute(
        text("SELECT matviewname FROM pg_matviews WHERE matviewname = 'evidence_current'")
    ).scalar()
    assert matview == "evidence_current"
    # The unique index on the natural key is what REFRESH ... CONCURRENTLY needs.
    keyed = connection.execute(
        text("SELECT indexname FROM pg_indexes WHERE indexname = 'ux_evidence_current_natural_key'")
    ).scalar()
    assert keyed == "ux_evidence_current_natural_key"


def test_append_only_table_set_matches_migration(connection: Connection) -> None:
    """The ORM's append-only list matches the tables with no UPDATE/DELETE grant."""
    rows = connection.execute(
        text(
            "SELECT table_name, privilege_type FROM information_schema.role_table_grants "
            "WHERE grantee = 'trustlist_app'"
        )
    ).all()
    grants: dict[str, set[str]] = {}
    for table_name, privilege in rows:
        grants.setdefault(table_name, set()).add(privilege)
    for table in APPEND_ONLY_TABLES:
        assert grants.get(table) == {"SELECT", "INSERT"}, (
            f"{table} should grant only SELECT/INSERT, got {grants.get(table)}"
        )


# --- CHECK constraints ------------------------------------------------------


def test_domain_check_rejects_unnormalised_url(connection: Connection) -> None:
    """The domain.normalised_url CHECK rejects scheme / www / query / case."""
    bad_urls = [
        "https://example.com",  # scheme present
        "www.example.com",  # leading www.
        "example.com?utm=x",  # query string
        "example.com#frag",  # fragment
        "Example.com",  # uppercase
        "exa mple.com",  # whitespace
    ]
    for url in bad_urls:
        with pytest.raises(IntegrityError):
            with connection.begin_nested():
                connection.execute(
                    text(
                        "INSERT INTO domain (normalised_url, current_status) "
                        "VALUES (:url, 'grey')"
                    ),
                    {"url": url},
                )


def test_domain_check_accepts_normalised_url(connection: Connection) -> None:
    """A correctly normalised URL is accepted by the CHECK."""
    domain_id = _insert_domain(connection, "good.example.com")
    assert uuid.UUID(domain_id)


def test_brand_customer_role_requires_customer_id(connection: Connection) -> None:
    """A brand-customer role grant without customer_id is rejected."""
    user_id = connection.execute(
        text(
            "INSERT INTO user_account (archetype, email, password_hash) "
            "VALUES ('brand_customer', :email, :hash) RETURNING user_id"
        ),
        {"email": f"bc-{uuid.uuid4()}@example.com", "hash": b"x"},
    ).scalar()
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO user_role_assignment "
                    "(user_id, account_archetype, role) "
                    "VALUES (:uid, 'brand_customer', 'analyst')"
                ),
                {"uid": user_id},
            )


def test_operator_role_rejects_customer_id(connection: Connection) -> None:
    """A non-brand-customer role grant carrying a customer_id is rejected."""
    user_id = connection.execute(
        text(
            "INSERT INTO user_account (archetype, email, password_hash) "
            "VALUES ('operator', :email, :hash) RETURNING user_id"
        ),
        {"email": f"op-{uuid.uuid4()}@example.com", "hash": b"x"},
    ).scalar()
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO user_role_assignment "
                    "(user_id, account_archetype, role, customer_id) "
                    "VALUES (:uid, 'operator', 'admin', :cid)"
                ),
                {"uid": user_id, "cid": str(uuid.uuid4())},
            )


def test_role_assignment_archetype_must_match_account(connection: Connection) -> None:
    """The composite FK rejects an account_archetype that lies about the user.

    This is what keeps the customer_id CHECK honest — the archetype carried on
    user_role_assignment cannot diverge from user_account.archetype.
    """
    user_id = connection.execute(
        text(
            "INSERT INTO user_account (archetype, email, password_hash) "
            "VALUES ('operator', :email, :hash) RETURNING user_id"
        ),
        {"email": f"op2-{uuid.uuid4()}@example.com", "hash": b"x"},
    ).scalar()
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO user_role_assignment "
                    "(user_id, account_archetype, role, customer_id) "
                    "VALUES (:uid, 'brand_customer', 'analyst', :cid)"
                ),
                {"uid": user_id, "cid": str(uuid.uuid4())},
            )


# --- Append-only enforcement ------------------------------------------------


def test_application_role_cannot_update_evidence(
    connection: Connection, app_role_connection: Connection
) -> None:
    """An UPDATE on evidence from the application role is refused by Postgres."""
    domain_id = _insert_domain(connection, "append.example.com")
    provenance_id = _insert_provenance(connection)
    evidence_id = _insert_evidence(
        connection,
        domain_id,
        provenance_id,
        signal_class="dns",
        source_url="",
        observed_at="2026-05-01T00:00:00Z",
        value='{"a": 1}',
    )
    connection.commit()
    try:
        with pytest.raises(DBAPIError) as excinfo:
            app_role_connection.execute(
                text("UPDATE evidence SET signal_class = 'tampered' WHERE evidence_id = :id"),
                {"id": evidence_id},
            )
        assert "permission denied" in str(excinfo.value).lower()
    finally:
        # Remove the committed fixture row using the privileged connection.
        connection.execute(
            text("DELETE FROM evidence WHERE evidence_id = :id"), {"id": evidence_id}
        )
        connection.execute(
            text("DELETE FROM provenance WHERE provenance_id = :id"),
            {"id": provenance_id},
        )
        connection.execute(
            text("DELETE FROM domain WHERE domain_id = :id"), {"id": domain_id}
        )
        connection.commit()


def test_application_role_cannot_delete_auth_audit_event(
    connection: Connection, app_role_connection: Connection
) -> None:
    """A DELETE on auth_audit_event from the application role is refused."""
    event_id = connection.execute(
        text(
            "INSERT INTO auth_audit_event (event_type) "
            "VALUES ('login_failure') RETURNING auth_audit_event_id"
        )
    ).scalar()
    connection.commit()
    try:
        with pytest.raises(DBAPIError) as excinfo:
            app_role_connection.execute(
                text("DELETE FROM auth_audit_event WHERE auth_audit_event_id = :id"),
                {"id": event_id},
            )
        assert "permission denied" in str(excinfo.value).lower()
    finally:
        connection.execute(
            text("DELETE FROM auth_audit_event WHERE auth_audit_event_id = :id"),
            {"id": event_id},
        )
        connection.commit()


def test_application_role_can_insert_and_select_evidence(
    connection: Connection, app_role_connection: Connection
) -> None:
    """The application role retains SELECT and INSERT on append-only tables."""
    domain_id = _insert_domain(connection, "insert.example.com")
    provenance_id = _insert_provenance(connection)
    connection.commit()
    try:
        evidence_id = app_role_connection.execute(
            text(
                "INSERT INTO evidence "
                "(domain_id, signal_class, source, method, observed_at, "
                " observed_value, provenance_id) "
                "VALUES (:d, 'dns', 'system', 'probe', now(), '{}'::jsonb, :p) "
                "RETURNING evidence_id"
            ),
            {"d": domain_id, "p": provenance_id},
        ).scalar()
        seen = app_role_connection.execute(
            text("SELECT evidence_id FROM evidence WHERE evidence_id = :id"),
            {"id": evidence_id},
        ).scalar()
        assert str(seen) == str(evidence_id)
        app_role_connection.rollback()
    finally:
        connection.execute(
            text("DELETE FROM provenance WHERE provenance_id = :id"),
            {"id": provenance_id},
        )
        connection.execute(
            text("DELETE FROM domain WHERE domain_id = :id"), {"id": domain_id}
        )
        connection.commit()


# --- evidence_current latest-per-key ----------------------------------------


def test_evidence_current_returns_latest_per_key(connection: Connection) -> None:
    """evidence_current exposes the most-recent evidence row per natural key.

    Three evidence rows for one (domain_id, signal_class, source_url) key are
    inserted out of observation order; the view must surface only the latest by
    observed_at. A second source_url for the same domain/signal produces a
    distinct view row, proving the source_url key extension works.
    """
    domain_id = _insert_domain(connection, "matview.example.com")
    provenance_id = _insert_provenance(connection)
    # Three observations for the same key, deliberately out of time order.
    _insert_evidence(
        connection,
        domain_id,
        provenance_id,
        signal_class="content",
        source_url="https://matview.example.com/a",
        observed_at="2026-05-02T00:00:00Z",
        value='{"v": "middle"}',
    )
    _insert_evidence(
        connection,
        domain_id,
        provenance_id,
        signal_class="content",
        source_url="https://matview.example.com/a",
        observed_at="2026-05-03T00:00:00Z",
        value='{"v": "latest"}',
    )
    _insert_evidence(
        connection,
        domain_id,
        provenance_id,
        signal_class="content",
        source_url="https://matview.example.com/a",
        observed_at="2026-05-01T00:00:00Z",
        value='{"v": "oldest"}',
    )
    # A second source_url for the same domain/signal — a distinct view row.
    _insert_evidence(
        connection,
        domain_id,
        provenance_id,
        signal_class="content",
        source_url="https://matview.example.com/b",
        observed_at="2026-05-04T00:00:00Z",
        value='{"v": "page-b"}',
    )
    connection.commit()
    try:
        # CONCURRENTLY needs its own transaction and cannot run inside the
        # test's wrapping transaction; AUTOCOMMIT isolates the refresh.
        with connection.engine.connect() as refresh_conn:
            refresh_conn.execution_options(isolation_level="AUTOCOMMIT").execute(
                text("REFRESH MATERIALIZED VIEW CONCURRENTLY evidence_current")
            )
        rows = connection.execute(
            text(
                "SELECT source_url, observed_value->>'v' AS v "
                "FROM evidence_current WHERE domain_id = :d "
                "ORDER BY source_url"
            ),
            {"d": domain_id},
        ).all()
        result = {source_url: v for source_url, v in rows}
        assert result == {
            "https://matview.example.com/a": "latest",
            "https://matview.example.com/b": "page-b",
        }
    finally:
        connection.execute(
            text("DELETE FROM evidence WHERE domain_id = :d"), {"d": domain_id}
        )
        connection.execute(
            text("DELETE FROM provenance WHERE provenance_id = :id"),
            {"id": provenance_id},
        )
        connection.execute(
            text("DELETE FROM domain WHERE domain_id = :id"), {"id": domain_id}
        )
        connection.commit()
        with connection.engine.connect() as refresh_conn:
            refresh_conn.execution_options(isolation_level="AUTOCOMMIT").execute(
                text("REFRESH MATERIALIZED VIEW CONCURRENTLY evidence_current")
            )


# --- CRUD across every table ------------------------------------------------


def test_crud_cycle_across_every_table(connection: Connection) -> None:
    """Exercise INSERT and SELECT on all fifteen tables in one referential graph.

    Mutable tables additionally get an UPDATE; this test runs as the migration
    owner (not the application role), so append-only tables are also INSERTed
    but never UPDATEd — the append-only grant discipline is asserted separately.
    """
    # --- domain-side -------------------------------------------------------
    domain_id = _insert_domain(connection, "crud.example.com")
    connection.execute(
        text("UPDATE domain SET current_status = 'green' WHERE domain_id = :id"),
        {"id": domain_id},
    )

    pool_id = connection.execute(
        text(
            "INSERT INTO pool (canonical_handle) VALUES (:h) RETURNING pool_id"
        ),
        {"h": f"crud-pool-{uuid.uuid4()}"},
    ).scalar()
    connection.execute(
        text("UPDATE pool SET attestation_flag = true WHERE pool_id = :id"),
        {"id": pool_id},
    )

    connection.execute(
        text(
            "INSERT INTO domain_pool_membership "
            "(domain_id, pool_id, valid_from, confidence, attestation_status) "
            "VALUES (:d, :p, now(), 0.75, 'inferred')"
        ),
        {"d": domain_id, "p": pool_id},
    )

    provenance_id = _insert_provenance(connection)
    evidence_id = _insert_evidence(
        connection,
        domain_id,
        provenance_id,
        signal_class="dns",
        source_url="",
        observed_at="2026-05-01T00:00:00Z",
        value='{"resolves": true}',
    )

    # --- authentication-side ----------------------------------------------
    operator_id = connection.execute(
        text(
            "INSERT INTO user_account (archetype, email, password_hash) "
            "VALUES ('operator', :e, :h) RETURNING user_id"
        ),
        {"e": f"op-{uuid.uuid4()}@example.com", "h": b"hash"},
    ).scalar()

    attestation_id = connection.execute(
        text(
            "INSERT INTO attestation "
            "(domain_id, pool_id, operator_user_id, signature, signed_at, "
            " verification_status) "
            "VALUES (:d, :p, :o, :sig, now(), 'pending') RETURNING attestation_id"
        ),
        {"d": domain_id, "p": pool_id, "o": operator_id, "sig": b"sig"},
    ).scalar()

    connection.execute(
        text(
            "INSERT INTO score "
            "(domain_id, composite_score, verdict, category_scores, "
            " algorithm_version, computed_at) "
            "VALUES (:d, 72.5, 'grey', '{}'::jsonb, 'v0', now())"
        ),
        {"d": domain_id},
    )
    connection.execute(
        text("UPDATE score SET composite_score = 80.0 WHERE domain_id = :d"),
        {"d": domain_id},
    )

    connection.execute(
        text(
            "INSERT INTO score_history "
            "(domain_id, composite_score, verdict, category_scores, "
            " algorithm_version, computed_at) "
            "VALUES (:d, 72.5, 'grey', '{}'::jsonb, 'v0', now())"
        ),
        {"d": domain_id},
    )

    session_id = connection.execute(
        text(
            "INSERT INTO user_session (user_id) VALUES (:u) RETURNING session_id"
        ),
        {"u": operator_id},
    ).scalar()
    connection.execute(
        text("UPDATE user_session SET revoked_at = now() WHERE session_id = :s"),
        {"s": session_id},
    )

    connection.execute(
        text(
            "INSERT INTO user_role_assignment "
            "(user_id, account_archetype, role) "
            "VALUES (:u, 'operator', 'operator')"
        ),
        {"u": operator_id},
    )

    connection.execute(
        text(
            "INSERT INTO auth_audit_event (user_id, event_type) "
            "VALUES (:u, 'login_success')"
        ),
        {"u": operator_id},
    )

    connection.execute(
        text(
            "INSERT INTO operator_account_extension (user_id, display_name) "
            "VALUES (:u, 'Test Operator')"
        ),
        {"u": operator_id},
    )
    connection.execute(
        text(
            "UPDATE operator_account_extension SET verification_tier = 'tier_1' "
            "WHERE user_id = :u"
        ),
        {"u": operator_id},
    )

    brand_user_id = connection.execute(
        text(
            "INSERT INTO user_account (archetype, email, password_hash) "
            "VALUES ('brand_customer', :e, :h) RETURNING user_id"
        ),
        {"e": f"bc-{uuid.uuid4()}@example.com", "h": b"hash"},
    ).scalar()
    connection.execute(
        text(
            "INSERT INTO brand_customer_account_extension "
            "(user_id, customer_id, display_name) "
            "VALUES (:u, :c, 'Test Brand')"
        ),
        {"u": brand_user_id, "c": str(uuid.uuid4())},
    )

    foundation_user_id = connection.execute(
        text(
            "INSERT INTO user_account (archetype, email, password_hash) "
            "VALUES ('foundation_internal', :e, :h) RETURNING user_id"
        ),
        {"e": f"fi-{uuid.uuid4()}@example.com", "h": b"hash"},
    ).scalar()
    connection.execute(
        text(
            "INSERT INTO foundation_user_account_extension "
            "(user_id, governance_role, display_name) "
            "VALUES (:u, 'maintainer', 'Test Maintainer')"
        ),
        {"u": foundation_user_id},
    )

    # --- read-back assertions ---------------------------------------------
    assert connection.execute(
        text("SELECT current_status FROM domain WHERE domain_id = :d"),
        {"d": domain_id},
    ).scalar() == "green"
    assert connection.execute(
        text("SELECT composite_score FROM score WHERE domain_id = :d"),
        {"d": domain_id},
    ).scalar() == 80.0
    assert connection.execute(
        text("SELECT verification_status FROM attestation WHERE attestation_id = :a"),
        {"a": attestation_id},
    ).scalar() == "pending"
    assert connection.execute(
        text("SELECT signal_class FROM evidence WHERE evidence_id = :e"),
        {"e": evidence_id},
    ).scalar() == "dns"
    assert connection.execute(
        text("SELECT count(*) FROM auth_audit_event WHERE user_id = :u"),
        {"u": operator_id},
    ).scalar() == 1
