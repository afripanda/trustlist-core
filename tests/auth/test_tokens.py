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

"""Unit tests for signed verification tokens (:mod:`trustlist_auth.tokens`)."""

from __future__ import annotations

import datetime

import pytest

from trustlist_auth.errors import TokenExpired, TokenInvalid
from trustlist_auth.tokens import TokenSigner, VerificationClaim

_SECRET = b"unit-test-signing-secret-not-a-real-key"
_OTHER_SECRET = b"a-different-signing-secret-entirely-xxx"


def _claim() -> VerificationClaim:
    """A sample verification claim."""
    return VerificationClaim(user_id="user-123", email="claim@example.com")


def test_empty_secret_is_rejected() -> None:
    """Constructing a signer with an empty secret raises."""
    with pytest.raises(ValueError, match="must not be empty"):
        TokenSigner(b"")


def test_issued_token_round_trips() -> None:
    """A freshly issued token verifies and yields back its claim."""
    signer = TokenSigner(_SECRET)
    token = signer.issue_email_verification_token(_claim())
    recovered = signer.verify_email_verification_token(token)
    assert recovered == _claim()


def test_token_is_three_segments() -> None:
    """A token is three dot-separated base64url segments."""
    token = TokenSigner(_SECRET).issue_email_verification_token(_claim())
    assert token.count(".") == 2


def test_tampered_payload_fails_verification() -> None:
    """Mutating the payload segment invalidates the signature."""
    signer = TokenSigner(_SECRET)
    token = signer.issue_email_verification_token(_claim())
    payload, expiry, signature = token.split(".")
    tampered = f"{payload}x.{expiry}.{signature}"
    with pytest.raises(TokenInvalid):
        signer.verify_email_verification_token(tampered)


def test_token_signed_with_other_secret_fails() -> None:
    """A token signed with a different secret does not verify."""
    issued = TokenSigner(_OTHER_SECRET).issue_email_verification_token(_claim())
    with pytest.raises(TokenInvalid):
        TokenSigner(_SECRET).verify_email_verification_token(issued)


def test_malformed_token_fails() -> None:
    """A token that is not three segments is rejected as malformed."""
    with pytest.raises(TokenInvalid):
        TokenSigner(_SECRET).verify_email_verification_token("not-a-token")


def test_expired_token_is_rejected() -> None:
    """A token verified after its expiry raises TokenExpired."""
    signer = TokenSigner(_SECRET)
    issued_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    token = signer.issue_email_verification_token(
        _claim(),
        ttl=datetime.timedelta(hours=1),
        now=issued_at,
    )
    later = issued_at + datetime.timedelta(hours=2)
    with pytest.raises(TokenExpired):
        signer.verify_email_verification_token(token, now=later)


def test_token_valid_within_ttl() -> None:
    """A token verified within its TTL is accepted."""
    signer = TokenSigner(_SECRET)
    issued_at = datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)
    token = signer.issue_email_verification_token(
        _claim(),
        ttl=datetime.timedelta(hours=24),
        now=issued_at,
    )
    within = issued_at + datetime.timedelta(hours=23)
    assert signer.verify_email_verification_token(token, now=within) == _claim()


def test_signing_secret_does_not_appear_in_token() -> None:
    """The signing secret is never embedded in the token string."""
    token = TokenSigner(_SECRET).issue_email_verification_token(_claim())
    assert _SECRET.decode("ascii") not in token
