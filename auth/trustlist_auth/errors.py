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

"""Exception hierarchy for the TrustList authentication library.

Every error the library raises descends from :class:`AuthError`, so an
application can catch the whole library surface with a single ``except``.

A deliberate security note on :class:`AuthenticationFailed`: the library raises
the *same* exception type, with the *same* message, whether authentication
failed because the email was unknown or because the password was wrong. Leaking
which of the two happened would hand an attacker a user-enumeration oracle.
"""

from __future__ import annotations


class AuthError(Exception):
    """Base class for every error raised by the authentication library."""


class AuthenticationFailed(AuthError):
    """Raised when a credential check fails.

    Carries no detail of *why* it failed — see the module docstring. Callers
    should surface a generic "invalid email or password" message to the user.
    """

    def __init__(self, message: str = "authentication failed") -> None:
        """Initialise with a deliberately uninformative default message."""
        super().__init__(message)


class AccountDisabled(AuthError):
    """Raised when authentication is attempted against a disabled account."""


class EmailNotVerified(AuthError):
    """Raised when an operation requires a verified email and it is not."""


class SessionNotFound(AuthError):
    """Raised when a session id does not resolve to a live session."""


class SessionRevoked(AuthError):
    """Raised when a session exists but has been revoked or has expired."""


class TotpError(AuthError):
    """Base class for TOTP-related failures."""


class TotpNotEnrolled(TotpError):
    """Raised when a TOTP operation is attempted before enrolment."""


class TotpAlreadyEnrolled(TotpError):
    """Raised when TOTP enrolment is attempted on an already-enrolled account."""


class TotpVerificationFailed(TotpError):
    """Raised when a supplied TOTP code does not verify."""


class TokenError(AuthError):
    """Base class for verification-token failures."""


class TokenInvalid(TokenError):
    """Raised when a verification token is malformed or its signature fails."""


class TokenExpired(TokenError):
    """Raised when a verification token is well-formed but past its expiry."""


class UnknownRole(AuthError):
    """Raised when a role identifier is not in an archetype's role catalogue."""


class UnknownPermission(AuthError):
    """Raised when a permission identifier is not in the permission model."""


class RoleScopeError(AuthError):
    """Raised when a role grant violates the archetype's scoping rules.

    For example, a brand-customer grant made without a ``customer_id``, or a
    non-brand-customer grant made *with* one — mirroring the database CHECK
    constraint on ``user_role_assignment``.
    """
