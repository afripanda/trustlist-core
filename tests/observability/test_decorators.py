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

"""Tests for the five §7f instrumentation decorators."""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, StatusCode

from observability.decorators import (
    instrument_consume,
    instrument_db_query,
    instrument_http_request,
    instrument_produce,
    instrument_scheduled_job,
)
from observability.propagation import inject_trace_context
from observability.tracing import init_tracing


def test_http_decorator_creates_a_server_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """instrument_http_request opens a SERVER-kind span around the handler."""

    @instrument_http_request("GET /domains")
    def handler() -> str:
        return "ok"

    assert handler() == "ok"

    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "HTTP GET /domains"
    assert spans[0].kind is SpanKind.SERVER
    assert spans[0].attributes is not None
    assert spans[0].attributes["trustlist.surface"] == "http_request"


def test_db_decorator_creates_a_client_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """instrument_db_query opens a CLIENT-kind span."""

    @instrument_db_query("select-domain")
    def query() -> int:
        return 7

    assert query() == 7

    span = span_exporter.get_finished_spans()[0]
    assert span.kind is SpanKind.CLIENT
    assert span.attributes is not None
    assert span.attributes["trustlist.surface"] == "db_query"


def test_produce_decorator_creates_a_producer_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """instrument_produce opens a PRODUCER-kind span."""

    @instrument_produce("signal.tier-one.example-collector")
    def publish() -> None:
        return None

    publish()

    span = span_exporter.get_finished_spans()[0]
    assert span.kind is SpanKind.PRODUCER
    assert span.attributes is not None
    assert span.attributes["trustlist.surface"] == "event_produce"


def test_scheduled_job_decorator_creates_an_internal_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """instrument_scheduled_job opens an INTERNAL-kind span."""

    @instrument_scheduled_job("rescore-sweep")
    def job() -> None:
        return None

    job()

    span = span_exporter.get_finished_spans()[0]
    assert span.kind is SpanKind.INTERNAL
    assert span.attributes is not None
    assert span.attributes["trustlist.surface"] == "scheduled_job"


def test_consume_decorator_creates_a_consumer_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """instrument_consume opens a CONSUMER-kind span."""

    @instrument_consume("evidence-writer")
    def consume(*, carrier: dict[str, str]) -> None:
        return None

    consume(carrier={})

    span = span_exporter.get_finished_spans()[0]
    assert span.kind is SpanKind.CONSUMER
    assert span.attributes is not None
    assert span.attributes["trustlist.surface"] == "event_consume"


def test_decorator_default_span_name_uses_qualified_name(
    span_exporter: InMemorySpanExporter,
) -> None:
    """With no explicit name, the span name falls back to the qualified name."""

    @instrument_db_query()
    def fetch_pool() -> None:
        return None

    fetch_pool()

    span = span_exporter.get_finished_spans()[0]
    assert "fetch_pool" in span.name


def test_extra_attributes_are_stamped_on_the_span(
    span_exporter: InMemorySpanExporter,
) -> None:
    """Attributes passed to the decorator land on the span."""

    @instrument_db_query("count", attributes={"db.table": "domain"})
    def count_domains() -> int:
        return 42

    count_domains()

    span = span_exporter.get_finished_spans()[0]
    assert span.attributes is not None
    assert span.attributes["db.table"] == "domain"


def test_decorator_records_exception_and_sets_error_status(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A raising callable yields a span with ERROR status and a recorded event."""

    @instrument_http_request("POST /broken")
    def handler() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        handler()

    span = span_exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    event_names = [event.name for event in span.events]
    assert "exception" in event_names


def test_decorator_preserves_signature_metadata(
    span_exporter: InMemorySpanExporter,
) -> None:
    """functools.wraps keeps the wrapped callable's identity intact."""

    @instrument_scheduled_job()
    def documented_job() -> None:
        """A job with a docstring."""
        return None

    assert documented_job.__name__ == "documented_job"
    assert documented_job.__doc__ == "A job with a docstring."


def test_decorator_passes_through_arguments(
    span_exporter: InMemorySpanExporter,
) -> None:
    """Positional and keyword arguments reach the wrapped callable unchanged."""

    @instrument_db_query("add")
    def add(left: int, right: int, *, scale: int = 1) -> int:
        return (left + right) * scale

    assert add(2, 3, scale=10) == 50


def test_consume_decorator_joins_the_producer_trace(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A consume handler parents its span to the trace in the carrier.

    This is the cross-bus propagation acceptance criterion: a producer span and
    a consumer span built from the producer's carrier share one trace id.
    """

    @instrument_produce("signal.tier-one.example-collector")
    def produce(*, envelope: dict[str, dict[str, str]]) -> None:
        # The event-bus SDK will write the carrier into the envelope here.
        inject_trace_context(envelope["trace_context"])

    @instrument_consume("evidence-writer")
    def consume(*, carrier: dict[str, str]) -> None:
        return None

    envelope: dict[str, dict[str, str]] = {"trace_context": {}}
    produce(envelope=envelope)
    consume(carrier=envelope["trace_context"])

    spans = span_exporter.get_finished_spans()
    by_kind = {span.kind: span for span in spans}
    producer = by_kind[SpanKind.PRODUCER]
    consumer = by_kind[SpanKind.CONSUMER]

    # Same trace; the consumer is a child of the producer span.
    assert consumer.context.trace_id == producer.context.trace_id
    assert consumer.parent is not None
    assert consumer.parent.span_id == producer.context.span_id


def test_consume_without_a_carrier_starts_a_root_trace(
    span_exporter: InMemorySpanExporter,
) -> None:
    """A consume handler with no carrier still runs, starting a fresh trace."""

    @instrument_consume("evidence-writer")
    def consume(value: int) -> int:
        return value

    assert consume(99) == 99

    span = span_exporter.get_finished_spans()[0]
    assert span.kind is SpanKind.CONSUMER
    assert span.parent is None


def test_async_callable_is_instrumented(
    span_exporter: InMemorySpanExporter,
) -> None:
    """The decorators wrap coroutine functions, keeping the span open until done."""

    @instrument_http_request("GET /async")
    async def handler() -> str:
        await asyncio.sleep(0)
        return "async-ok"

    result = asyncio.run(handler())
    assert result == "async-ok"

    span = span_exporter.get_finished_spans()[0]
    assert span.name == "HTTP GET /async"
    assert span.kind is SpanKind.SERVER


def test_async_callable_records_exceptions(
    span_exporter: InMemorySpanExporter,
) -> None:
    """An async callable that raises yields a span with ERROR status."""

    @instrument_scheduled_job("async-job")
    async def job() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("async boom")

    with pytest.raises(RuntimeError, match="async boom"):
        asyncio.run(job())

    span = span_exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR


def test_init_tracing_with_explicit_exporter_is_idempotent_per_test() -> None:
    """init_tracing returns a provider carrying the configured service name."""
    exporter = InMemorySpanExporter()
    provider = init_tracing("explicit-service", exporter=exporter)
    assert provider.resource.attributes["service.name"] == "explicit-service"
