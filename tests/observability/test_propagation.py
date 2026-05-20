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

"""Tests for W3C trace-context propagation (event-envelope carrier helpers)."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from observability.propagation import extract_trace_context, inject_trace_context


def _tracer() -> trace.Tracer:
    """Return a tracer from the current provider (re-resolved per call)."""
    return trace.get_tracer("trustlist.tests")


def test_inject_into_a_fresh_carrier_writes_traceparent(
    span_exporter: InMemorySpanExporter,
) -> None:
    """Injecting inside a span writes a W3C traceparent into a new carrier."""
    with _tracer().start_as_current_span("producer"):
        carrier = inject_trace_context()

    assert "traceparent" in carrier
    # A W3C traceparent has four hyphen-separated fields.
    assert carrier["traceparent"].count("-") == 3


def test_inject_mutates_and_returns_the_supplied_carrier(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A supplied carrier is mutated in place and also returned."""
    carrier: dict[str, str] = {"existing": "value"}
    with _tracer().start_as_current_span("producer"):
        returned = inject_trace_context(carrier)

    assert returned is carrier
    assert carrier["existing"] == "value"
    assert "traceparent" in carrier


def test_inject_extract_round_trips_the_trace_id(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A consumer span built from the carrier shares the producer's trace id."""
    # Producer side: open a span and serialise its context to a carrier.
    with _tracer().start_as_current_span("producer") as producer_span:
        producer_trace_id = producer_span.get_span_context().trace_id
        carrier = inject_trace_context()

    # Consumer side: rebuild the context and start a child span under it.
    parent_context = extract_trace_context(carrier)
    with _tracer().start_as_current_span(
        "consumer", context=parent_context
    ) as consumer_span:
        consumer_context = consumer_span.get_span_context()

    # The consumer span joined the producer's trace.
    assert consumer_context.trace_id == producer_trace_id


def test_extract_from_empty_carrier_yields_a_root_context() -> None:
    """An empty carrier extracts to a context with no remote span."""
    context = extract_trace_context({})
    span_context = trace.get_current_span(context).get_span_context()
    assert not span_context.is_valid


def test_round_trip_through_a_plain_dict_only(
    span_exporter: InMemorySpanExporter,
) -> None:
    """The carrier is an ordinary dict — what the event envelope will store.

    This guards the contract the event-bus SDK (issue 13) depends on: trace
    context survives a round-trip through nothing more than a string-keyed,
    string-valued dict.
    """
    with _tracer().start_as_current_span("producer") as span:
        original_trace_id = span.get_span_context().trace_id
        envelope_field: dict[str, str] = {}
        inject_trace_context(envelope_field)
    assert original_trace_id != 0

    # Every value is a plain string — JSON-serialisable for the envelope.
    assert all(isinstance(value, str) for value in envelope_field.values())

    parent = extract_trace_context(dict(envelope_field))
    recovered = trace.get_current_span(parent).get_span_context()
    assert recovered.trace_id == original_trace_id
