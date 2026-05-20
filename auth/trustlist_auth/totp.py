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

"""TOTP-based two-factor authentication — RFC 6238.

TrustList mandates TOTP-based 2FA across all three archetypes at Stage 0
(PRD §7e). This module is the cross-archetype TOTP primitive: it generates the
shared secret, renders the provisioning URI an authenticator app scans, and
verifies a user-supplied code.

Parameters are fixed at the RFC 6238 defaults that authenticator apps (Google
Authenticator, Authy, 1Password and the rest) universally support: a 30-second
time step, 6-digit codes, SHA-1 as the HMAC primitive. These are *interop*
constants, not security knobs — changing them would break every enrolled
authenticator.

Security discipline.

* The TOTP secret is sensitive key material — equivalent to a password. It is
  generated here, handed once to the caller (to render the QR code and to
  persist encrypted-at-rest in ``user_account.totp_secret``), and never logged.
* :func:`verify_totp` accepts a small ``valid_window`` so a code from the
  immediately-adjacent time step still verifies, absorbing clock skew between
  the server and the user's device. The window is deliberately tight (one step
  either side) to keep the replay surface minimal.
"""

from __future__ import annotations

import pyotp

# RFC 6238 interop constants. Authenticator apps assume these defaults; they are
# not tunable without breaking every enrolled device.
TOTP_DIGITS = 6
TOTP_INTERVAL_SECONDS = 30

# How many adjacent 30-second steps either side of "now" still verify. One step
# absorbs ordinary device/server clock skew; widening it enlarges the replay
# window, so it is held at the minimum that is still usable.
_DEFAULT_VALID_WINDOW = 1

# The issuer label shown beside the account in an authenticator app.
_ISSUER_NAME = "TrustList"


def generate_totp_secret() -> str:
    """Return a fresh base32-encoded TOTP secret.

    The secret is sensitive key material. The caller persists it (encrypted at
    rest) in ``user_account.totp_secret`` and renders it — typically as a QR
    code via :func:`provisioning_uri` — exactly once, at enrolment.
    """
    return pyotp.random_base32()


def provisioning_uri(secret: str, account_label: str) -> str:
    """Return the ``otpauth://`` URI an authenticator app scans at enrolment.

    :param secret: the base32 secret from :func:`generate_totp_secret`.
    :param account_label: a human-readable account identifier — typically the
        user's email — shown in the authenticator app.
    :returns: an ``otpauth://totp/...`` URI carrying the issuer, the label and
        the secret.
    """
    return _totp(secret).provisioning_uri(name=account_label, issuer_name=_ISSUER_NAME)


def current_totp(secret: str) -> str:
    """Return the TOTP code valid *right now* for ``secret``.

    This is a test and tooling helper — it lets a test or an integration
    fixture compute the code the user's authenticator would currently display.
    Production login paths never call it; they receive the code from the user.
    """
    return _totp(secret).now()


def verify_totp(
    secret: str,
    code: str,
    *,
    valid_window: int = _DEFAULT_VALID_WINDOW,
) -> bool:
    """Return ``True`` iff ``code`` is a currently-valid TOTP code for ``secret``.

    :param secret: the user's base32 TOTP secret.
    :param code: the 6-digit code the user supplied. Whitespace is tolerated;
        anything non-numeric simply fails to verify.
    :param valid_window: how many adjacent 30-second steps either side of "now"
        are also accepted, absorbing clock skew. Defaults to one.

    The comparison inside ``pyotp`` is constant-time. A malformed code returns
    ``False`` rather than raising — an unparseable code can never be valid.
    """
    candidate = code.strip().replace(" ", "")
    if not candidate.isdigit():
        return False
    return _totp(secret).verify(candidate, valid_window=valid_window)


def _totp(secret: str) -> pyotp.TOTP:
    """Construct a :class:`pyotp.TOTP` bound to the fixed RFC 6238 parameters."""
    return pyotp.TOTP(secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL_SECONDS)
