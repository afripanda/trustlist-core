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

"""Unit tests for the typed event envelope (PRD §7b).

These tests need no broker and no registry — the envelope is deliberately
dependency-free.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from trustlist_event_bus.envelope import (
    ENVELOPE_VERSION,
    EventEnvelope,
    Provenance,
    new_envelope,
)

# The nine §7b envelope fields. The envelope must carry exactly these.
_SECTION_7B_FIELDS = {
    "event_id",
    "event_type",
    "event_version",
    "produced_at",
    "producer_id",
    "trace_context",
    "idempotency_key",
    "provenance",
    "payload",
}


def _provenance() -> Provenance:
    """Return a representative provenance object."""
    return Provenance(source="system", method="dns-lookup")


def test_new_envelope_carries_every_section_7b_field() -> None:
    """A constructed envelope serialises to exactly the nine §7b fields."""
    envelope = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={"domain_id": str(uuid.uuid4())},
        producer_id="collector-1",
        idempotency_key="abc123",
        provenance=_provenance(),
    )
    assert set(envelope.to_dict()) == _SECTION_7B_FIELDS


def test_new_envelope_generates_event_id_and_timestamp() -> None:
    """``event_id`` and ``produced_at`` are generated when not supplied."""
    before = datetime.now(tz=UTC)
    envelope = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={},
        producer_id="collector-1",
        idempotency_key="k",
        provenance=_provenance(),
    )
    after = datetime.now(tz=UTC)
    assert isinstance(envelope.event_id, uuid.UUID)
    assert before <= envelope.produced_at <= after
    assert envelope.produced_at.tzinfo is not None


def test_new_envelope_honours_explicit_event_id_and_timestamp() -> None:
    """Explicit ``event_id`` / ``produced_at`` make a produce reproducible."""
    fixed_id = uuid.UUID("00000000-0000-0000-0000-000000000001")
    fixed_time = datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
    envelope = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={},
        producer_id="collector-1",
        idempotency_key="k",
        provenance=_provenance(),
        event_id=fixed_id,
        produced_at=fixed_time,
    )
    assert envelope.event_id == fixed_id
    assert envelope.produced_at == fixed_time


def test_new_envelope_rejects_naive_timestamp() -> None:
    """A timezone-naive ``produced_at`` is refused — the field is a tstamptz."""
    with pytest.raises(ValueError, match="timezone-aware"):
        new_envelope(
            event_type="signal.tier-one.example-collector",
            payload={},
            producer_id="collector-1",
            idempotency_key="k",
            provenance=_provenance(),
            produced_at=datetime(2026, 5, 20, 12, 0, 0),  # noqa: DTZ001
        )


def test_new_envelope_defaults_event_version_to_envelope_version() -> None:
    """``event_version`` defaults to the envelope version when unspecified."""
    envelope = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={},
        producer_id="collector-1",
        idempotency_key="k",
        provenance=_provenance(),
    )
    assert envelope.event_version == ENVELOPE_VERSION


def test_envelope_round_trips_through_bytes() -> None:
    """An envelope survives a to_bytes / from_bytes round-trip unchanged."""
    original = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={"domain_id": "d1", "value": 7},
        producer_id="collector-1",
        idempotency_key="abc123",
        provenance=Provenance(
            source="contributor",
            method="manual",
            contributor_identity="analyst@example.org",
        ),
        trace_context={"traceparent": "00-" + "0" * 32 + "-" + "0" * 16 + "-01"},
    )
    restored = EventEnvelope.from_bytes(original.to_bytes())

    assert restored.event_id == original.event_id
    assert restored.event_type == original.event_type
    assert restored.event_version == original.event_version
    assert restored.produced_at == original.produced_at
    assert restored.producer_id == original.producer_id
    assert restored.trace_context == original.trace_context
    assert restored.idempotency_key == original.idempotency_key
    assert restored.provenance == original.provenance
    assert restored.payload == original.payload


def test_envelope_is_immutable() -> None:
    """The envelope is frozen — fields cannot be reassigned after construction."""
    envelope = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={},
        producer_id="collector-1",
        idempotency_key="k",
        provenance=_provenance(),
    )
    with pytest.raises((AttributeError, TypeError)):
        envelope.producer_id = "tampered"  # type: ignore[misc]


def test_from_dict_rejects_an_envelope_missing_a_required_field() -> None:
    """Decoding an envelope missing a §7b field is a hard error."""
    good = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={},
        producer_id="collector-1",
        idempotency_key="k",
        provenance=_provenance(),
    ).to_dict()
    del good["idempotency_key"]
    with pytest.raises(KeyError):
        EventEnvelope.from_dict(good)


def test_provenance_round_trips_with_and_without_contributor() -> None:
    """Provenance survives a dict round-trip in both shapes."""
    system = Provenance(source="system", method="probe")
    assert Provenance.from_dict(system.to_dict()) == system

    human = Provenance(
        source="human", method="review", contributor_identity="rev@example.org"
    )
    assert Provenance.from_dict(human.to_dict()) == human
