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

"""Signed, expiring verification tokens — email verification and the like.

A verification token proves that whoever holds it received a one-time link the
Foundation sent to a particular email address. The token is a self-contained,
HMAC-signed string: the server stores no per-token state, and tampering or
expiry are detected purely from the token itself.

Token shape — three dot-separated base64url segments::

    <payload-b64>.<expiry-b64>.<signature-b64>

* ``payload`` — the subject the token vouches for (a ``user_id`` and the
  ``email`` being verified), JSON-encoded.
* ``expiry`` — the Unix expiry timestamp, as text.
* ``signature`` — ``HMAC-SHA256(secret, payload-b64 + "." + expiry-b64)``.

Security discipline.

* The signing secret is sensitive. It is supplied by the caller (resolved from
  AWS Secrets Manager at start-up per PRD §7g) — never hard-coded, never
  defaulted. :class:`TokenSigner` refuses an empty secret.
* Signature comparison is constant-time (:func:`hmac.compare_digest`), so a
  forged token leaks no timing oracle.
* The token carries no secret of its own: it is a *capability*, not a
  credential. A leaked token grants only the bounded action it encodes, and
  only until it expires.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
from dataclasses import dataclass

from trustlist_auth.errors import TokenExpired, TokenInvalid

# How long an email-verification token stays valid by default. Twenty-four
# hours is the usual industry choice — long enough for a user to act on the
# email, short enough to bound the window of a leaked link.
DEFAULT_EMAIL_TOKEN_TTL = datetime.timedelta(hours=24)


@dataclass(frozen=True)
class VerificationClaim:
    """The subject a verification token vouches for.

    :param user_id: the account the token concerns.
    :param email: the email address being verified — bound into the token so a
        token issued for one address cannot verify a different one.
    """

    user_id: str
    email: str


def _b64encode(raw: bytes) -> str:
    """URL-safe base64-encode ``raw`` with no padding."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(text: str) -> bytes:
    """Decode a URL-safe, unpadded base64 string back to bytes."""
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class TokenSigner:
    """Issues and verifies HMAC-signed, expiring verification tokens.

    A single signer is constructed once at start-up with the signing secret and
    reused for the process lifetime.
    """

    def __init__(self, secret: bytes) -> None:
        """Bind the signer to ``secret``.

        :param secret: the HMAC signing key, resolved from the secrets store.
            Must be non-empty — an empty key would make every signature
            forgeable.
        :raises ValueError: if ``secret`` is empty.
        """
        if not secret:
            raise ValueError("token signing secret must not be empty")
        self._secret = secret

    def issue_email_verification_token(
        self,
        claim: VerificationClaim,
        *,
        ttl: datetime.timedelta = DEFAULT_EMAIL_TOKEN_TTL,
        now: datetime.datetime | None = None,
    ) -> str:
        """Issue a signed email-verification token for ``claim``.

        :param claim: the user id and email the token vouches for.
        :param ttl: how long the token stays valid. Defaults to 24 hours.
        :param now: the issuing instant; defaults to the current UTC time.
            Exposed so tests can pin time deterministically.
        :returns: the encoded ``payload.expiry.signature`` token string.
        """
        moment = now or datetime.datetime.now(tz=datetime.UTC)
        expiry = moment + ttl

        payload = json.dumps(
            {"user_id": claim.user_id, "email": claim.email},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        payload_b64 = _b64encode(payload)
        expiry_b64 = _b64encode(str(int(expiry.timestamp())).encode("ascii"))

        signature = self._sign(payload_b64, expiry_b64)
        return f"{payload_b64}.{expiry_b64}.{signature}"

    def verify_email_verification_token(
        self,
        token: str,
        *,
        now: datetime.datetime | None = None,
    ) -> VerificationClaim:
        """Verify ``token`` and return the claim it carries.

        :param token: the token string previously issued by
            :meth:`issue_email_verification_token`.
        :param now: the instant to check expiry against; defaults to the
            current UTC time. Exposed for deterministic tests.
        :returns: the :class:`VerificationClaim` the token vouches for.
        :raises TokenInvalid: if the token is malformed or its signature fails.
        :raises TokenExpired: if the token is well-formed but past its expiry.

        The signature is checked *before* the expiry: an expired-but-genuine
        token and a forged token are different conditions, and an attacker must
        not learn anything from the order of checks.
        """
        parts = token.split(".")
        if len(parts) != 3:
            raise TokenInvalid("token is not three dot-separated segments")
        payload_b64, expiry_b64, signature = parts

        expected = self._sign(payload_b64, expiry_b64)
        if not hmac.compare_digest(expected, signature):
            raise TokenInvalid("token signature does not verify")

        try:
            expiry_raw = _b64decode(expiry_b64).decode("ascii")
            expiry_ts = int(expiry_raw)
            payload = json.loads(_b64decode(payload_b64))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TokenInvalid("token payload is malformed") from exc

        moment = now or datetime.datetime.now(tz=datetime.UTC)
        if moment.timestamp() > expiry_ts:
            raise TokenExpired("token has expired")

        if not isinstance(payload, dict):
            raise TokenInvalid("token payload is not an object")
        user_id = payload.get("user_id")
        email = payload.get("email")
        if not isinstance(user_id, str) or not isinstance(email, str):
            raise TokenInvalid("token payload is missing required fields")

        return VerificationClaim(user_id=user_id, email=email)

    def _sign(self, payload_b64: str, expiry_b64: str) -> str:
        """Return the base64url HMAC-SHA256 signature over the signed portion."""
        message = f"{payload_b64}.{expiry_b64}".encode("ascii")
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        return _b64encode(digest)
