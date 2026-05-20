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

"""create canonical data model

Revision ID: 0001
Revises:
Create Date: 2026-05-20

The Stage 0 canonical store — Stage 0 PRD §7a. Creates the fifteen tables (eight
domain-side, seven authentication-side), their enum types, indexes and CHECK
constraints; the ``evidence_current`` materialised view; and the append-only
application database role.

Migration discipline: forward-only. Rollbacks are new forward migrations, not
``downgrade`` runs against production (Stage 0 PRD §7a). ``downgrade()`` is
therefore a deliberate no-op.

This migration is hand-written rather than left as raw autogenerate output for
three reasons that autogenerate cannot express: the shared ``domain_status``
enum must be created exactly once and then reused (autogenerate would emit it
twice and the second CREATE TYPE would fail); the ``evidence_current``
materialised view and its unique index are not modelled by SQLAlchemy
``Table`` objects; and the append-only application role and its GRANT discipline
are operational DDL outside the ORM metadata entirely.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The login-less application role created by this migration. The application
# connects as a member of this role; it carries SELECT/INSERT on every table
# and SELECT/UPDATE/DELETE only on the mutable tables — see _grant_application
# below. The name is fixed so later auth-scaffolding issues can target it.
APP_ROLE = "trustlist_app"

# Tables the application role may only SELECT and INSERT — never UPDATE or
# DELETE. Mirrors trustlist_data_model.APPEND_ONLY_TABLES; duplicated here as a
# literal so the migration is self-contained and never drifts under a model
# refactor (the integration tests assert the two lists agree).
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "evidence",
    "provenance",
    "score_history",
    "attestation",
    "auth_audit_event",
)

ALL_TABLES: tuple[str, ...] = (
    "domain",
    "pool",
    "domain_pool_membership",
    "attestation",
    "evidence",
    "provenance",
    "score",
    "score_history",
    "user_account",
    "user_session",
    "user_role_assignment",
    "auth_audit_event",
    "operator_account_extension",
    "brand_customer_account_extension",
    "foundation_user_account_extension",
)

# Enum type definitions, created once at the top of upgrade(). Each enum is then
# referenced by name with create_type=False so a type shared across two columns
# (domain_status, evidence_source, user_archetype, score_verdict) is created
# exactly once.
_ENUMS: dict[str, tuple[str, ...]] = {
    "domain_status": ("green", "grey", "red", "under_review", "dormant"),
    "attestation_membership_status": ("inferred", "operator_attested", "contested"),
    "evidence_source": ("system", "human", "contributor", "cti_partner"),
    "attestation_verification_status": ("pending", "verified", "rejected", "revoked"),
    "score_verdict": ("green", "grey", "red", "under_review"),
    "user_archetype": ("operator", "brand_customer", "foundation_internal"),
    "auth_audit_event_type": (
        "login_success",
        "login_failure",
        "mfa_challenge_issued",
        "mfa_challenge_success",
        "mfa_challenge_failure",
        "password_change",
        "role_grant",
        "role_revoke",
        "account_disable",
        "session_revoke",
    ),
    "operator_verification_tier": ("tier_0", "tier_1", "tier_2"),
    "foundation_governance_role": (
        "trust_council_voting",
        "trust_council_observer",
        "adjudicator",
        "ops_staff",
        "maintainer",
    ),
}


def _enum(name: str) -> postgresql.ENUM:
    """A reference to an already-created enum type (no CREATE TYPE emitted)."""
    return postgresql.ENUM(*_ENUMS[name], name=name, create_type=False)


def _create_enum_types() -> None:
    """Create every enum type once, before any table references it."""
    bind = op.get_bind()
    for name, values in _ENUMS.items():
        postgresql.ENUM(*values, name=name).create(bind, checkfirst=True)


def _create_tables() -> None:
    """Create the fifteen canonical tables, their indexes and CHECK constraints."""
    op.create_table(
        "domain",
        sa.Column(
            "domain_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("normalised_url", sa.String(length=255), nullable=False),
        sa.Column("current_status", _enum("domain_status"), nullable=False),
        sa.Column("current_score", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column("score_version", sa.Text(), nullable=True),
        sa.Column("last_scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "normalised_url = lower(normalised_url) "
            "AND normalised_url !~ '^[a-z][a-z0-9+.-]*://' "
            "AND normalised_url NOT LIKE 'www.%' "
            "AND normalised_url NOT LIKE '%?%' "
            "AND normalised_url NOT LIKE '%#%' "
            "AND normalised_url !~ '\\s' "
            "AND length(normalised_url) > 0",
            name="ck_domain_normalised_url_is_normalised",
        ),
        sa.PrimaryKeyConstraint("domain_id", name="pk_domain"),
        sa.UniqueConstraint("normalised_url", name="uq_domain_normalised_url"),
    )
    op.create_index("ix_domain_current_status", "domain", ["current_status"])
    op.create_index("ix_domain_last_scored_at", "domain", ["last_scored_at"])

    op.create_table(
        "pool",
        sa.Column(
            "pool_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("canonical_handle", sa.String(length=128), nullable=False),
        sa.Column("derived_status", _enum("domain_status"), nullable=True),
        sa.Column("derived_score", sa.Numeric(precision=6, scale=3), nullable=True),
        sa.Column(
            "attestation_flag",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("pool_id", name="pk_pool"),
        sa.UniqueConstraint("canonical_handle", name="uq_pool_canonical_handle"),
    )

    op.create_table(
        "user_account",
        sa.Column(
            "user_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("archetype", _enum("user_archetype"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("password_hash", sa.LargeBinary(), nullable=False),
        sa.Column("totp_secret", sa.LargeBinary(), nullable=True),
        sa.Column("totp_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_account"),
        sa.UniqueConstraint("archetype", "email", name="uq_user_account_archetype_email"),
        sa.UniqueConstraint(
            "user_id", "archetype", name="uq_user_account_user_id_archetype"
        ),
    )

    op.create_table(
        "domain_pool_membership",
        sa.Column("domain_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("pool_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column(
            "attestation_status", _enum("attestation_membership_status"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_domain_pool_membership_confidence_in_unit_interval",
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_domain_pool_membership_domain_id_domain",
        ),
        sa.ForeignKeyConstraint(
            ["pool_id"], ["pool.pool_id"], name="fk_domain_pool_membership_pool_id_pool"
        ),
        sa.PrimaryKeyConstraint(
            "domain_id", "pool_id", "valid_from", name="pk_domain_pool_membership"
        ),
    )
    op.create_index(
        "ix_domain_pool_membership_domain_id_valid_until",
        "domain_pool_membership",
        ["domain_id", "valid_until"],
    )

    op.create_table(
        "provenance",
        sa.Column(
            "provenance_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("source", _enum("evidence_source"), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contributor_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("contributor_identity", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["contributor_id"],
            ["user_account.user_id"],
            name="fk_provenance_contributor_id_user_account",
        ),
        sa.PrimaryKeyConstraint("provenance_id", name="pk_provenance"),
    )

    op.create_table(
        "evidence",
        sa.Column(
            "evidence_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("domain_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("signal_class", sa.Text(), nullable=False),
        sa.Column("source", _enum("evidence_source"), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column(
            "source_url", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("contributor_identity", sa.Text(), nullable=True),
        sa.Column(
            "observed_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("provenance_id", sa.UUID(as_uuid=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["domain.domain_id"], name="fk_evidence_domain_id_domain"
        ),
        sa.ForeignKeyConstraint(
            ["provenance_id"],
            ["provenance.provenance_id"],
            name="fk_evidence_provenance_id_provenance",
        ),
        sa.PrimaryKeyConstraint("evidence_id", name="pk_evidence"),
    )
    op.create_index(
        "ix_evidence_domain_id_signal_class_observed_at",
        "evidence",
        ["domain_id", "signal_class", sa.literal_column("observed_at DESC")],
    )

    op.create_table(
        "attestation",
        sa.Column(
            "attestation_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("domain_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("pool_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("operator_user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("signature", sa.LargeBinary(), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "verification_status",
            _enum("attestation_verification_status"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["domain.domain_id"], name="fk_attestation_domain_id_domain"
        ),
        sa.ForeignKeyConstraint(
            ["pool_id"], ["pool.pool_id"], name="fk_attestation_pool_id_pool"
        ),
        sa.ForeignKeyConstraint(
            ["operator_user_id"],
            ["user_account.user_id"],
            name="fk_attestation_operator_user_id_user_account",
        ),
        sa.PrimaryKeyConstraint("attestation_id", name="pk_attestation"),
    )

    op.create_table(
        "score",
        sa.Column("domain_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("composite_score", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("verdict", _enum("score_verdict"), nullable=False),
        sa.Column(
            "category_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("rationale_summary", sa.Text(), nullable=True),
        sa.Column(
            "severity_flag",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id"], ["domain.domain_id"], name="fk_score_domain_id_domain"
        ),
        sa.PrimaryKeyConstraint("domain_id", name="pk_score"),
    )

    op.create_table(
        "score_history",
        sa.Column(
            "score_history_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("domain_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("composite_score", sa.Numeric(precision=6, scale=3), nullable=False),
        sa.Column("verdict", _enum("score_verdict"), nullable=False),
        sa.Column(
            "category_scores", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("rationale_summary", sa.Text(), nullable=True),
        sa.Column(
            "severity_flag",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("algorithm_version", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["domain_id"],
            ["domain.domain_id"],
            name="fk_score_history_domain_id_domain",
        ),
        sa.PrimaryKeyConstraint("score_history_id", name="pk_score_history"),
    )
    op.create_index(
        "ix_score_history_domain_id_computed_at",
        "score_history",
        ["domain_id", sa.literal_column("computed_at DESC")],
    )

    op.create_table(
        "user_session",
        sa.Column(
            "session_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_fingerprint", sa.Text(), nullable=True),
        sa.Column("ip_address_observed", postgresql.INET(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.user_id"],
            name="fk_user_session_user_id_user_account",
        ),
        sa.PrimaryKeyConstraint("session_id", name="pk_user_session"),
    )
    op.create_index(
        "ix_user_session_user_id_revoked_at",
        "user_session",
        ["user_id", "revoked_at"],
    )

    op.create_table(
        "user_role_assignment",
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("account_archetype", _enum("user_archetype"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("granted_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_id", sa.UUID(as_uuid=False), nullable=True),
        sa.CheckConstraint(
            "(account_archetype = 'brand_customer' AND customer_id IS NOT NULL) "
            "OR (account_archetype <> 'brand_customer' AND customer_id IS NULL)",
            name="ck_user_role_assignment_customer_id_required_for_brand_customer",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_user_id"],
            ["user_account.user_id"],
            name="fk_user_role_assignment_granted_by_user_id_user_account",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "account_archetype"],
            ["user_account.user_id", "user_account.archetype"],
            name="fk_user_role_assignment_user_id_user_account",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", "role", "granted_at", name="pk_user_role_assignment"
        ),
    )

    op.create_table(
        "auth_audit_event",
        sa.Column(
            "auth_audit_event_id",
            sa.UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("event_type", _enum("auth_audit_event_type"), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("ip_address_observed", postgresql.INET(), nullable=True),
        sa.Column("user_agent_observed", sa.Text(), nullable=True),
        sa.Column(
            "event_details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.user_id"],
            name="fk_auth_audit_event_user_id_user_account",
        ),
        sa.PrimaryKeyConstraint("auth_audit_event_id", name="pk_auth_audit_event"),
    )
    op.create_index(
        "ix_auth_audit_event_user_id_occurred_at",
        "auth_audit_event",
        ["user_id", sa.literal_column("occurred_at DESC")],
    )

    op.create_table(
        "operator_account_extension",
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "verification_tier",
            _enum("operator_verification_tier"),
            server_default=sa.text("'tier_0'"),
            nullable=False,
        ),
        sa.Column(
            "tier_1_verifications",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "tier_2_verification",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.user_id"],
            name="fk_operator_account_extension_user_id_user_account",
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_operator_account_extension"),
    )

    op.create_table(
        "brand_customer_account_extension",
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("customer_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "customer_id IS NOT NULL",
            name="ck_brand_customer_account_extension_customer_id_not_null",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.user_id"],
            name="fk_brand_customer_account_extension_user_id_user_account",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", name="pk_brand_customer_account_extension"
        ),
    )

    op.create_table(
        "foundation_user_account_extension",
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "governance_role", _enum("foundation_governance_role"), nullable=False
        ),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.user_id"],
            name="fk_foundation_user_account_extension_user_id_user_account",
        ),
        sa.PrimaryKeyConstraint(
            "user_id", name="pk_foundation_user_account_extension"
        ),
    )


def _create_evidence_current_view() -> None:
    """Create the ``evidence_current`` materialised view.

    The view exposes the single most-recent evidence row per
    ``(domain_id, signal_class, source_url)`` — the natural key extended by the
    PRD §10 content-scrape addendum. DISTINCT ON, ordered by ``observed_at``
    descending then ``recorded_at`` descending, picks the latest observation and
    breaks ties deterministically by recording time and finally by evidence_id.

    Refresh strategy: REFRESH MATERIALIZED VIEW CONCURRENTLY. The view carries a
    UNIQUE index on the natural key, which CONCURRENTLY requires; concurrent
    refresh keeps the scoring engine's Stage 2 read path unblocked while the
    view rebuilds. See data-model/README.md for the rationale and the PRD §10
    question 2 resolution.
    """
    op.execute(
        """
        CREATE MATERIALIZED VIEW evidence_current AS
        SELECT DISTINCT ON (domain_id, signal_class, source_url)
            evidence_id,
            domain_id,
            signal_class,
            source,
            method,
            source_url,
            observed_at,
            recorded_at,
            contributor_identity,
            observed_value,
            provenance_id
        FROM evidence
        ORDER BY domain_id, signal_class, source_url,
                 observed_at DESC, recorded_at DESC, evidence_id DESC
        WITH NO DATA
        """
    )
    # A UNIQUE index on the natural key is mandatory for REFRESH ... CONCURRENTLY.
    op.execute(
        """
        CREATE UNIQUE INDEX ux_evidence_current_natural_key
        ON evidence_current (domain_id, signal_class, source_url)
        """
    )
    # Populate the view once so first reads do not hit an unpopulated relation.
    # The first refresh of a WITH NO DATA view cannot use CONCURRENTLY.
    op.execute("REFRESH MATERIALIZED VIEW evidence_current")


def _create_application_role() -> None:
    """Create the append-only application role and grant its privileges.

    Append-only discipline (Stage 0 PRD §7a) is enforced here, not in
    application code: the role receives SELECT/INSERT on every table, plus
    UPDATE/DELETE on the mutable tables only. The append-only tables — evidence,
    provenance, score_history, attestation, auth_audit_event — receive no
    UPDATE or DELETE grant, so an UPDATE or DELETE from the application role is
    refused by Postgres with a permission error.

    The role is created NOLOGIN: it is a privilege bundle that real login roles
    (provisioned per environment from the secrets store, §7g) are granted
    membership of. CREATE ROLE IF NOT EXISTS is not available, so a guarded
    DO block is used for idempotency.
    """
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}'
            ) THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END
        $$
        """
    )
    # Baseline: SELECT + INSERT on every table.
    for table in ALL_TABLES:
        op.execute(f"GRANT SELECT, INSERT ON TABLE {table} TO {APP_ROLE}")
    # Mutable tables additionally receive UPDATE + DELETE.
    mutable = [t for t in ALL_TABLES if t not in APPEND_ONLY_TABLES]
    for table in mutable:
        op.execute(f"GRANT UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}")
    # The materialised view is read-only for the application role; the scoring
    # engine reads it and a privileged maintenance role refreshes it.
    op.execute(f"GRANT SELECT ON TABLE evidence_current TO {APP_ROLE}")
    # USAGE on sequences is not required: every surrogate key uses
    # gen_random_uuid(), not a sequence. Granting it would be a no-op.


def upgrade() -> None:
    """Apply the schema change — Stage 0 PRD §7a canonical store."""
    _create_enum_types()
    _create_tables()
    _create_evidence_current_view()
    _create_application_role()


def downgrade() -> None:
    """Present for tooling completeness; the project does not run downgrades.

    Migration discipline is forward-only (Stage 0 PRD §7a): a rollback is a new
    forward migration, never a downgrade against production.
    """
    pass
