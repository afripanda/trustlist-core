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

"""Unit tests for trace-context carried through the event envelope.

These exercise the contract the SDK depends on: the W3C trace context injected
into an envelope's ``trace_context`` field by ``observability.inject_trace_context``
survives serialisation and re-parents a consumer-side span — joining the
producer's distributed trace (PRD §7b, ADR-0012).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from observability import (
    extract_trace_context,
    init_tracing,
    inject_trace_context,
)
from trustlist_event_bus.envelope import EventEnvelope, Provenance, new_envelope


def _reset_global_provider() -> None:
    """Clear OpenTelemetry's process-global tracer provider between tests."""
    once_cls = type(trace._TRACER_PROVIDER_SET_ONCE)
    trace._TRACER_PROVIDER_SET_ONCE = once_cls()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Yield an in-memory span exporter wired as the global tracer provider."""
    _reset_global_provider()
    exporter = InMemorySpanExporter()
    init_tracing("event-bus-sdk-test", exporter=exporter)
    yield exporter
    exporter.clear()
    _reset_global_provider()


def _tracer() -> trace.Tracer:
    """Return a tracer from the current provider."""
    return trace.get_tracer("trustlist.tests")


def test_envelope_carries_injected_trace_context(
    span_exporter: InMemorySpanExporter,
) -> None:
    """An envelope built inside a span carries a W3C traceparent."""
    with _tracer().start_as_current_span("producer"):
        carrier = inject_trace_context()
        envelope = new_envelope(
            event_type="signal.tier-one.example-collector",
            payload={},
            producer_id="collector-1",
            idempotency_key="k",
            provenance=Provenance(source="system", method="probe"),
            trace_context=carrier,
        )
    assert "traceparent" in envelope.trace_context
    assert envelope.trace_context["traceparent"].count("-") == 3


def test_trace_context_round_trips_through_the_envelope_wire_form(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A consumer span built from a serialised envelope joins the producer trace.

    This is the end-to-end §7b tracing contract, exercised without a broker:
    producer-side inject -> envelope bytes -> consumer-side decode -> extract
    -> child span. The child must share the producer's trace id.
    """
    # Producer side: open a span, inject context, build and serialise.
    with _tracer().start_as_current_span("producer") as producer_span:
        producer_trace_id = producer_span.get_span_context().trace_id
        envelope = new_envelope(
            event_type="signal.tier-one.example-collector",
            payload={"domain_id": "d1"},
            producer_id="collector-1",
            idempotency_key="k",
            provenance=Provenance(source="system", method="probe"),
            trace_context=inject_trace_context(),
        )
    wire = envelope.to_bytes()

    # Consumer side: decode, extract the parent context, open a child span.
    decoded = EventEnvelope.from_bytes(wire)
    parent = extract_trace_context(decoded.trace_context)
    with _tracer().start_as_current_span(
        "consumer", context=parent
    ) as consumer_span:
        consumer_trace_id = consumer_span.get_span_context().trace_id

    assert consumer_trace_id == producer_trace_id


def test_envelope_with_no_active_span_has_an_empty_carrier(
    span_exporter: InMemorySpanExporter,
) -> None:
    """With no active span, the envelope's trace_context is simply empty.

    The SDK must not fail to produce just because tracing was never started;
    the consumer then starts a fresh root trace.
    """
    envelope = new_envelope(
        event_type="signal.tier-one.example-collector",
        payload={},
        producer_id="collector-1",
        idempotency_key="k",
        provenance=Provenance(source="system", method="probe"),
        trace_context=inject_trace_context(),
    )
    assert envelope.trace_context == {}
    # Extracting from the empty carrier yields a non-remote (root) context.
    parent = extract_trace_context(envelope.trace_context)
    assert not trace.get_current_span(parent).get_span_context().is_valid
