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

"""Unit tests for the audit-event sinks and the ``auth.audit`` mirror seam.

These exercise the sink half of :mod:`trustlist_auth.audit` with no database;
the database write is exercised by the integration tests.
"""

from __future__ import annotations

import datetime

from trustlist_auth.audit import (
    AUTH_AUDIT_TOPIC,
    AuditEvent,
    AuditEventSink,
    AuthEventType,
    LoggingAuditEventSink,
    NullAuditEventSink,
)
from trustlist_auth.testing import RecordingAuditEventSink


def _event() -> AuditEvent:
    """A sample audit event."""
    return AuditEvent(
        event_id="evt-1",
        event_type=AuthEventType.LOGIN_SUCCESS,
        occurred_at=datetime.datetime(2026, 5, 20, tzinfo=datetime.UTC),
        user_id="user-1",
    )


def test_auth_audit_topic_constant() -> None:
    """The mirror seam targets the §7b ``auth.audit`` topic."""
    assert AUTH_AUDIT_TOPIC == "auth.audit"


def test_null_sink_satisfies_protocol_and_discards() -> None:
    """The null sink conforms to the protocol and silently discards."""
    sink = NullAuditEventSink()
    assert isinstance(sink, AuditEventSink)
    sink.emit(_event())  # no raise, no observable effect


def test_logging_sink_satisfies_protocol() -> None:
    """The logging sink conforms to the protocol and does not raise."""
    sink = LoggingAuditEventSink()
    assert isinstance(sink, AuditEventSink)
    sink.emit(_event())


def test_recording_sink_retains_events() -> None:
    """The recording harness sink retains every event it receives."""
    sink = RecordingAuditEventSink()
    assert isinstance(sink, AuditEventSink)
    sink.emit(_event())
    sink.emit(_event())
    assert len(sink.events) == 2
    assert sink.event_types() == ["login_success", "login_success"]


def test_recording_sink_clear() -> None:
    """The recording sink can be cleared between assertions."""
    sink = RecordingAuditEventSink()
    sink.emit(_event())
    sink.clear()
    assert sink.events == []
