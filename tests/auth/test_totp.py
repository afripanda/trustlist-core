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

"""Unit tests for RFC 6238 TOTP 2FA (:mod:`trustlist_auth.totp`)."""

from __future__ import annotations

from trustlist_auth.totp import (
    TOTP_DIGITS,
    TOTP_INTERVAL_SECONDS,
    current_totp,
    generate_totp_secret,
    provisioning_uri,
    verify_totp,
)


def test_rfc6238_parameters_are_the_interop_defaults() -> None:
    """The library fixes TOTP to 6-digit codes and a 30-second step."""
    assert TOTP_DIGITS == 6
    assert TOTP_INTERVAL_SECONDS == 30


def test_generated_secret_is_non_empty_base32() -> None:
    """A generated secret is a non-empty base32 string."""
    secret = generate_totp_secret()
    assert secret
    # base32 alphabet is A–Z and 2–7.
    assert all(char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for char in secret)


def test_generated_secrets_are_distinct() -> None:
    """Two generated secrets differ."""
    assert generate_totp_secret() != generate_totp_secret()


def test_current_code_verifies() -> None:
    """The code valid right now verifies against its secret."""
    secret = generate_totp_secret()
    assert verify_totp(secret, current_totp(secret)) is True


def test_wrong_code_is_rejected() -> None:
    """A code that is not the current one does not verify."""
    secret = generate_totp_secret()
    current = current_totp(secret)
    wrong = "000000" if current != "000000" else "111111"
    assert verify_totp(secret, wrong) is False


def test_code_from_different_secret_is_rejected() -> None:
    """A valid code for one secret does not verify against another."""
    secret_a = generate_totp_secret()
    secret_b = generate_totp_secret()
    assert verify_totp(secret_b, current_totp(secret_a)) is False


def test_code_is_six_digits() -> None:
    """A current code is exactly six decimal digits."""
    code = current_totp(generate_totp_secret())
    assert len(code) == TOTP_DIGITS
    assert code.isdigit()


def test_whitespace_in_code_is_tolerated() -> None:
    """A code with surrounding or interior spaces still verifies."""
    secret = generate_totp_secret()
    code = current_totp(secret)
    spaced = f" {code[:3]} {code[3:]} "
    assert verify_totp(secret, spaced) is True


def test_non_numeric_code_is_rejected_without_raising() -> None:
    """A non-numeric code returns False rather than raising."""
    secret = generate_totp_secret()
    assert verify_totp(secret, "abcdef") is False
    assert verify_totp(secret, "") is False


def test_provisioning_uri_carries_issuer_and_label() -> None:
    """The provisioning URI is an otpauth URI carrying the issuer and label."""
    secret = generate_totp_secret()
    uri = provisioning_uri(secret, "alice@example.com")
    assert uri.startswith("otpauth://totp/")
    assert "issuer=TrustList" in uri
    assert "alice%40example.com" in uri


def test_provisioning_uri_embeds_the_secret() -> None:
    """The provisioning URI embeds the shared secret for the authenticator app."""
    secret = generate_totp_secret()
    uri = provisioning_uri(secret, "bob@example.com")
    assert f"secret={secret}" in uri
