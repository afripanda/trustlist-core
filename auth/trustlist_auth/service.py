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

"""The :class:`AuthService` — the library's composition layer.

This is the single object the per-archetype auth surfaces (issues 17–19) and
later application code hold. It composes the four primitives the rest of the
library provides:

* an :class:`~trustlist_auth.provider.IdentityProvider` — the credential store
  and session authority (the in-memory fake at Stage 0; the Clerk adapter once
  issues 17–19 wire it);
* a :class:`~trustlist_auth.role_store.RoleStore` — RBAC persistence over
  ``user_role_assignment``;
* an :class:`~trustlist_auth.audit.AuditTrail` — writes to ``auth_audit_event``
  and mirrors to the ``auth.audit`` seam;
* the :mod:`trustlist_auth.rbac` catalogues — the role/permission framework.

The provider is injected at construction, so swapping it — a fake in a test, a
real Clerk adapter in production — is a constructor argument and changes no
caller code. That is PRD §7e's hard replaceability requirement, made concrete.

Transaction model. Every method that touches canonical state takes the
SQLAlchemy connection of the caller's unit of work. The audit row and the role
change therefore commit (or roll back) atomically with the action — the service
never opens its own transaction. The *provider* side (sessions, TOTP) is
separate state owned by the provider; a method that does both performs the
provider call first and the audit write second, so a persisted audit row always
reflects a provider action that actually happened.
"""

from __future__ import annotations

import datetime

from sqlalchemy.engine import Connection

from trustlist_auth.audit import AuditTrail, AuthEventType
from trustlist_auth.errors import TotpVerificationFailed
from trustlist_auth.provider import IdentityProvider, ProviderUser, Session, TotpEnrolment
from trustlist_auth.rbac import Archetype, Permission, catalogue_for, validate_permission
from trustlist_auth.role_store import RoleAssignment, RoleStore


class AuthService:
    """The composed authentication surface — PRD §7e's named library entry point.

    Construct one per process at composition time::

        service = AuthService(
            provider=InMemoryIdentityProvider(),   # or the Clerk adapter
            audit_trail=AuditTrail(sink),
        )

    and pass it to the per-archetype surfaces. Every method that records an
    auth event takes a :class:`~sqlalchemy.engine.Connection` so the audit row
    is part of the caller's transaction.
    """

    def __init__(
        self,
        *,
        provider: IdentityProvider,
        audit_trail: AuditTrail | None = None,
        role_store: RoleStore | None = None,
    ) -> None:
        """Compose the service.

        :param provider: the identity provider — the only argument that
            changes between a test and production.
        :param audit_trail: the audit-trail writer; defaults to a fresh
            :class:`AuditTrail` with the Stage-0 null mirror sink.
        :param role_store: the RBAC persistence store; defaults to a fresh
            :class:`RoleStore`.
        """
        self._provider = provider
        self._audit = audit_trail or AuditTrail()
        self._roles = role_store or RoleStore()

    @property
    def provider(self) -> IdentityProvider:
        """The composed identity provider — exposed for the per-archetype surfaces."""
        return self._provider

    # --- registration and email verification -------------------------------

    def register(
        self, archetype: Archetype, email: str, password: str
    ) -> ProviderUser:
        """Register a new account with the provider.

        This is the credential-side of registration only. The per-archetype
        surfaces (issues 17–19) additionally create the canonical
        ``user_account`` row and the archetype extension; this library provides
        the provider primitive they build on. No audit event is recorded here —
        registration is audited by the archetype surface that owns the
        canonical row.
        """
        return self._provider.register(archetype, email, password)

    def mark_email_verified(self, user_id: str) -> ProviderUser:
        """Mark an account's email as verified (tier-0 verification)."""
        return self._provider.mark_email_verified(user_id)

    # --- authentication ----------------------------------------------------

    def authenticate(
        self,
        connection: Connection,
        archetype: Archetype,
        email: str,
        password: str,
        *,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> ProviderUser:
        """Verify an email-and-password credential — the first auth factor.

        A success records a ``login_success`` audit event; a failure records a
        ``login_failure`` event (with a ``None`` ``user_id`` — the address may
        not resolve to an account) and re-raises. TOTP is a separate second
        factor: callers proceed to :meth:`verify_totp` and only then to
        :meth:`issue_session`.

        :raises trustlist_auth.errors.AuthenticationFailed: on a bad credential.
        :raises trustlist_auth.errors.AccountDisabled: if the account is
            disabled.
        """
        try:
            user = self._provider.authenticate(archetype, email, password)
        except Exception:
            # Audit the failure with no user_id — the email may be unknown, and
            # the audit trail must not become a user-enumeration oracle. The
            # email itself is recorded so a security reviewer can still see the
            # targeted address; an email is not a credential.
            self._audit.record(
                connection,
                AuthEventType.LOGIN_FAILURE,
                user_id=None,
                ip_address_observed=ip_address,
                user_agent_observed=user_agent,
                event_details={"archetype": archetype.value, "email": email},
            )
            raise

        self._audit.record(
            connection,
            AuthEventType.LOGIN_SUCCESS,
            user_id=user.user_id,
            ip_address_observed=ip_address,
            user_agent_observed=user_agent,
            event_details={"archetype": archetype.value},
        )
        return user

    def change_password(
        self,
        connection: Connection,
        user_id: str,
        new_password: str,
        *,
        ip_address: str | None = None,
    ) -> None:
        """Change an account's password and revoke all its sessions.

        Revoking sessions on a password change is PRD §7a's session-revocation
        discipline. A ``password_change`` audit event is recorded — never with
        the password itself; :class:`AuditTrail` rejects credential material in
        the detail object as a defence-in-depth guard.
        """
        self._provider.change_password(user_id, new_password)
        self._provider.revoke_all_sessions(user_id)
        self._audit.record(
            connection,
            AuthEventType.PASSWORD_CHANGE,
            user_id=user_id,
            ip_address_observed=ip_address,
            event_details={"sessions_revoked": True},
        )

    # --- sessions ----------------------------------------------------------

    def issue_session(
        self,
        connection: Connection,
        user_id: str,
        *,
        device_fingerprint: str | None = None,
        ip_address: str | None = None,
    ) -> Session:
        """Issue a session for an already-authenticated user.

        Call this only after both factors have passed — :meth:`authenticate`
        and :meth:`verify_totp`. No audit event is recorded for the issue
        itself: the ``login_success`` and ``mfa_challenge_success`` events
        already mark the authentication; a session-issue is their consequence.
        """
        return self._provider.issue_session(
            user_id, device_fingerprint=device_fingerprint
        )

    def get_session(self, session_id: str) -> Session:
        """Return the live session for ``session_id``.

        :raises trustlist_auth.errors.SessionNotFound: if unknown.
        :raises trustlist_auth.errors.SessionRevoked: if revoked or expired.
        """
        return self._provider.get_session(session_id)

    def revoke_session(
        self,
        connection: Connection,
        session_id: str,
        *,
        user_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Revoke a session — a logout — and record a ``session_revoke`` event."""
        self._provider.revoke_session(session_id)
        self._audit.record(
            connection,
            AuthEventType.SESSION_REVOKE,
            user_id=user_id,
            ip_address_observed=ip_address,
            event_details={"session_id": session_id},
        )

    # --- TOTP --------------------------------------------------------------

    def enrol_totp(
        self, connection: Connection, user_id: str
    ) -> TotpEnrolment:
        """Begin TOTP enrolment and record an ``mfa_challenge_issued`` event.

        Returns the secret and provisioning URI. The secret is **not** written
        to the audit trail — :class:`AuditTrail` rejects it as credential
        material. Enrolment is completed by a subsequent successful
        :meth:`verify_totp`.
        """
        enrolment = self._provider.enrol_totp(user_id)
        self._audit.record(
            connection,
            AuthEventType.MFA_CHALLENGE_ISSUED,
            user_id=user_id,
            event_details={"purpose": "totp_enrolment"},
        )
        return enrolment

    def verify_totp(
        self,
        connection: Connection,
        user_id: str,
        code: str,
        *,
        ip_address: str | None = None,
    ) -> bool:
        """Verify a TOTP code — the second auth factor.

        Records ``mfa_challenge_success`` or ``mfa_challenge_failure``
        accordingly. The supplied code is never written to the audit trail.

        :returns: ``True`` if the code verified.
        """
        verified = self._provider.verify_totp(user_id, code)
        self._audit.record(
            connection,
            (
                AuthEventType.MFA_CHALLENGE_SUCCESS
                if verified
                else AuthEventType.MFA_CHALLENGE_FAILURE
            ),
            user_id=user_id,
            ip_address_observed=ip_address,
            event_details={"factor": "totp"},
        )
        return verified

    def verify_totp_strict(
        self,
        connection: Connection,
        user_id: str,
        code: str,
        *,
        ip_address: str | None = None,
    ) -> None:
        """Verify a TOTP code, raising on failure rather than returning a bool.

        A convenience for call sites that treat a bad second factor as an
        error path.

        :raises trustlist_auth.errors.TotpVerificationFailed: if the code does
            not verify.
        """
        if not self.verify_totp(connection, user_id, code, ip_address=ip_address):
            raise TotpVerificationFailed("TOTP code did not verify")

    # --- account lifecycle -------------------------------------------------

    def disable_account(
        self,
        connection: Connection,
        user_id: str,
        *,
        disabled_by_user_id: str | None = None,
    ) -> None:
        """Disable an account, revoke its sessions, record an audit event."""
        self._provider.disable_account(user_id)
        self._audit.record(
            connection,
            AuthEventType.ACCOUNT_DISABLE,
            user_id=user_id,
            event_details={"disabled_by": disabled_by_user_id},
        )

    # --- RBAC --------------------------------------------------------------

    def assign_role(
        self,
        connection: Connection,
        *,
        user_id: str,
        archetype: Archetype,
        role: str,
        granted_by: str | None,
        customer_id: str | None = None,
    ) -> RoleAssignment:
        """Grant a role and record a ``role_grant`` audit event.

        The role is validated against the archetype's catalogue and the
        customer-id scoping rule is enforced before the INSERT. The grant and
        its audit event commit in the caller's transaction.

        :raises trustlist_auth.errors.UnknownRole: if ``role`` is not in the
            archetype's catalogue.
        :raises trustlist_auth.errors.RoleScopeError: on a customer-id scoping
            violation.
        """
        assignment = self._roles.grant_role(
            connection,
            user_id=user_id,
            archetype=archetype,
            role=role,
            granted_by_user_id=granted_by,
            customer_id=customer_id,
        )
        self._audit.record(
            connection,
            AuthEventType.ROLE_GRANT,
            user_id=user_id,
            event_details={
                "role": role,
                "granted_by": granted_by,
                "customer_id": customer_id,
            },
        )
        return assignment

    def revoke_role(
        self,
        connection: Connection,
        *,
        user_id: str,
        role: str,
        granted_at: datetime.datetime,
        revoked_by: str | None = None,
    ) -> bool:
        """Revoke a specific role grant and record a ``role_revoke`` event.

        The provider's sessions for the user are also revoked — a role change
        is one of PRD §7a's session-revocation triggers.

        :returns: ``True`` if a live grant was revoked.
        """
        revoked = self._roles.revoke_role(
            connection, user_id=user_id, role=role, granted_at=granted_at
        )
        if revoked:
            self._provider.revoke_all_sessions(user_id)
            self._audit.record(
                connection,
                AuthEventType.ROLE_REVOKE,
                user_id=user_id,
                event_details={"role": role, "revoked_by": revoked_by},
            )
        return revoked

    def roles_for(
        self,
        connection: Connection,
        user_id: str,
        *,
        customer_id: str | None = None,
    ) -> frozenset[str]:
        """Return the identifiers of every live role ``user_id`` holds.

        :param customer_id: when supplied, restricts to grants scoped to that
            customer — the per-customer isolation filter.
        """
        return self._roles.live_role_identifiers(
            connection, user_id, customer_id=customer_id
        )

    def has_permission(
        self,
        connection: Connection,
        user_id: str,
        archetype: Archetype,
        permission: Permission | str,
        *,
        customer_id: str | None = None,
    ) -> bool:
        """Return ``True`` iff ``user_id``'s live roles confer ``permission``.

        Resolves the user's live role grants from ``user_role_assignment``,
        then asks the archetype's catalogue whether those roles together grant
        the permission. A feature-flagged-off role contributes nothing even
        when held.

        :param permission: a :class:`~trustlist_auth.rbac.Permission` or the
            equivalent string.
        :param customer_id: when supplied, only grants scoped to that customer
            count — the per-customer isolation check.
        :raises trustlist_auth.errors.UnknownPermission: if ``permission`` is
            not in the common permission model.
        """
        resolved = (
            permission
            if isinstance(permission, Permission)
            else validate_permission(permission)
        )
        held = self._roles.live_role_identifiers(
            connection, user_id, customer_id=customer_id
        )
        return catalogue_for(archetype).has_permission(held, resolved)
