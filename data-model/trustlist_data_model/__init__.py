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

"""TrustList canonical-store ORM models and schema metadata.

This package holds the SQLAlchemy declarative models for the fifteen Stage 0
tables of Stage 0 PRD §7a — eight domain-side, seven authentication-side — plus
the ``Base`` declarative class whose ``metadata`` is the single source of truth
that Alembic's autogenerate machinery and the migrations compare against.

The append-only discipline (§7a) and the dedicated application database role
are enforced in the migration, not here; the ORM models describe the table
*shapes* only.
"""

from trustlist_data_model.models import (
    APPEND_ONLY_TABLES,
    Attestation,
    AuthAuditEvent,
    Base,
    BrandCustomerAccountExtension,
    Domain,
    DomainPoolMembership,
    Evidence,
    FoundationUserAccountExtension,
    OperatorAccountExtension,
    Pool,
    Provenance,
    Score,
    ScoreHistory,
    UserAccount,
    UserRoleAssignment,
    UserSession,
    metadata,
)

__all__ = [
    "APPEND_ONLY_TABLES",
    "Attestation",
    "AuthAuditEvent",
    "Base",
    "BrandCustomerAccountExtension",
    "Domain",
    "DomainPoolMembership",
    "Evidence",
    "FoundationUserAccountExtension",
    "OperatorAccountExtension",
    "Pool",
    "Provenance",
    "Score",
    "ScoreHistory",
    "UserAccount",
    "UserRoleAssignment",
    "UserSession",
    "metadata",
]
