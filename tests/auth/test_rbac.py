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

"""Unit tests for the RBAC framework (:mod:`trustlist_auth.rbac`)."""

from __future__ import annotations

import pytest

from trustlist_auth.errors import UnknownPermission, UnknownRole
from trustlist_auth.rbac import (
    BRAND_CUSTOMER_CATALOGUE,
    FOUNDATION_INTERNAL_CATALOGUE,
    OPERATOR_CATALOGUE,
    Archetype,
    Permission,
    catalogue_for,
    validate_permission,
)


def test_operator_catalogue_has_the_four_built_in_roles() -> None:
    """The operator catalogue carries Owner, Admin, Operator, Read-only."""
    assert set(OPERATOR_CATALOGUE.roles) == {
        "owner",
        "admin",
        "operator",
        "read_only",
    }


def test_brand_customer_catalogue_has_the_five_built_in_roles() -> None:
    """The brand-customer catalogue carries the five built-in roles."""
    assert set(BRAND_CUSTOMER_CATALOGUE.roles) == {
        "owner",
        "admin",
        "operator",
        "analyst",
        "read_only",
    }


def test_foundation_catalogue_has_the_five_governance_roles() -> None:
    """The Foundation-internal catalogue carries the five governance roles."""
    assert set(FOUNDATION_INTERNAL_CATALOGUE.roles) == {
        "maintainer",
        "ops_staff",
        "trust_council_voting",
        "trust_council_observer",
        "adjudicator",
    }


def test_catalogue_for_returns_the_right_catalogue() -> None:
    """catalogue_for maps each archetype to its catalogue."""
    assert catalogue_for(Archetype.OPERATOR) is OPERATOR_CATALOGUE
    assert catalogue_for(Archetype.BRAND_CUSTOMER) is BRAND_CUSTOMER_CATALOGUE
    assert (
        catalogue_for(Archetype.FOUNDATION_INTERNAL)
        is FOUNDATION_INTERNAL_CATALOGUE
    )


def test_owner_has_account_manage_permission() -> None:
    """The operator Owner role grants the account-management permission."""
    assert OPERATOR_CATALOGUE.has_permission(["owner"], Permission.ACCOUNT_MANAGE)


def test_read_only_lacks_evidence_submit() -> None:
    """The operator Read-only role does not grant evidence submission."""
    assert not OPERATOR_CATALOGUE.has_permission(
        ["read_only"], Permission.EVIDENCE_SUBMIT
    )


def test_operator_role_lacks_team_management() -> None:
    """The default operator workhorse role cannot manage the team."""
    assert not OPERATOR_CATALOGUE.has_permission(
        ["operator"], Permission.TEAM_MANAGE
    )
    assert OPERATOR_CATALOGUE.has_permission(["admin"], Permission.TEAM_MANAGE)


def test_permission_resolution_unions_multiple_roles() -> None:
    """Holding two roles confers the union of their permissions."""
    resolved = OPERATOR_CATALOGUE.resolve_permissions(["read_only", "operator"])
    assert Permission.DOMAIN_VIEW in resolved
    assert Permission.EVIDENCE_SUBMIT in resolved


def test_unknown_role_raises() -> None:
    """Resolving an identifier not in the catalogue raises UnknownRole."""
    with pytest.raises(UnknownRole):
        OPERATOR_CATALOGUE.has_permission(["nonexistent"], Permission.DOMAIN_VIEW)


def test_brand_customer_analyst_cannot_connect_data_sources() -> None:
    """The brand-customer Analyst role lacks the data-source-connect grant."""
    assert not BRAND_CUSTOMER_CATALOGUE.has_permission(
        ["analyst"], Permission.DATA_SOURCE_CONNECT
    )
    assert BRAND_CUSTOMER_CATALOGUE.has_permission(
        ["operator"], Permission.DATA_SOURCE_CONNECT
    )


def test_active_stage0_foundation_roles() -> None:
    """Only maintainer and ops_staff are active Foundation roles at Stage 0."""
    assert FOUNDATION_INTERNAL_CATALOGUE.active_role_identifiers() == frozenset(
        {"maintainer", "ops_staff"}
    )


def test_feature_flagged_role_confers_no_permission_even_when_held() -> None:
    """A feature-flagged-off role grants nothing even when assigned.

    The Trust Council voting role is flagged off at Stage 0; holding it must
    not confer the governance:vote permission until it is activated.
    """
    voting = FOUNDATION_INTERNAL_CATALOGUE.get("trust_council_voting")
    assert voting.feature_flagged_off is True
    # The role *definition* still carries the permission ...
    assert Permission.GOVERNANCE_VOTE in voting.permissions
    # ... but resolution through the catalogue ignores a flagged-off role.
    assert not FOUNDATION_INTERNAL_CATALOGUE.has_permission(
        ["trust_council_voting"], Permission.GOVERNANCE_VOTE
    )
    assert FOUNDATION_INTERNAL_CATALOGUE.resolve_permissions(
        ["trust_council_voting"]
    ) == frozenset()


def test_active_foundation_role_confers_its_permission() -> None:
    """An active Foundation role (maintainer) confers its permissions."""
    assert FOUNDATION_INTERNAL_CATALOGUE.has_permission(
        ["maintainer"], Permission.CODEBASE_MAINTAIN
    )


def test_role_identifiers_match_data_model_foundation_governance_enum() -> None:
    """Foundation role identifiers mirror the foundation_governance_role enum."""
    # The data model's foundation_governance_role enum labels.
    expected = {
        "trust_council_voting",
        "trust_council_observer",
        "adjudicator",
        "ops_staff",
        "maintainer",
    }
    assert set(FOUNDATION_INTERNAL_CATALOGUE.roles) == expected


def test_validate_permission_accepts_known_and_rejects_unknown() -> None:
    """validate_permission coerces a known string and rejects an unknown one."""
    assert validate_permission("domain:view") == Permission.DOMAIN_VIEW
    with pytest.raises(UnknownPermission):
        validate_permission("not:a-permission")


def test_every_role_is_pinned_to_its_catalogue_archetype() -> None:
    """Every role in every catalogue is pinned to that catalogue's archetype."""
    for archetype, catalogue in (
        (Archetype.OPERATOR, OPERATOR_CATALOGUE),
        (Archetype.BRAND_CUSTOMER, BRAND_CUSTOMER_CATALOGUE),
        (Archetype.FOUNDATION_INTERNAL, FOUNDATION_INTERNAL_CATALOGUE),
    ):
        for role in catalogue.roles.values():
            assert role.archetype is archetype
