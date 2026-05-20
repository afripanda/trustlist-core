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

"""Exported test utilities for the authentication library.

PRD §7e calls for a "trust-boundary test harness" — exported test utilities
that the Stage-0 trust-boundary isolation test (issue 23) uses to assert that a
Foundation-side connection cannot read commercial-entity data, and vice versa.

This module is that harness. It is shipped *inside* the library — not under
``tests/`` — precisely so a *different* test suite (issue 23, in its own file)
can import and reuse it. Everything here is deterministic, in-memory and
dependency-free; none of it touches a network or a real database.

Two utilities:

* :class:`RecordingAuditEventSink` — an :class:`~trustlist_auth.audit.AuditEventSink`
  that retains every event it receives, so a test can assert that the
  ``auth.audit`` mirror was driven for a given action.
* :func:`build_in_memory_service` — constructs an :class:`AuthService` over the
  in-memory provider and a recording sink, the standard fixture for exercising
  the library with no backing services.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from trustlist_auth.audit import AuditEvent, AuditTrail
from trustlist_auth.fake_provider import InMemoryIdentityProvider
from trustlist_auth.role_store import RoleStore
from trustlist_auth.service import AuthService


@dataclass
class RecordingAuditEventSink:
    """An :class:`AuditEventSink` that retains every event for later assertion.

    Conforms structurally to the :class:`~trustlist_auth.audit.AuditEventSink`
    protocol. A test wires one of these in place of the Stage-0 null sink and
    then inspects :attr:`events` to confirm that the ``auth.audit`` mirror was
    driven — which is how the audit-trail tests assert "every auth event writes
    a row *and* emits a matching event".
    """

    events: list[AuditEvent] = field(default_factory=list)

    def emit(self, event: AuditEvent) -> None:
        """Retain ``event``. Never raises, as the sink contract requires."""
        self.events.append(event)

    def clear(self) -> None:
        """Discard all retained events."""
        self.events.clear()

    def event_types(self) -> list[str]:
        """Return the ``event_type`` value of every retained event, in order."""
        return [event.event_type.value for event in self.events]


@dataclass(frozen=True)
class InMemoryHarness:
    """A composed in-memory :class:`AuthService` plus the parts behind it.

    Returned by :func:`build_in_memory_service` so a test can reach the
    provider and the recording sink directly when it needs to.
    """

    service: AuthService
    provider: InMemoryIdentityProvider
    sink: RecordingAuditEventSink


def build_in_memory_service() -> InMemoryHarness:
    """Build an :class:`AuthService` over the in-memory provider for tests.

    The returned :class:`InMemoryHarness` bundles the service, its in-memory
    provider and a :class:`RecordingAuditEventSink`. This is the canonical
    fixture for exercising the library — and the per-archetype surfaces of
    issues 17–19 — with no Postgres and no Clerk.
    """
    provider = InMemoryIdentityProvider()
    sink = RecordingAuditEventSink()
    service = AuthService(
        provider=provider,
        audit_trail=AuditTrail(sink),
        role_store=RoleStore(),
    )
    return InMemoryHarness(service=service, provider=provider, sink=sink)
