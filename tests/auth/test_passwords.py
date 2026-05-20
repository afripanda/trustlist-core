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

"""Unit tests for Argon2id password hashing (:mod:`trustlist_auth.passwords`)."""

from __future__ import annotations

from trustlist_auth.passwords import hash_password, needs_rehash, verify_password


def test_hash_is_argon2id() -> None:
    """A produced hash is a PHC string identifying the Argon2id variant."""
    digest = hash_password("correct horse battery staple")
    assert digest.startswith("$argon2id$")


def test_hash_is_salted_and_non_deterministic() -> None:
    """Hashing the same password twice yields different strings (random salt)."""
    first = hash_password("same-password")
    second = hash_password("same-password")
    assert first != second


def test_verify_accepts_correct_password() -> None:
    """The correct password verifies against its own hash."""
    digest = hash_password("s3cret-passphrase")
    assert verify_password(digest, "s3cret-passphrase") is True


def test_verify_rejects_wrong_password() -> None:
    """An incorrect password does not verify."""
    digest = hash_password("the-real-password")
    assert verify_password(digest, "not-the-password") is False


def test_verify_rejects_malformed_hash_without_raising() -> None:
    """A malformed stored hash returns False rather than raising."""
    assert verify_password("not-a-valid-phc-hash", "anything") is False


def test_plaintext_does_not_appear_in_hash() -> None:
    """The plaintext password is not embedded in the hash output."""
    plaintext = "very-unique-plaintext-marker-9173"
    digest = hash_password(plaintext)
    assert plaintext not in digest


def test_needs_rehash_is_false_for_current_parameters() -> None:
    """A hash made with the current profile does not need rehashing."""
    digest = hash_password("current-params")
    assert needs_rehash(digest) is False


def test_unicode_password_round_trips() -> None:
    """A non-ASCII password hashes and verifies correctly."""
    plaintext = "пароль-密码-🔐"
    digest = hash_password(plaintext)
    assert verify_password(digest, plaintext) is True
    assert verify_password(digest, "wrong") is False
