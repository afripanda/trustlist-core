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

"""TrustList shared authentication library — Stage 0 PRD §7e.

This package is the shared authentication foundation every archetype's auth
surface (operator, brand-customer, Foundation-internal — issues 17, 18, 19)
builds on. It is the *shared library and core primitives* only; the
per-archetype surfaces are out of this issue's scope.

What the package provides:

- **Identity-verification primitives** — Argon2id password hashing
  (:mod:`trustlist_auth.passwords`), RFC 6238 TOTP 2FA
  (:mod:`trustlist_auth.totp`), signed expiring email-verification tokens
  (:mod:`trustlist_auth.tokens`).
- **The provider abstraction** — :class:`IdentityProvider`, the stable
  interface ADR-0014's Clerk choice (and any future provider) sits behind, plus
  :class:`InMemoryIdentityProvider`, the reference in-memory implementation the
  tests run against.
- **The RBAC framework** — a common permission model and per-archetype role
  catalogues (:mod:`trustlist_auth.rbac`), with assignment persistence over
  ``user_role_assignment`` (:mod:`trustlist_auth.role_store`).
- **Audit-trail emission** — authentication events written to
  ``auth_audit_event`` and mirrored through a seam to the ``auth.audit``
  event-bus topic (:mod:`trustlist_auth.audit`).
- **The composition layer** — :class:`AuthService`, the single object the
  per-archetype surfaces hold.
- **A trust-boundary test harness** — exported test utilities
  (:mod:`trustlist_auth.testing`) for the Stage-0 isolation test (issue 23).
"""

from trustlist_auth.audit import (
    AUTH_AUDIT_TOPIC,
    AuditEvent,
    AuditEventSink,
    AuditTrail,
    AuthEventType,
    LoggingAuditEventSink,
    NullAuditEventSink,
)
from trustlist_auth.errors import (
    AccountDisabled,
    AuthenticationFailed,
    AuthError,
    EmailNotVerified,
    RoleScopeError,
    SessionNotFound,
    SessionRevoked,
    TokenError,
    TokenExpired,
    TokenInvalid,
    TotpAlreadyEnrolled,
    TotpError,
    TotpNotEnrolled,
    TotpVerificationFailed,
    UnknownPermission,
    UnknownRole,
)
from trustlist_auth.fake_provider import InMemoryIdentityProvider
from trustlist_auth.passwords import hash_password, needs_rehash, verify_password
from trustlist_auth.provider import (
    IdentityProvider,
    ProviderUser,
    Session,
    TotpEnrolment,
)
from trustlist_auth.rbac import (
    BRAND_CUSTOMER_CATALOGUE,
    CATALOGUES,
    FOUNDATION_INTERNAL_CATALOGUE,
    OPERATOR_CATALOGUE,
    Archetype,
    Permission,
    Role,
    RoleCatalogue,
    catalogue_for,
    validate_permission,
)
from trustlist_auth.role_store import RoleAssignment, RoleStore
from trustlist_auth.service import AuthService
from trustlist_auth.tokens import (
    DEFAULT_EMAIL_TOKEN_TTL,
    TokenSigner,
    VerificationClaim,
)
from trustlist_auth.totp import (
    TOTP_DIGITS,
    TOTP_INTERVAL_SECONDS,
    current_totp,
    generate_totp_secret,
    provisioning_uri,
    verify_totp,
)

__all__ = [
    "AUTH_AUDIT_TOPIC",
    "BRAND_CUSTOMER_CATALOGUE",
    "CATALOGUES",
    "DEFAULT_EMAIL_TOKEN_TTL",
    "FOUNDATION_INTERNAL_CATALOGUE",
    "OPERATOR_CATALOGUE",
    "TOTP_DIGITS",
    "TOTP_INTERVAL_SECONDS",
    "AccountDisabled",
    "Archetype",
    "AuditEvent",
    "AuditEventSink",
    "AuditTrail",
    "AuthError",
    "AuthEventType",
    "AuthService",
    "AuthenticationFailed",
    "EmailNotVerified",
    "IdentityProvider",
    "InMemoryIdentityProvider",
    "LoggingAuditEventSink",
    "NullAuditEventSink",
    "Permission",
    "ProviderUser",
    "Role",
    "RoleAssignment",
    "RoleCatalogue",
    "RoleScopeError",
    "RoleStore",
    "Session",
    "SessionNotFound",
    "SessionRevoked",
    "TokenError",
    "TokenExpired",
    "TokenInvalid",
    "TokenSigner",
    "TotpAlreadyEnrolled",
    "TotpEnrolment",
    "TotpError",
    "TotpNotEnrolled",
    "TotpVerificationFailed",
    "UnknownPermission",
    "UnknownRole",
    "VerificationClaim",
    "catalogue_for",
    "current_totp",
    "generate_totp_secret",
    "hash_password",
    "needs_rehash",
    "provisioning_uri",
    "validate_permission",
    "verify_password",
    "verify_totp",
]
