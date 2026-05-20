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

"""Password hashing for TrustList accounts — Argon2id.

This module is the single place the library hashes and verifies passwords. It
uses Argon2id (the hybrid Argon2 variant, resistant to both side-channel and
GPU attacks) via ``argon2-cffi``, with parameters at or above the OWASP
Password Storage Cheat Sheet recommendation for Argon2id.

Security discipline.

* The library never stores, logs or returns a plaintext password. A plaintext
  password lives only inside the argument to :func:`hash_password` /
  :func:`verify_password` and is discarded the moment the call returns.
* The hash produced is a PHC-format string that embeds the algorithm, the
  parameters and a per-password random salt. It is safe to persist verbatim;
  ``user_account.password_hash`` is a ``bytea`` column, so callers encode the
  string to UTF-8 bytes for storage and decode on read.
* :func:`needs_rehash` lets a login path transparently upgrade a stored hash
  when the cost parameters are raised in a later release.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2 import exceptions as argon2_exceptions
from argon2.profiles import RFC_9106_HIGH_MEMORY

# The shared hasher. ``RFC_9106_HIGH_MEMORY`` is the argon2-cffi profile that
# matches the first option of RFC 9106 §4 (2 GiB memory, t=1, p=4) — comfortably
# above the OWASP minimum (19 MiB, t=2, p=1) for a server-side credential store.
# Using a named profile rather than hand-tuned integers keeps the parameter
# choice auditable and lets the dependency track the standard.
_HASHER = PasswordHasher.from_parameters(RFC_9106_HIGH_MEMORY)


def hash_password(plaintext: str) -> str:
    """Hash ``plaintext`` with Argon2id and return a PHC-format hash string.

    The returned string embeds the algorithm, the cost parameters and a fresh
    random salt; it is what gets persisted to ``user_account.password_hash``.
    Two calls with the same input return different strings — the salt differs.

    :param plaintext: the password to hash. Never logged or retained.
    :returns: a self-describing PHC hash string, safe to store verbatim.
    """
    return _HASHER.hash(plaintext)


def verify_password(stored_hash: str, plaintext: str) -> bool:
    """Return ``True`` iff ``plaintext`` matches ``stored_hash``.

    The comparison is constant-time within argon2-cffi. A mismatch returns
    ``False`` rather than raising, so a caller can treat the boolean as the
    single decision point. A malformed ``stored_hash`` also returns ``False``:
    an unparseable hash can never be a correct password and must not crash the
    login path.

    :param stored_hash: the PHC hash string previously produced by
        :func:`hash_password`.
    :param plaintext: the candidate password. Never logged or retained.
    """
    try:
        return _HASHER.verify(stored_hash, plaintext)
    except argon2_exceptions.VerifyMismatchError:
        return False
    except (argon2_exceptions.VerificationError, argon2_exceptions.InvalidHashError):
        # A malformed or unparseable stored hash cannot match any password.
        # ``InvalidHashError`` is a ``ValueError`` rather than a
        # ``VerificationError`` subclass, so it is caught explicitly.
        return False


def needs_rehash(stored_hash: str) -> bool:
    """Return ``True`` when ``stored_hash`` was made with weaker parameters.

    A login path should call this after a successful :func:`verify_password`
    and, when it returns ``True``, re-hash the (already verified) plaintext and
    persist the new hash. This upgrades stored credentials transparently when
    the cost profile is raised in a later release.
    """
    return _HASHER.check_needs_rehash(stored_hash)
