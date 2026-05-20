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

"""SQLAlchemy declarative models for the TrustList canonical store.

These fifteen models implement Stage 0 PRD §7a — eight domain-side tables and
seven authentication-side tables. The ``Base.metadata`` object exported here is
the single source of truth that ``migrations/env.py`` wires into Alembic's
``target_metadata``.

Notes on scope.

* Append-only discipline is enforced operationally — the migration grants the
  application role SELECT/INSERT only on the append-only tables and withholds
  UPDATE/DELETE. The ORM models do not (and cannot) express that grant.
* The Foundation / commercial-entity database split (§7a, §7c) is a deployment
  concern: the brand-customer ``user_account`` rows and
  ``brand_customer_account_extension`` live in the commercial-entity database,
  the rest in the Foundation database. A single ``metadata`` describes both;
  the physical placement is enforced at the network and IAM layer.
* ``customer`` (referenced by ``brand_customer_account_extension.customer_id``)
  is a Stage 5 commercial-entity table and is deliberately *not* modelled here;
  no database-level foreign key is emitted for it.
"""

from __future__ import annotations

import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# A consistent naming convention keeps Alembic autogenerate output stable and
# gives every constraint a predictable, greppable name.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base carrying the shared, naming-conventioned metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


metadata = Base.metadata

# The tables on which the application role holds SELECT/INSERT but no
# UPDATE/DELETE (Stage 0 PRD §7a). The migration reads this list to issue the
# grants; the integration tests read it to assert the discipline holds.
APPEND_ONLY_TABLES: tuple[str, ...] = (
    "evidence",
    "provenance",
    "score_history",
    "attestation",
    "auth_audit_event",
)

# --- PostgreSQL enum types --------------------------------------------------
#
# Enum types are created once and reused. ``create_type=False`` on every reuse
# would be required if a type backed more than one column with autogenerate;
# here each enum backs exactly one column, so the defaults are correct.

domain_status_enum = Enum(
    "green",
    "grey",
    "red",
    "under_review",
    "dormant",
    name="domain_status",
)
"""Lifecycle status of a domain (§7a `domain.current_status`)."""

attestation_membership_status_enum = Enum(
    "inferred",
    "operator_attested",
    "contested",
    name="attestation_membership_status",
)
"""Attestation state of a domain-pool membership (§7a `domain_pool_membership`)."""

evidence_source_enum = Enum(
    "system",
    "human",
    "contributor",
    "cti_partner",
    name="evidence_source",
)
"""Origin class of an evidence observation (§7a `evidence.source`)."""

attestation_verification_status_enum = Enum(
    "pending",
    "verified",
    "rejected",
    "revoked",
    name="attestation_verification_status",
)
"""Verification state of an operator attestation (§7a `attestation`)."""

score_verdict_enum = Enum(
    "green",
    "grey",
    "red",
    "under_review",
    name="score_verdict",
)
"""Verdict band of a computed score (§7a `score.verdict`)."""

user_archetype_enum = Enum(
    "operator",
    "brand_customer",
    "foundation_internal",
    name="user_archetype",
)
"""The three user archetypes (§7a `user_account.archetype`)."""

auth_audit_event_type_enum = Enum(
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
    name="auth_audit_event_type",
)
"""Audit-event taxonomy (§7a `auth_audit_event.event_type`)."""

operator_verification_tier_enum = Enum(
    "tier_0",
    "tier_1",
    "tier_2",
    name="operator_verification_tier",
)
"""Operator verification tier (§7a `operator_account_extension`)."""

foundation_governance_role_enum = Enum(
    "trust_council_voting",
    "trust_council_observer",
    "adjudicator",
    "ops_staff",
    "maintainer",
    name="foundation_governance_role",
)
"""Foundation-internal governance role (§7a `foundation_user_account_extension`)."""


# --- Shared column helpers --------------------------------------------------


def _uuid_pk() -> Mapped[str]:
    """A UUID primary key defaulting to a server-side ``gen_random_uuid()``."""
    return mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _created_at() -> Mapped[datetime.datetime]:
    """A non-null creation timestamp defaulting to ``now()``."""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


def _updated_at() -> Mapped[datetime.datetime]:
    """A non-null mutation timestamp defaulting to ``now()``.

    Used only on the mutable tables (``domain``, ``pool``, ``user_account``).
    The append-only tables carry ``recorded_at`` / ``occurred_at`` instead and
    are never updated.
    """
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


# --- Domain-side tables -----------------------------------------------------


class Domain(Base):
    """A single normalised domain — the unit of trust scoring (§7a `domain`).

    URL normalisation per ADR-0002: protocol stripped, leading ``www.``
    stripped, query strings and fragments dropped, lowercased; subdomains are
    separate records. The ``CHECK`` constraint fails fast on un-normalised
    input as a belt-and-braces complement to application-layer enforcement.
    """

    __tablename__ = "domain"

    domain_id: Mapped[str] = _uuid_pk()
    normalised_url: Mapped[str] = mapped_column(String(255), nullable=False)
    current_status: Mapped[str] = mapped_column(domain_status_enum, nullable=False)
    current_score: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    score_version: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_scored_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("normalised_url", name="uq_domain_normalised_url"),
        # ADR-0002 normalisation, enforced at the database boundary: no scheme,
        # no leading "www.", no query string or fragment, lowercase only, and
        # no whitespace.
        CheckConstraint(
            "normalised_url = lower(normalised_url) "
            "AND normalised_url !~ '^[a-z][a-z0-9+.-]*://' "
            "AND normalised_url NOT LIKE 'www.%' "
            "AND normalised_url NOT LIKE '%?%' "
            "AND normalised_url NOT LIKE '%#%' "
            "AND normalised_url !~ '\\s' "
            "AND length(normalised_url) > 0",
            name="normalised_url_is_normalised",
        ),
        Index("ix_domain_current_status", "current_status"),
        Index("ix_domain_last_scored_at", "last_scored_at"),
    )


class Pool(Base):
    """A pool — a coherent group of domains under common control (§7a `pool`).

    ``derived_status`` and ``derived_score`` are computed by the Trust
    Council-governed composite formula (ADR-0004); they are never written
    directly by collectors.
    """

    __tablename__ = "pool"

    pool_id: Mapped[str] = _uuid_pk()
    canonical_handle: Mapped[str] = mapped_column(String(128), nullable=False)
    derived_status: Mapped[str | None] = mapped_column(domain_status_enum, nullable=True)
    derived_score: Mapped[float | None] = mapped_column(Numeric(6, 3), nullable=True)
    attestation_flag: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()

    __table_args__ = (
        UniqueConstraint("canonical_handle", name="uq_pool_canonical_handle"),
    )


class DomainPoolMembership(Base):
    """Time-bounded membership of a domain in a pool (§7a `domain_pool_membership`).

    A domain may belong to several pools simultaneously; ``valid_until`` being
    NULL marks a currently-active membership.
    """

    __tablename__ = "domain_pool_membership"

    domain_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("domain.domain_id"),
        primary_key=True,
    )
    pool_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pool.pool_id"),
        primary_key=True,
    )
    valid_from: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    valid_until: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confidence: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    attestation_status: Mapped[str] = mapped_column(
        attestation_membership_status_enum, nullable=False
    )
    created_at: Mapped[datetime.datetime] = _created_at()

    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="confidence_in_unit_interval",
        ),
        # "Current pools for this domain" lookups filter on (domain_id,
        # valid_until) — §7a index list.
        Index("ix_domain_pool_membership_domain_id_valid_until", "domain_id", "valid_until"),
    )


class Provenance(Base):
    """The origin record every evidence row references (§7a `provenance`).

    Append-only: provenance is written once and never edited. ``contributor_id``
    optionally references a ``user_account`` row; ``contributor_identity`` is a
    free-text fallback for non-account contributors (e.g. CTI partners).
    """

    __tablename__ = "provenance"

    provenance_id: Mapped[str] = _uuid_pk()
    source: Mapped[str] = mapped_column(evidence_source_enum, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contributor_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        nullable=True,
    )
    contributor_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = _created_at()


class Evidence(Base):
    """An immutable observation about a domain (§7a `evidence`).

    Append-only: old evidence is never overwritten. A superseding observation is
    a fresh INSERT; the ``evidence_current`` materialised view exposes the
    latest row per ``(domain_id, signal_class, source_url)``.

    ``source_url`` is part of the natural key per the PRD §10 content-scrape
    addendum, which extended ``evidence_current`` keying to
    ``(domain_id, signal_class, source_url)`` for per-source-URL granularity.
    """

    __tablename__ = "evidence"

    evidence_id: Mapped[str] = _uuid_pk()
    domain_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("domain.domain_id"),
        nullable=False,
    )
    signal_class: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(evidence_source_enum, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    # The specific URL the observation was drawn from. Defaults to the empty
    # string rather than NULL so it can sit in the materialised-view natural
    # key without NULL-equality surprises; collectors with no per-URL notion
    # leave it empty.
    source_url: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    observed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    contributor_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    provenance_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("provenance.provenance_id"),
        nullable=False,
    )

    __table_args__ = (
        # Backs the evidence_current materialised view and current-feature
        # reads — §7a index list, observed_at descending.
        Index(
            "ix_evidence_domain_id_signal_class_observed_at",
            "domain_id",
            "signal_class",
            text("observed_at DESC"),
        ),
    )


class Attestation(Base):
    """A cryptographically signed operator attestation (§7a `attestation`).

    Append-only: an attestation is a point-in-time signed statement; revising it
    means a fresh INSERT with a new ``verification_status``.
    """

    __tablename__ = "attestation"

    attestation_id: Mapped[str] = _uuid_pk()
    domain_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("domain.domain_id"),
        nullable=False,
    )
    pool_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("pool.pool_id"),
        nullable=True,
    )
    operator_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        nullable=False,
    )
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    signed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    verification_status: Mapped[str] = mapped_column(
        attestation_verification_status_enum, nullable=False
    )
    created_at: Mapped[datetime.datetime] = _created_at()


class Score(Base):
    """The current composite score for a domain (§7a `score`).

    Exactly one row per domain; mutable — the scoring engine overwrites it on
    each re-score. The immutable history lives in ``score_history``. First
    write happens at Stage 2.
    """

    __tablename__ = "score"

    domain_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("domain.domain_id"),
        primary_key=True,
    )
    composite_score: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    verdict: Mapped[str] = mapped_column(score_verdict_enum, nullable=False)
    category_scores: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity_flag: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ScoreHistory(Base):
    """The append-only record of every score the engine has produced (§7a).

    Same shape as ``score`` plus a surrogate ``score_history_id``. Pool score
    history is implicit in member-domain histories and is not stored. First
    write at Stage 2.
    """

    __tablename__ = "score_history"

    score_history_id: Mapped[str] = _uuid_pk()
    domain_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("domain.domain_id"),
        nullable=False,
    )
    composite_score: Mapped[float] = mapped_column(Numeric(6, 3), nullable=False)
    verdict: Mapped[str] = mapped_column(score_verdict_enum, nullable=False)
    category_scores: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    rationale_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity_flag: Mapped[bool] = mapped_column(
        nullable=False, server_default=text("false")
    )
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        # History surfaces page by most-recent-first — §7a index list.
        Index(
            "ix_score_history_domain_id_computed_at",
            "domain_id",
            text("computed_at DESC"),
        ),
    )


# --- Authentication-side tables ---------------------------------------------


class UserAccount(Base):
    """The archetype-agnostic identity base table (§7a `user_account`).

    Email is unique *within an archetype*, not globally — the same address may
    register as both an operator and a brand customer. Operator and
    Foundation-internal rows live in the Foundation database; brand-customer
    rows live in the commercial-entity database (§7c).
    """

    __tablename__ = "user_account"

    user_id: Mapped[str] = _uuid_pk()
    archetype: Mapped[str] = mapped_column(user_archetype_enum, nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_verified_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    password_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    totp_secret: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_enrolled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime.datetime] = _created_at()
    updated_at: Mapped[datetime.datetime] = _updated_at()
    disabled_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # Composite unique — archetype-scoped email lookup (§7a index list).
        UniqueConstraint("archetype", "email", name="uq_user_account_archetype_email"),
        # Unique over (user_id, archetype) so user_role_assignment can carry a
        # composite foreign key onto it — see UserRoleAssignment. user_id alone
        # is already unique (it is the primary key); this pair is what the
        # archetype-pinning FK target requires.
        UniqueConstraint("user_id", "archetype", name="uq_user_account_user_id_archetype"),
    )


class UserSession(Base):
    """An authenticated session (§7a `user_session`).

    Sessions expire on a TTL and are revoked on logout, role change or password
    change; ``revoked_at`` being NULL marks an active session.
    """

    __tablename__ = "user_session"

    session_id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        nullable=False,
    )
    created_at: Mapped[datetime.datetime] = _created_at()
    last_seen_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    device_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address_observed: Mapped[str | None] = mapped_column(INET, nullable=True)

    __table_args__ = (
        # Active-session queries filter on (user_id, revoked_at) — §7a index.
        Index("ix_user_session_user_id_revoked_at", "user_id", "revoked_at"),
    )


class UserRoleAssignment(Base):
    """An RBAC role grant for a user (§7a `user_role_assignment`).

    ``customer_id`` scopes brand-customer-side grants to a single customer. The
    ``CHECK`` constraint enforces that ``customer_id`` is non-null for, and
    only for, brand-customer assignments — see ``Migration discipline`` in
    ``data-model/README.md``. Because the constraint must consult the owning
    account's archetype, the archetype is denormalised onto this table as
    ``account_archetype``: a row-local ``CHECK`` cannot reach into another
    table, so the discriminator is copied here and kept honest by a foreign key
    over ``(user_id, account_archetype)``.
    """

    __tablename__ = "user_role_assignment"

    # Composite primary key (user_id, role, granted_at): a user may hold a role
    # only once at a given instant, and re-grants after a revoke produce a fresh
    # row with a later granted_at.
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    account_archetype: Mapped[str] = mapped_column(user_archetype_enum, nullable=False)
    role: Mapped[str] = mapped_column(Text, primary_key=True)
    granted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        primary_key=True,
        server_default=func.now(),
    )
    granted_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        nullable=True,
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    customer_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)

    __table_args__ = (
        # The composite foreign key pins account_archetype to the owning
        # account's real archetype. Without it, account_archetype could be set
        # to any value and the CHECK below would be toothless; with it, the
        # discriminator is guaranteed to mirror user_account.archetype.
        ForeignKeyConstraint(
            ["user_id", "account_archetype"],
            ["user_account.user_id", "user_account.archetype"],
            name="fk_user_role_assignment_user_id_user_account",
        ),
        # Belt-and-braces per §7a: brand-customer grants must carry a
        # customer_id; non-brand-customer grants must not. This is the
        # "CHECK that joins through user_account.archetype" of the acceptance
        # criteria — a row-local CHECK cannot literally join, so the archetype
        # is carried on this row and kept honest by the composite FK above.
        CheckConstraint(
            "(account_archetype = 'brand_customer' AND customer_id IS NOT NULL) "
            "OR (account_archetype <> 'brand_customer' AND customer_id IS NULL)",
            name="customer_id_required_for_brand_customer",
        ),
    )


class AuthAuditEvent(Base):
    """An append-only authentication-audit record (§7a `auth_audit_event`).

    ``user_id`` is nullable for events that occur before identification (e.g. a
    login failure on an unknown address). Every row is mirrored to the
    ``auth.audit`` event-bus topic by the §7e auth library.
    """

    __tablename__ = "auth_audit_event"

    auth_audit_event_id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(auth_audit_event_type_enum, nullable=False)
    occurred_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ip_address_observed: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent_observed: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    __table_args__ = (
        # Per-user audit-trail surfaces page by most-recent-first — §7a index.
        Index(
            "ix_auth_audit_event_user_id_occurred_at",
            "user_id",
            text("occurred_at DESC"),
        ),
    )


class OperatorAccountExtension(Base):
    """Operator-specific account fields (§7a `operator_account_extension`).

    ``tier_2_verification`` is feature-flagged off at Stage 0 and ships in
    Stage 4b; the column exists so the contract is frozen.
    """

    __tablename__ = "operator_account_extension"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        primary_key=True,
    )
    verification_tier: Mapped[str] = mapped_column(
        operator_verification_tier_enum,
        nullable=False,
        server_default=text("'tier_0'"),
    )
    tier_1_verifications: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    tier_2_verification: Mapped[dict[str, object] | None] = mapped_column(
        JSONB, nullable=True
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class BrandCustomerAccountExtension(Base):
    """Brand-customer-specific account fields (§7a).

    ``customer_id`` references the Stage 5 commercial-entity ``customer`` table,
    which is out of Stage 0 scope; no database-level foreign key is emitted for
    it. At Stage 0 the registration flow populates it against a stub
    ``customer`` row. It is ``NOT NULL`` — every brand-customer account belongs
    to exactly one customer, the per-customer isolation anchor of §7e.
    """

    __tablename__ = "brand_customer_account_extension"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        primary_key=True,
    )
    customer_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # The per-customer isolation anchor must always be populated (§7a).
        CheckConstraint(
            "customer_id IS NOT NULL",
            name="customer_id_not_null",
        ),
    )


class FoundationUserAccountExtension(Base):
    """Foundation-internal account fields (§7a).

    Most ``governance_role`` values are stubbed / feature-flagged off until
    Foundation incorporation and Trust Council seating; only ``maintainer`` and
    ``ops_staff`` are active at Stage 0.
    """

    __tablename__ = "foundation_user_account_extension"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("user_account.user_id"),
        primary_key=True,
    )
    governance_role: Mapped[str] = mapped_column(
        foundation_governance_role_enum, nullable=False
    )
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
