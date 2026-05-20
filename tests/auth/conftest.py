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

"""Fixtures for the auth-library integration tests.

The integration tests run against a *real* migrated Postgres — no mocks, per
PRD §7e — reusing the ``engine`` / ``connection`` fixtures from the top-level
``tests/conftest.py``. The fixtures here add the auth-specific composition: an
:class:`AuthService` over the in-memory identity provider and a recording audit
sink, so a test can assert both the ``auth_audit_event`` database row and the
``auth.audit`` mirror in one place.

The identity provider stays in-memory even for the integration tests: the
provider's own state (sessions, TOTP) is exercised exhaustively by the unit
tests, and there is no real Clerk account to integrate against (ADR-0014; the
Clerk adapter lands in issues 17–19). What the integration tests verify is the
*canonical-store* side — ``user_account``, ``user_session``,
``user_role_assignment`` and ``auth_audit_event`` — which is real Postgres.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from trustlist_auth.testing import InMemoryHarness, build_in_memory_service


@pytest.fixture
def harness() -> Iterator[InMemoryHarness]:
    """Yield a freshly composed in-memory auth harness for each test."""
    yield build_in_memory_service()
