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

"""Unit tests for the in-memory identity provider.

These also serve as the conformance tests for the
:class:`~trustlist_auth.provider.IdentityProvider` protocol: the in-memory
provider is the reference implementation, so what it does here is the contract
the Clerk adapter (issues 17–19) must match.
"""

from __future__ import annotations

import datetime

import pytest

from trustlist_auth.errors import (
    AccountDisabled,
    AuthenticationFailed,
    AuthError,
    SessionNotFound,
    SessionRevoked,
    TotpAlreadyEnrolled,
    TotpNotEnrolled,
)
from trustlist_auth.fake_provider import InMemoryIdentityProvider
from trustlist_auth.provider import IdentityProvider
from trustlist_auth.rbac import Archetype
from trustlist_auth.totp import current_totp


def test_in_memory_provider_satisfies_the_protocol() -> None:
    """The in-memory provider is a structural IdentityProvider."""
    assert isinstance(InMemoryIdentityProvider(), IdentityProvider)


def test_register_and_get_user() -> None:
    """A registered account is retrievable and starts unverified, no TOTP."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "op@example.com", "pw-correct")
    assert user.email == "op@example.com"
    assert user.archetype is Archetype.OPERATOR
    assert user.email_verified is False
    assert user.totp_enrolled is False
    assert user.disabled is False
    assert provider.get_user(user.user_id) == user


def test_duplicate_registration_within_archetype_raises() -> None:
    """A second registration for the same archetype+email raises."""
    provider = InMemoryIdentityProvider()
    provider.register(Archetype.OPERATOR, "dup@example.com", "pw")
    with pytest.raises(AuthError):
        provider.register(Archetype.OPERATOR, "dup@example.com", "pw")


def test_same_email_across_archetypes_is_allowed() -> None:
    """The same email may register under two different archetypes."""
    provider = InMemoryIdentityProvider()
    a = provider.register(Archetype.OPERATOR, "both@example.com", "pw")
    b = provider.register(Archetype.BRAND_CUSTOMER, "both@example.com", "pw")
    assert a.user_id != b.user_id


def test_authenticate_succeeds_with_correct_password() -> None:
    """Correct credentials authenticate."""
    provider = InMemoryIdentityProvider()
    registered = provider.register(Archetype.OPERATOR, "a@example.com", "right-pw")
    authed = provider.authenticate(Archetype.OPERATOR, "a@example.com", "right-pw")
    assert authed.user_id == registered.user_id


def test_authenticate_fails_with_wrong_password() -> None:
    """A wrong password raises AuthenticationFailed."""
    provider = InMemoryIdentityProvider()
    provider.register(Archetype.OPERATOR, "b@example.com", "right-pw")
    with pytest.raises(AuthenticationFailed):
        provider.authenticate(Archetype.OPERATOR, "b@example.com", "wrong-pw")


def test_authenticate_unknown_email_raises_same_error() -> None:
    """An unknown email raises the same AuthenticationFailed (no enumeration)."""
    provider = InMemoryIdentityProvider()
    with pytest.raises(AuthenticationFailed):
        provider.authenticate(Archetype.OPERATOR, "nobody@example.com", "pw")


def test_authenticate_wrong_archetype_fails() -> None:
    """Authenticating against the wrong archetype fails — boundaries are scoped."""
    provider = InMemoryIdentityProvider()
    provider.register(Archetype.OPERATOR, "scoped@example.com", "pw")
    with pytest.raises(AuthenticationFailed):
        provider.authenticate(Archetype.BRAND_CUSTOMER, "scoped@example.com", "pw")


def test_disabled_account_cannot_authenticate() -> None:
    """A disabled account raises AccountDisabled on authentication."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "off@example.com", "pw")
    provider.disable_account(user.user_id)
    with pytest.raises(AccountDisabled):
        provider.authenticate(Archetype.OPERATOR, "off@example.com", "pw")


def test_email_verification_is_recorded() -> None:
    """mark_email_verified flips the verified flag."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "v@example.com", "pw")
    updated = provider.mark_email_verified(user.user_id)
    assert updated.email_verified is True


def test_change_password_invalidates_old_password() -> None:
    """After a password change, only the new password authenticates."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "cp@example.com", "old-pw")
    provider.change_password(user.user_id, "new-pw")
    with pytest.raises(AuthenticationFailed):
        provider.authenticate(Archetype.OPERATOR, "cp@example.com", "old-pw")
    assert provider.authenticate(
        Archetype.OPERATOR, "cp@example.com", "new-pw"
    ).user_id == user.user_id


def test_session_lifecycle() -> None:
    """A session is issued, retrievable, then revoked."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "s@example.com", "pw")
    session = provider.issue_session(user.user_id, device_fingerprint="fp-1")
    assert session.device_fingerprint == "fp-1"
    assert provider.get_session(session.session_id).session_id == session.session_id
    provider.revoke_session(session.session_id)
    with pytest.raises(SessionRevoked):
        provider.get_session(session.session_id)


def test_unknown_session_raises_not_found() -> None:
    """Fetching an unknown session id raises SessionNotFound."""
    provider = InMemoryIdentityProvider()
    with pytest.raises(SessionNotFound):
        provider.get_session("no-such-session")


def test_revoke_session_is_idempotent() -> None:
    """Revoking an unknown or already-revoked session does not raise."""
    provider = InMemoryIdentityProvider()
    provider.revoke_session("unknown")  # no raise
    user = provider.register(Archetype.OPERATOR, "idem@example.com", "pw")
    session = provider.issue_session(user.user_id)
    provider.revoke_session(session.session_id)
    provider.revoke_session(session.session_id)  # second revoke, no raise


def test_expired_session_is_rejected() -> None:
    """A session past its TTL is treated as revoked."""
    provider = InMemoryIdentityProvider(session_ttl=datetime.timedelta(seconds=0))
    user = provider.register(Archetype.OPERATOR, "exp@example.com", "pw")
    session = provider.issue_session(user.user_id)
    with pytest.raises(SessionRevoked):
        provider.get_session(session.session_id)


def test_revoke_all_sessions() -> None:
    """revoke_all_sessions ends every live session for a user."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "all@example.com", "pw")
    first = provider.issue_session(user.user_id)
    second = provider.issue_session(user.user_id)
    provider.revoke_all_sessions(user.user_id)
    for session in (first, second):
        with pytest.raises(SessionRevoked):
            provider.get_session(session.session_id)


def test_totp_enrolment_and_verification() -> None:
    """Enrolling TOTP then verifying a current code completes enrolment."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "totp@example.com", "pw")
    enrolment = provider.enrol_totp(user.user_id)
    assert enrolment.secret
    assert provider.get_user(user.user_id).totp_enrolled is False
    assert provider.verify_totp(user.user_id, current_totp(enrolment.secret)) is True
    assert provider.get_user(user.user_id).totp_enrolled is True


def test_verify_totp_before_enrolment_raises() -> None:
    """Verifying TOTP before enrolment raises TotpNotEnrolled."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "noenrol@example.com", "pw")
    with pytest.raises(TotpNotEnrolled):
        provider.verify_totp(user.user_id, "123456")


def test_double_enrolment_raises() -> None:
    """Enrolling TOTP twice raises TotpAlreadyEnrolled."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "double@example.com", "pw")
    enrolment = provider.enrol_totp(user.user_id)
    provider.verify_totp(user.user_id, current_totp(enrolment.secret))
    with pytest.raises(TotpAlreadyEnrolled):
        provider.enrol_totp(user.user_id)


def test_wrong_totp_code_does_not_complete_enrolment() -> None:
    """A wrong code returns False and leaves enrolment incomplete."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "wrong@example.com", "pw")
    enrolment = provider.enrol_totp(user.user_id)
    current = current_totp(enrolment.secret)
    wrong = "000000" if current != "000000" else "111111"
    assert provider.verify_totp(user.user_id, wrong) is False
    assert provider.get_user(user.user_id).totp_enrolled is False


def test_disable_account_revokes_sessions() -> None:
    """Disabling an account revokes its live sessions."""
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "dis@example.com", "pw")
    session = provider.issue_session(user.user_id)
    provider.disable_account(user.user_id)
    with pytest.raises(SessionRevoked):
        provider.get_session(session.session_id)


def test_totp_secret_field_suppressed_in_enrolment_repr() -> None:
    """The ``secret`` field is suppressed from the TotpEnrolment repr.

    The ``provisioning_uri`` necessarily embeds the secret as a query
    parameter — an authenticator app must read it — so the secret string
    appears there. What ``repr=False`` guarantees is that the dedicated
    ``secret`` *field* is not dumped, so a record-level log or debugger dump
    does not surface the secret as a labelled attribute.
    """
    provider = InMemoryIdentityProvider()
    user = provider.register(Archetype.OPERATOR, "repr@example.com", "pw")
    enrolment = provider.enrol_totp(user.user_id)
    assert "secret=" + repr(enrolment.secret) not in repr(enrolment)
    assert "secret=" not in repr(enrolment).split("provisioning_uri")[0]
