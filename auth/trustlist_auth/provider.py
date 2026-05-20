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

"""The identity-provider abstraction.

ADR-0014 chose Clerk as TrustList's identity provider, but PRD §7e makes the
*abstraction* a hard architectural requirement: application code calls library
functions and never binds to a provider SDK directly, so the provider stays
replaceable (the ADR names Keycloak as the migration target).

This module defines that abstraction as a :class:`typing.Protocol` —
:class:`IdentityProvider` — plus the value types the protocol trades in. A
provider is the *credential store and session authority*: it owns email
addresses, password hashes, TOTP secrets and session lifecycle. It does **not**
own RBAC role assignments or the audit trail — those are TrustList-canonical
state (``user_role_assignment``, ``auth_audit_event``) and are the concern of
:class:`trustlist_auth.service.AuthService`.

Two implementations exist behind this protocol:

* :class:`trustlist_auth.fake_provider.InMemoryIdentityProvider` — a complete,
  in-memory provider used by this library's tests and by local development. It
  is the reference implementation: it defines, by construction, exactly what a
  conforming provider must do.
* the Clerk adapter — *not built in this issue*. Issues 17–19 wire a thin Clerk
  adapter behind this same protocol. The protocol is deliberately shaped so
  that adapter is a translation layer (Clerk session token in, :class:`Session`
  out), not a rewrite.

Why a ``Protocol`` and not an ABC: structural typing means the Clerk adapter
and any future provider conform by *shape*, with no import-time dependency on
this module — the looser coupling the ADR's replaceability requirement wants.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from trustlist_auth.rbac import Archetype


@dataclass(frozen=True)
class ProviderUser:
    """A provider's record of an account.

    This is the provider's *credential-side* view: identity and 2FA state. The
    archetype-specific extension data (verification tier, customer id and so
    on) lives in the canonical store, not here.

    :param user_id: the stable account identifier. The same value keys
        ``user_account.user_id`` in the canonical store.
    :param archetype: which archetype the account belongs to. A provider
        instance is scoped to a trust boundary — ADR-0014 runs two Clerk
        applications — and will only ever return accounts of the archetypes it
        serves.
    :param email: the account's email address.
    :param email_verified: whether the email has been verified.
    :param totp_enrolled: whether TOTP 2FA has been enrolled.
    :param disabled: whether the account is disabled.
    """

    user_id: str
    archetype: Archetype
    email: str
    email_verified: bool
    totp_enrolled: bool
    disabled: bool


@dataclass(frozen=True)
class Session:
    """An authenticated session issued by the provider.

    :param session_id: the stable session identifier. Mirrors
        ``user_session.session_id``.
    :param user_id: the account the session belongs to.
    :param archetype: the account's archetype, carried so the application can
        route without a second provider round-trip.
    :param issued_at: when the session was issued (UTC).
    :param expires_at: when the session's TTL elapses (UTC).
    :param device_fingerprint: an optional opaque device identifier supplied at
        issue time; mirrors ``user_session.device_fingerprint``.
    """

    session_id: str
    user_id: str
    archetype: Archetype
    issued_at: datetime.datetime
    expires_at: datetime.datetime
    device_fingerprint: str | None = None


@dataclass(frozen=True)
class TotpEnrolment:
    """The material returned when a user begins TOTP enrolment.

    :param secret: the base32 TOTP secret. Sensitive — shown to the user once
        (typically as a QR code) and then persisted encrypted-at-rest by the
        provider. Never logged.
    :param provisioning_uri: the ``otpauth://`` URI an authenticator app scans.
    """

    secret: str = field(repr=False)
    provisioning_uri: str


@runtime_checkable
class IdentityProvider(Protocol):
    """The stable interface every TrustList identity provider implements.

    Application code depends on this protocol, never on a concrete provider.
    The method set is exactly PRD §7e's named surface — ``authenticate``,
    ``issue_session``, ``revoke_session``, ``enrol_totp``, ``verify_totp`` —
    plus the registration and lookup operations the auth services in issues
    17–19 need to drive tier-0 email verification.

    A provider raises :class:`trustlist_auth.errors.AuthenticationFailed` for a
    failed credential check (with no detail of *why*, to avoid a user-
    enumeration oracle), and the other :mod:`trustlist_auth.errors` exceptions
    for the conditions they name.
    """

    def register(self, archetype: Archetype, email: str, password: str) -> ProviderUser:
        """Create an account and return the provider's record of it.

        The account starts with an unverified email and no TOTP enrolment. The
        password is hashed by the provider; the plaintext is never retained.

        :raises trustlist_auth.errors.AuthError: if an account already exists
            for ``(archetype, email)``.
        """
        ...

    def get_user(self, user_id: str) -> ProviderUser:
        """Return the provider's record of ``user_id``.

        :raises trustlist_auth.errors.AuthError: if no such account exists.
        """
        ...

    def find_user_by_email(
        self, archetype: Archetype, email: str
    ) -> ProviderUser | None:
        """Return the account for ``(archetype, email)``, or ``None``."""
        ...

    def authenticate(
        self, archetype: Archetype, email: str, password: str
    ) -> ProviderUser:
        """Verify an email-and-password credential.

        This is the first authentication factor only — TOTP is a separate step
        the application drives via :meth:`verify_totp`. A successful return
        means the password matched and the account is not disabled.

        :raises trustlist_auth.errors.AuthenticationFailed: if the email is
            unknown or the password is wrong — the same exception either way.
        :raises trustlist_auth.errors.AccountDisabled: if the account is
            disabled.
        """
        ...

    def mark_email_verified(self, user_id: str) -> ProviderUser:
        """Record that ``user_id`` has completed email verification.

        Idempotent — verifying an already-verified email is a no-op.
        """
        ...

    def change_password(self, user_id: str, new_password: str) -> None:
        """Replace ``user_id``'s password.

        The new plaintext is hashed and the old hash discarded; neither
        plaintext is retained.
        """
        ...

    def issue_session(
        self, user_id: str, *, device_fingerprint: str | None = None
    ) -> Session:
        """Issue a fresh session for ``user_id``.

        :param device_fingerprint: an optional opaque device identifier,
            carried onto the :class:`Session` and into ``user_session``.
        :raises trustlist_auth.errors.AccountDisabled: if the account is
            disabled.
        """
        ...

    def get_session(self, session_id: str) -> Session:
        """Return the live session for ``session_id``.

        :raises trustlist_auth.errors.SessionNotFound: if no such session
            exists.
        :raises trustlist_auth.errors.SessionRevoked: if the session has been
            revoked or its TTL has elapsed.
        """
        ...

    def revoke_session(self, session_id: str) -> None:
        """Revoke ``session_id``.

        Idempotent — revoking an already-revoked or unknown session does not
        raise, so a logout path need not race a concurrent revoke.
        """
        ...

    def revoke_all_sessions(self, user_id: str) -> None:
        """Revoke every live session belonging to ``user_id``.

        Used on a password change or a role change, per PRD §7a's session-
        revocation triggers.
        """
        ...

    def enrol_totp(self, user_id: str) -> TotpEnrolment:
        """Begin TOTP enrolment for ``user_id``.

        Returns the secret and provisioning URI. Enrolment is *completed* by a
        subsequent successful :meth:`verify_totp` — this method only generates
        and stores the secret.

        :raises trustlist_auth.errors.TotpAlreadyEnrolled: if TOTP is already
            enrolled.
        """
        ...

    def verify_totp(self, user_id: str, code: str) -> bool:
        """Verify a TOTP ``code`` for ``user_id``.

        The first successful verification after :meth:`enrol_totp` *completes*
        enrolment (sets ``totp_enrolled``). Subsequent calls are the 2FA
        challenge check on the login path.

        :returns: ``True`` if the code verified, ``False`` otherwise.
        :raises trustlist_auth.errors.TotpNotEnrolled: if :meth:`enrol_totp`
            has not been called for this account.
        """
        ...

    def disable_account(self, user_id: str) -> None:
        """Disable ``user_id`` and revoke all of its sessions.

        Idempotent — disabling an already-disabled account is a no-op.
        """
        ...
