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

"""An in-memory identity provider — the reference :class:`IdentityProvider`.

ADR-0014 chose Clerk, but there is no Clerk account yet, so the library ships
its own complete provider implementation. :class:`InMemoryIdentityProvider`:

* is the provider this library's tests and local development run against — no
  network, no Clerk credentials;
* is the *reference* implementation. It conforms to the
  :class:`trustlist_auth.provider.IdentityProvider` protocol by construction,
  so it documents, executably, exactly what the Clerk adapter built in issues
  17–19 must do;
* is a legitimate test fixture for *consumers* of the library: PRD §7e requires
  that "a test fixture provider can be swapped in without changing application
  code", and this is that fixture.

It is **not** a production provider — it holds all state in process memory and
loses everything on restart. The real credential store is Clerk.

Security notes, even for an in-memory provider — because the same disciplines
must hold in the Clerk adapter:

* passwords are Argon2id-hashed via :mod:`trustlist_auth.passwords`; the
  plaintext is never stored;
* TOTP secrets are generated via :mod:`trustlist_auth.totp` and held in a field
  whose ``repr`` is suppressed, so a debugger dump or a log of the record does
  not leak the secret;
* :meth:`authenticate` raises the same :class:`AuthenticationFailed` for an
  unknown email and a wrong password — no user-enumeration oracle.
"""

from __future__ import annotations

import datetime
import secrets
import uuid
from dataclasses import dataclass, field

from trustlist_auth.errors import (
    AccountDisabled,
    AuthenticationFailed,
    AuthError,
    SessionNotFound,
    SessionRevoked,
    TotpAlreadyEnrolled,
    TotpNotEnrolled,
)
from trustlist_auth.passwords import hash_password, needs_rehash, verify_password
from trustlist_auth.provider import ProviderUser, Session, TotpEnrolment
from trustlist_auth.rbac import Archetype
from trustlist_auth.totp import generate_totp_secret, provisioning_uri, verify_totp

# Default session lifetime. A real provider reads this from configuration; the
# in-memory provider fixes a sensible twelve-hour default.
_DEFAULT_SESSION_TTL = datetime.timedelta(hours=12)


def _now() -> datetime.datetime:
    """Return the current UTC time — the single time source for this module."""
    return datetime.datetime.now(tz=datetime.UTC)


@dataclass
class _Account:
    """The provider's full internal record of one account.

    Distinct from the protocol's :class:`ProviderUser`, which is the immutable
    *view* handed to callers. This record is mutable provider-private state.
    """

    user_id: str
    archetype: Archetype
    email: str
    password_hash: str = field(repr=False)
    email_verified: bool = False
    disabled: bool = False
    # Set once enrolment begins; `totp_confirmed` flips on the first successful
    # verification, which is what "enrolled" means to the protocol.
    totp_secret: str | None = field(default=None, repr=False)
    totp_confirmed: bool = False

    def view(self) -> ProviderUser:
        """Return the immutable :class:`ProviderUser` view of this account."""
        return ProviderUser(
            user_id=self.user_id,
            archetype=self.archetype,
            email=self.email,
            email_verified=self.email_verified,
            totp_enrolled=self.totp_confirmed,
            disabled=self.disabled,
        )


@dataclass
class _SessionRecord:
    """The provider's internal record of one session."""

    session_id: str
    user_id: str
    archetype: Archetype
    issued_at: datetime.datetime
    expires_at: datetime.datetime
    device_fingerprint: str | None
    revoked: bool = False

    def view(self) -> Session:
        """Return the immutable :class:`Session` view of this record."""
        return Session(
            session_id=self.session_id,
            user_id=self.user_id,
            archetype=self.archetype,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
            device_fingerprint=self.device_fingerprint,
        )


class InMemoryIdentityProvider:
    """A complete, in-memory :class:`IdentityProvider` implementation."""

    def __init__(self, *, session_ttl: datetime.timedelta = _DEFAULT_SESSION_TTL) -> None:
        """Initialise an empty provider.

        :param session_ttl: how long an issued session stays valid.
        """
        self._session_ttl = session_ttl
        self._accounts: dict[str, _Account] = {}
        # Secondary index: (archetype, lower-cased email) -> user_id.
        self._email_index: dict[tuple[Archetype, str], str] = {}
        self._sessions: dict[str, _SessionRecord] = {}

    # --- registration and lookup -------------------------------------------

    def register(self, archetype: Archetype, email: str, password: str) -> ProviderUser:
        """Create an account; see :meth:`IdentityProvider.register`."""
        key = (archetype, email.lower())
        if key in self._email_index:
            raise AuthError(
                f"an account already exists for {email!r} in the "
                f"{archetype} archetype"
            )
        account = _Account(
            user_id=str(uuid.uuid4()),
            archetype=archetype,
            email=email,
            password_hash=hash_password(password),
        )
        self._accounts[account.user_id] = account
        self._email_index[key] = account.user_id
        return account.view()

    def get_user(self, user_id: str) -> ProviderUser:
        """Return the record of ``user_id``; see :meth:`IdentityProvider.get_user`."""
        return self._account(user_id).view()

    def find_user_by_email(
        self, archetype: Archetype, email: str
    ) -> ProviderUser | None:
        """Look an account up by email; see the protocol method."""
        user_id = self._email_index.get((archetype, email.lower()))
        if user_id is None:
            return None
        return self._accounts[user_id].view()

    # --- credential check --------------------------------------------------

    def authenticate(
        self, archetype: Archetype, email: str, password: str
    ) -> ProviderUser:
        """Verify an email/password pair; see :meth:`IdentityProvider.authenticate`.

        An unknown email and a wrong password both raise
        :class:`AuthenticationFailed` with the same message. When the email is
        unknown a verify is still run against a throwaway hash so the response
        time does not betray whether the account exists.
        """
        user_id = self._email_index.get((archetype, email.lower()))
        if user_id is None:
            # Burn a verify against a dummy hash to keep the timing of the
            # unknown-email and wrong-password paths indistinguishable.
            verify_password(hash_password("decoy"), password)
            raise AuthenticationFailed
        account = self._accounts[user_id]
        if not verify_password(account.password_hash, password):
            raise AuthenticationFailed
        if account.disabled:
            raise AccountDisabled(f"account {user_id} is disabled")
        # Transparently upgrade the stored hash if the cost profile was raised.
        if needs_rehash(account.password_hash):
            account.password_hash = hash_password(password)
        return account.view()

    def mark_email_verified(self, user_id: str) -> ProviderUser:
        """Record email verification; see the protocol method."""
        account = self._account(user_id)
        account.email_verified = True
        return account.view()

    def change_password(self, user_id: str, new_password: str) -> None:
        """Replace a password; see :meth:`IdentityProvider.change_password`."""
        account = self._account(user_id)
        account.password_hash = hash_password(new_password)

    # --- sessions ----------------------------------------------------------

    def issue_session(
        self, user_id: str, *, device_fingerprint: str | None = None
    ) -> Session:
        """Issue a session; see :meth:`IdentityProvider.issue_session`."""
        account = self._account(user_id)
        if account.disabled:
            raise AccountDisabled(f"account {user_id} is disabled")
        issued = _now()
        record = _SessionRecord(
            session_id=str(uuid.uuid4()),
            user_id=user_id,
            archetype=account.archetype,
            issued_at=issued,
            expires_at=issued + self._session_ttl,
            device_fingerprint=device_fingerprint,
        )
        self._sessions[record.session_id] = record
        return record.view()

    def get_session(self, session_id: str) -> Session:
        """Return a live session; see :meth:`IdentityProvider.get_session`."""
        record = self._sessions.get(session_id)
        if record is None:
            raise SessionNotFound(f"no session {session_id!r}")
        if record.revoked:
            raise SessionRevoked(f"session {session_id!r} is revoked")
        if _now() >= record.expires_at:
            raise SessionRevoked(f"session {session_id!r} has expired")
        return record.view()

    def revoke_session(self, session_id: str) -> None:
        """Revoke a session; idempotent. See the protocol method."""
        record = self._sessions.get(session_id)
        if record is not None:
            record.revoked = True

    def revoke_all_sessions(self, user_id: str) -> None:
        """Revoke every session of a user; see the protocol method."""
        for record in self._sessions.values():
            if record.user_id == user_id:
                record.revoked = True

    # --- TOTP --------------------------------------------------------------

    def enrol_totp(self, user_id: str) -> TotpEnrolment:
        """Begin TOTP enrolment; see :meth:`IdentityProvider.enrol_totp`."""
        account = self._account(user_id)
        if account.totp_confirmed:
            raise TotpAlreadyEnrolled(f"account {user_id} already has TOTP enrolled")
        secret = generate_totp_secret()
        account.totp_secret = secret
        account.totp_confirmed = False
        return TotpEnrolment(
            secret=secret,
            provisioning_uri=provisioning_uri(secret, account.email),
        )

    def verify_totp(self, user_id: str, code: str) -> bool:
        """Verify a TOTP code; see :meth:`IdentityProvider.verify_totp`.

        The first successful verification after :meth:`enrol_totp` completes
        enrolment.
        """
        account = self._account(user_id)
        if account.totp_secret is None:
            raise TotpNotEnrolled(f"account {user_id} has not begun TOTP enrolment")
        if verify_totp(account.totp_secret, code):
            account.totp_confirmed = True
            return True
        return False

    # --- account lifecycle -------------------------------------------------

    def disable_account(self, user_id: str) -> None:
        """Disable an account and revoke its sessions; idempotent."""
        account = self._account(user_id)
        account.disabled = True
        self.revoke_all_sessions(user_id)

    # --- internals ---------------------------------------------------------

    def _account(self, user_id: str) -> _Account:
        """Return the internal record for ``user_id`` or raise."""
        account = self._accounts.get(user_id)
        if account is None:
            raise AuthError(f"no account {user_id!r}")
        return account

    def generate_secure_token(self) -> str:
        """Return a fresh URL-safe random token.

        A convenience for tests and tooling that need an opaque random string;
        not part of the :class:`IdentityProvider` protocol.
        """
        return secrets.token_urlsafe(32)
