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

"""Role-based access control — the cross-archetype RBAC framework.

This is the *framework* half of PRD §7e's RBAC requirement: a common permission
model, plus role definitions over it, against which each archetype configures
its own roles. It is data and pure logic — no I/O. Persistence of *who holds
which role* lives in ``user_role_assignment`` and is the concern of
:mod:`trustlist_auth.service`; this module answers the question "given a set of
held roles, does the user have permission X?".

Design.

* A :class:`Permission` is an opaque identifier in the common permission model.
  Permissions are namespaced ``area:verb`` strings (``score:read``,
  ``team:manage``) so the catalogue stays greppable and self-documenting.
* A :class:`Role` is a named bundle of permissions, declared in code and pinned
  to one archetype. Role *identifiers* are what ``user_role_assignment.role``
  stores; the framework validates an assignment's role string against the
  owning archetype's catalogue.
* A :class:`RoleCatalogue` is one archetype's complete set of roles. The three
  Stage-0 catalogues — operator, brand-customer, Foundation-internal — are
  declared at the foot of this module straight from ``CONTEXT.md`` and PRD §7e.

WebAuthn and tier-2 custom roles are out of Stage-0 scope; this model accepts
both later without an API change — a custom role is just another
:class:`Role`, and WebAuthn changes nothing here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from trustlist_auth.errors import UnknownPermission, UnknownRole


class Archetype(StrEnum):
    """The three user archetypes (mirrors ``user_account.archetype``)."""

    OPERATOR = "operator"
    BRAND_CUSTOMER = "brand_customer"
    FOUNDATION_INTERNAL = "foundation_internal"


class Permission(StrEnum):
    """The common permission model — every grantable capability at Stage 0.

    Permissions are ``area:verb`` strings. The set is deliberately small at
    Stage 0; later stages extend it, and an extension is purely additive.
    """

    # --- Account and team administration (all archetypes) ------------------
    TEAM_MANAGE = "team:manage"
    """Invite, remove and re-role fellow team members."""
    ACCOUNT_MANAGE = "account:manage"
    """Change account-level settings; the highest-trust administrative grant."""

    # --- Operator / Publisher-Portal capabilities --------------------------
    DOMAIN_VIEW = "domain:view"
    """View the trust scores and signals for the account's own domains."""
    EVIDENCE_SUBMIT = "evidence:submit"
    """Submit evidence about an owned domain."""
    ATTESTATION_DEPLOY = "attestation:deploy"
    """Deploy and manage attestation for an owned domain."""
    DECISION_CONTEST = "decision:contest"
    """Raise a contestation against a scoring or pool-membership decision."""

    # --- Brand-customer capabilities ---------------------------------------
    AUDIT_MANAGE = "audit:manage"
    """Create and manage audit envelopes."""
    AUDIT_VIEW = "audit:view"
    """View audit results."""
    CERTIFICATION_GENERATE = "certification:generate"
    """Generate and share certification artefacts."""
    DATA_SOURCE_CONNECT = "data_source:connect"
    """Connect and configure DSP / SSP / ad-server data sources."""

    # --- Foundation-internal / governance capabilities ---------------------
    GOVERNANCE_VOTE = "governance:vote"
    """Cast a binding vote in Trust Council ratification."""
    GOVERNANCE_OBSERVE = "governance:observe"
    """Observe Trust Council proceedings without a binding vote."""
    ADJUDICATION_REVIEW = "adjudication:review"
    """Staff the contestation review queue and adjudicate disputes."""
    PLATFORM_OPERATE = "platform:operate"
    """Perform Foundation operational work — incorporation, partnerships."""
    CODEBASE_MAINTAIN = "codebase:maintain"
    """Maintain the open-source codebase as a Foundation maintainer."""


@dataclass(frozen=True)
class Role:
    """A named bundle of permissions, pinned to one archetype.

    :param identifier: the stable string stored in ``user_role_assignment.role``.
    :param archetype: the archetype this role belongs to. A role is only
        meaningful — and only assignable — within its own archetype.
    :param permissions: the permissions the role confers.
    :param description: a one-line human-readable summary.
    :param feature_flagged_off: when ``True``, the role exists in the catalogue
        (so the contract is frozen) but is not yet active. Several
        Foundation-internal governance roles are flagged off at Stage 0 pending
        Foundation incorporation — see PRD §7e and ``CONTEXT.md``.
    """

    identifier: str
    archetype: Archetype
    permissions: frozenset[Permission]
    description: str
    feature_flagged_off: bool = False

    def grants(self, permission: Permission) -> bool:
        """Return ``True`` iff this role confers ``permission``."""
        return permission in self.permissions


@dataclass(frozen=True)
class RoleCatalogue:
    """One archetype's complete, immutable set of roles.

    Constructing a catalogue validates that every role belongs to the
    catalogue's archetype, so a mis-pinned role is caught at import time rather
    than at an access check.
    """

    archetype: Archetype
    roles: dict[str, Role] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate that every role is pinned to this catalogue's archetype."""
        for identifier, role in self.roles.items():
            if role.identifier != identifier:
                raise ValueError(
                    f"role keyed as {identifier!r} has identifier "
                    f"{role.identifier!r}"
                )
            if role.archetype is not self.archetype:
                raise ValueError(
                    f"role {identifier!r} is pinned to {role.archetype} but "
                    f"sits in the {self.archetype} catalogue"
                )

    def get(self, identifier: str) -> Role:
        """Return the role named ``identifier``.

        :raises UnknownRole: if no such role exists in this catalogue.
        """
        try:
            return self.roles[identifier]
        except KeyError as exc:
            raise UnknownRole(
                f"{identifier!r} is not a role in the {self.archetype} catalogue"
            ) from exc

    def has(self, identifier: str) -> bool:
        """Return ``True`` iff ``identifier`` names a role in this catalogue."""
        return identifier in self.roles

    def active_role_identifiers(self) -> frozenset[str]:
        """Return the identifiers of every role that is *not* feature-flagged off."""
        return frozenset(
            identifier
            for identifier, role in self.roles.items()
            if not role.feature_flagged_off
        )

    def resolve_permissions(self, role_identifiers: Iterable[str]) -> frozenset[Permission]:
        """Return the union of permissions conferred by ``role_identifiers``.

        A feature-flagged-off role contributes no permissions even when held —
        the flag gates the role's *effect*, not merely its assignability.

        :raises UnknownRole: if any identifier is not in this catalogue.
        """
        resolved: set[Permission] = set()
        for identifier in role_identifiers:
            role = self.get(identifier)
            if role.feature_flagged_off:
                continue
            resolved.update(role.permissions)
        return frozenset(resolved)

    def has_permission(
        self,
        role_identifiers: Iterable[str],
        permission: Permission,
    ) -> bool:
        """Return ``True`` iff the held roles together confer ``permission``.

        :raises UnknownRole: if any identifier is not in this catalogue.
        """
        return permission in self.resolve_permissions(role_identifiers)


# --- Catalogue construction -------------------------------------------------


def _role(
    identifier: str,
    archetype: Archetype,
    permissions: Iterable[Permission],
    description: str,
    *,
    feature_flagged_off: bool = False,
) -> Role:
    """Construct a :class:`Role` — a terse helper for the catalogue declarations."""
    return Role(
        identifier=identifier,
        archetype=archetype,
        permissions=frozenset(permissions),
        description=description,
        feature_flagged_off=feature_flagged_off,
    )


def _catalogue(archetype: Archetype, roles: Iterable[Role]) -> RoleCatalogue:
    """Construct a :class:`RoleCatalogue` keyed by role identifier."""
    return RoleCatalogue(
        archetype=archetype,
        roles={role.identifier: role for role in roles},
    )


# --- Operator catalogue — four built-in roles (PRD §7e, CONTEXT.md) ---------
#
# Owner > Admin > Operator > Read-only, with permissions nesting downward.

OPERATOR_CATALOGUE: RoleCatalogue = _catalogue(
    Archetype.OPERATOR,
    [
        _role(
            "owner",
            Archetype.OPERATOR,
            [
                Permission.ACCOUNT_MANAGE,
                Permission.TEAM_MANAGE,
                Permission.DOMAIN_VIEW,
                Permission.EVIDENCE_SUBMIT,
                Permission.ATTESTATION_DEPLOY,
                Permission.DECISION_CONTEST,
            ],
            "Account owner — full control including account-level settings.",
        ),
        _role(
            "admin",
            Archetype.OPERATOR,
            [
                Permission.TEAM_MANAGE,
                Permission.DOMAIN_VIEW,
                Permission.EVIDENCE_SUBMIT,
                Permission.ATTESTATION_DEPLOY,
                Permission.DECISION_CONTEST,
            ],
            "Administrator — manages the team and all operator workflows.",
        ),
        _role(
            "operator",
            Archetype.OPERATOR,
            [
                Permission.DOMAIN_VIEW,
                Permission.EVIDENCE_SUBMIT,
                Permission.ATTESTATION_DEPLOY,
                Permission.DECISION_CONTEST,
            ],
            "Operator — the default workhorse role; full domain workflows, "
            "no team administration.",
        ),
        _role(
            "read_only",
            Archetype.OPERATOR,
            [Permission.DOMAIN_VIEW],
            "Read-only — views scores and signals, makes no changes.",
        ),
    ],
)


# --- Brand-customer catalogue — five built-in roles (PRD §7e, CONTEXT.md) ---

BRAND_CUSTOMER_CATALOGUE: RoleCatalogue = _catalogue(
    Archetype.BRAND_CUSTOMER,
    [
        _role(
            "owner",
            Archetype.BRAND_CUSTOMER,
            [
                Permission.ACCOUNT_MANAGE,
                Permission.TEAM_MANAGE,
                Permission.AUDIT_MANAGE,
                Permission.AUDIT_VIEW,
                Permission.CERTIFICATION_GENERATE,
                Permission.DATA_SOURCE_CONNECT,
            ],
            "Account owner — full control including account-level settings.",
        ),
        _role(
            "admin",
            Archetype.BRAND_CUSTOMER,
            [
                Permission.TEAM_MANAGE,
                Permission.AUDIT_MANAGE,
                Permission.AUDIT_VIEW,
                Permission.CERTIFICATION_GENERATE,
                Permission.DATA_SOURCE_CONNECT,
            ],
            "Administrator — manages the team and all brand-customer workflows.",
        ),
        _role(
            "operator",
            Archetype.BRAND_CUSTOMER,
            [
                Permission.AUDIT_MANAGE,
                Permission.AUDIT_VIEW,
                Permission.CERTIFICATION_GENERATE,
                Permission.DATA_SOURCE_CONNECT,
            ],
            "Operator — the default workhorse role; runs audits and "
            "certifications, no team administration. Same name as the "
            "operator-archetype Operator role but a distinct scope.",
        ),
        _role(
            "analyst",
            Archetype.BRAND_CUSTOMER,
            [
                Permission.AUDIT_MANAGE,
                Permission.AUDIT_VIEW,
                Permission.CERTIFICATION_GENERATE,
            ],
            "Analyst — runs and interprets audits; cannot connect data sources.",
        ),
        _role(
            "read_only",
            Archetype.BRAND_CUSTOMER,
            [Permission.AUDIT_VIEW],
            "Read-only — views audit results, makes no changes.",
        ),
    ],
)


# --- Foundation-internal catalogue — five governance roles ------------------
#
# Per PRD §7e and CONTEXT.md, only `maintainer` and `ops_staff` are active at
# Stage 0; the Trust Council and adjudication roles are feature-flagged off
# pending Foundation incorporation and Trust Council seating. The role
# identifiers mirror foundation_governance_role in the data model.

FOUNDATION_INTERNAL_CATALOGUE: RoleCatalogue = _catalogue(
    Archetype.FOUNDATION_INTERNAL,
    [
        _role(
            "maintainer",
            Archetype.FOUNDATION_INTERNAL,
            [Permission.CODEBASE_MAINTAIN, Permission.PLATFORM_OPERATE],
            "Foundation maintainer — open-source codebase maintainer. Active "
            "at Stage 0.",
        ),
        _role(
            "ops_staff",
            Archetype.FOUNDATION_INTERNAL,
            [Permission.PLATFORM_OPERATE],
            "Foundation operations staff — incorporation, partnership and "
            "community work. Active at Stage 0.",
        ),
        _role(
            "trust_council_voting",
            Archetype.FOUNDATION_INTERNAL,
            [Permission.GOVERNANCE_VOTE, Permission.GOVERNANCE_OBSERVE],
            "Trust Council voting member. Feature-flagged off until Foundation "
            "incorporation and Trust Council seating.",
            feature_flagged_off=True,
        ),
        _role(
            "trust_council_observer",
            Archetype.FOUNDATION_INTERNAL,
            [Permission.GOVERNANCE_OBSERVE],
            "Trust Council observer (IAB-SA, commercial-entity). "
            "Feature-flagged off until Trust Council seating.",
            feature_flagged_off=True,
        ),
        _role(
            "adjudicator",
            Archetype.FOUNDATION_INTERNAL,
            [Permission.ADJUDICATION_REVIEW],
            "Designated adjudicator — staffs the contestation review queue. "
            "Feature-flagged off until Foundation incorporation.",
            feature_flagged_off=True,
        ),
    ],
)


# The Stage-0 catalogue set, indexed by archetype. Each archetype "configures
# its own role definitions over a common permission model" (PRD §7e) by owning
# one entry in this mapping.
CATALOGUES: dict[Archetype, RoleCatalogue] = {
    Archetype.OPERATOR: OPERATOR_CATALOGUE,
    Archetype.BRAND_CUSTOMER: BRAND_CUSTOMER_CATALOGUE,
    Archetype.FOUNDATION_INTERNAL: FOUNDATION_INTERNAL_CATALOGUE,
}


def catalogue_for(archetype: Archetype) -> RoleCatalogue:
    """Return the :class:`RoleCatalogue` for ``archetype``."""
    return CATALOGUES[archetype]


def validate_permission(permission: str) -> Permission:
    """Coerce a permission string to a :class:`Permission`.

    :raises UnknownPermission: if ``permission`` is not in the common model.
    """
    try:
        return Permission(permission)
    except ValueError as exc:
        raise UnknownPermission(
            f"{permission!r} is not a permission in the common model"
        ) from exc
