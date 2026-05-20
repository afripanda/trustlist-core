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

"""Integration tests for the event-bus SDK — real RedPanda, no mocks.

These tests exercise the SDK against a real RedPanda broker and its built-in
schema registry (Stage 0 PRD §7a / §7b: no mocks for backing services). They
cover the §7b producer/consumer contract end to end:

- a synthetic produce -> consume round-trip persisting the decoded envelope;
- schema validation on both produce and consume;
- idempotency-key deduplication of a redelivered event;
- offset commit only after successful handler processing (at-least-once);
- distributed-trace context surviving the bus hop.

Run with ``pytest -m integration`` and the two
``TRUSTLIST_EVENT_BUS_*`` environment variables set (see ``conftest.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from observability import init_tracing, inject_trace_context
from trustlist_event_bus.admin import ensure_topic
from trustlist_event_bus.config import EventBusConfig
from trustlist_event_bus.consumer import EventConsumer, InMemoryDedupStore
from trustlist_event_bus.envelope import EventEnvelope, Provenance, new_envelope
from trustlist_event_bus.errors import SchemaValidationError
from trustlist_event_bus.idempotency import derive_idempotency_key
from trustlist_event_bus.producer import EventProducer
from trustlist_event_bus.schema_registry import SchemaRegistry

pytestmark = pytest.mark.integration

_EVENT_TYPE = "signal.tier-one.example-collector"
_KEY_FIELDS = ("domain_id", "signal_class", "observed_at")


def _signal_payload(domain_id: str) -> dict[str, object]:
    """Return a schema-valid example-collector payload for ``domain_id``."""
    return {
        "domain_id": domain_id,
        "signal_class": "dns",
        "source_url": "",
        "observed_at": "2026-05-20T12:00:00+00:00",
        "observed_value": {"resolves": True},
    }


def _reset_global_provider() -> None:
    """Clear OpenTelemetry's process-global tracer provider between tests."""
    once_cls = type(trace._TRACER_PROVIDER_SET_ONCE)
    trace._TRACER_PROVIDER_SET_ONCE = once_cls()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """An in-memory span exporter wired as the global tracer provider."""
    _reset_global_provider()
    exporter = InMemorySpanExporter()
    init_tracing("event-bus-sdk-integration", exporter=exporter)
    yield exporter
    exporter.clear()
    _reset_global_provider()


def test_produce_consume_roundtrip(
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    unique_topic: str,
) -> None:
    """A produced event is consumed back, decoded, and equal to what was sent.

    This is the synthetic round-trip of PRD §8.2 at the SDK level: a stub
    collector produces a tier-one signal; an evidence-writer-style consumer
    reads it back off the real bus.
    """
    ensure_topic(unique_topic, config=event_bus_config)
    domain_id = str(uuid.uuid4())
    payload = _signal_payload(domain_id)
    idempotency_key = derive_idempotency_key(
        event_type=_EVENT_TYPE, payload=payload, key_fields=_KEY_FIELDS
    )

    with EventProducer(
        "it-collector",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as producer:
        sent = producer.produce(
            unique_topic,
            payload,
            event_type=_EVENT_TYPE,
            idempotency_key=idempotency_key,
            provenance=Provenance(source="system", method="dns-probe"),
            partition_key=domain_id,
            flush=True,
        )

    received: list[EventEnvelope] = []
    with EventConsumer(
        f"it-group-{uuid.uuid4().hex[:8]}",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as consumer:
        consumer.subscribe([unique_topic])
        processed = consumer.run(
            received.append, poll_timeout=2.0, max_events=1
        )

    assert processed == 1
    assert len(received) == 1
    got = received[0]
    assert got.event_id == sent.event_id
    assert got.event_type == _EVENT_TYPE
    assert got.idempotency_key == idempotency_key
    assert got.payload == payload
    assert got.provenance == Provenance(source="system", method="dns-probe")


def test_produce_rejects_a_schema_invalid_payload(
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    unique_topic: str,
) -> None:
    """A payload that fails schema validation is never produced (§7b)."""
    ensure_topic(unique_topic, config=event_bus_config)
    with EventProducer(
        "it-collector",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as producer:
        with pytest.raises(SchemaValidationError):
            producer.produce(
                unique_topic,
                {"signal_class": "dns"},  # missing required domain_id
                event_type=_EVENT_TYPE,
                idempotency_key="k",
                provenance=Provenance(source="system", method="probe"),
            )


def test_consumer_deduplicates_a_redelivered_event(
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    unique_topic: str,
) -> None:
    """Two events sharing an idempotency key are handled once (§7b dedup).

    The producer emits the same logical observation twice — same payload, same
    derived idempotency key. The consumer's handler must fire only once; the
    second delivery is recognised as a duplicate and skipped.
    """
    ensure_topic(unique_topic, config=event_bus_config)
    domain_id = str(uuid.uuid4())
    payload = _signal_payload(domain_id)
    idempotency_key = derive_idempotency_key(
        event_type=_EVENT_TYPE, payload=payload, key_fields=_KEY_FIELDS
    )

    with EventProducer(
        "it-collector",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as producer:
        # Two emissions of the same logical event (distinct event_ids, one key).
        for _ in range(2):
            producer.produce(
                unique_topic,
                payload,
                event_type=_EVENT_TYPE,
                idempotency_key=idempotency_key,
                provenance=Provenance(source="system", method="dns-probe"),
                partition_key=domain_id,
            )
        producer.flush()

    handled: list[str] = []
    with EventConsumer(
        f"it-group-{uuid.uuid4().hex[:8]}",
        config=event_bus_config,
        schema_registry=schema_registry,
        dedup_store=InMemoryDedupStore(),
    ) as consumer:
        consumer.subscribe([unique_topic])
        # Poll for both events — both are delivered, but the handler sees one.
        processed = consumer.run(
            lambda env: handled.append(env.idempotency_key),
            poll_timeout=2.0,
            max_events=2,
        )

    assert processed == 2  # both events were consumed off the bus
    assert handled == [idempotency_key]  # the handler fired exactly once


def test_handler_failure_leaves_the_event_for_redelivery(
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    unique_topic: str,
) -> None:
    """A handler exception leaves the offset uncommitted; the event redelivers.

    This is the at-least-once contract (§7b): the offset is committed only
    after the handler succeeds. A first consumer fails on the event; a second
    consumer in the same group still receives it because the offset never
    advanced.
    """
    ensure_topic(unique_topic, config=event_bus_config)
    domain_id = str(uuid.uuid4())
    payload = _signal_payload(domain_id)
    group_id = f"it-group-{uuid.uuid4().hex[:8]}"

    with EventProducer(
        "it-collector",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as producer:
        producer.produce(
            unique_topic,
            payload,
            event_type=_EVENT_TYPE,
            idempotency_key=derive_idempotency_key(
                event_type=_EVENT_TYPE, payload=payload, key_fields=_KEY_FIELDS
            ),
            provenance=Provenance(source="system", method="dns-probe"),
            partition_key=domain_id,
            flush=True,
        )

    # First consumer: the handler raises, so the offset is not committed.
    def _failing_handler(_: EventEnvelope) -> None:
        raise RuntimeError("simulated processing failure")

    with EventConsumer(
        group_id, config=event_bus_config, schema_registry=schema_registry
    ) as failing_consumer:
        failing_consumer.subscribe([unique_topic])
        with pytest.raises(RuntimeError, match="simulated processing failure"):
            failing_consumer.poll_once(_failing_handler, timeout=5.0)

    # Second consumer, same group: the uncommitted event is redelivered.
    redelivered: list[EventEnvelope] = []
    with EventConsumer(
        group_id, config=event_bus_config, schema_registry=schema_registry
    ) as recovering_consumer:
        recovering_consumer.subscribe([unique_topic])
        processed = recovering_consumer.run(
            redelivered.append, poll_timeout=2.0, max_events=1
        )

    assert processed == 1
    assert redelivered[0].payload == payload


def test_trace_context_survives_the_bus_hop(
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    unique_topic: str,
    span_exporter: InMemorySpanExporter,
) -> None:
    """A consumer span joins the producer's trace across a real bus hop (§7b).

    The producer injects the active W3C trace context into the envelope; after
    a real produce/consume the consumer extracts it and a child span shares the
    producer's trace id — collector -> bus -> consumer is one distributed trace.
    """
    from observability import extract_trace_context

    ensure_topic(unique_topic, config=event_bus_config)
    domain_id = str(uuid.uuid4())
    payload = _signal_payload(domain_id)
    tracer = trace.get_tracer("trustlist.tests")

    with EventProducer(
        "it-collector",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as producer:
        with tracer.start_as_current_span("producer") as producer_span:
            producer_trace_id = producer_span.get_span_context().trace_id
            # The producer injects inject_trace_context() internally; building
            # an explicit envelope here only confirms a span is active.
            assert inject_trace_context().get("traceparent")
            producer.produce(
                unique_topic,
                payload,
                event_type=_EVENT_TYPE,
                idempotency_key=derive_idempotency_key(
                    event_type=_EVENT_TYPE,
                    payload=payload,
                    key_fields=_KEY_FIELDS,
                ),
                provenance=Provenance(source="system", method="dns-probe"),
                partition_key=domain_id,
                flush=True,
            )

    received: list[EventEnvelope] = []
    with EventConsumer(
        f"it-group-{uuid.uuid4().hex[:8]}",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as consumer:
        consumer.subscribe([unique_topic])
        consumer.run(received.append, poll_timeout=2.0, max_events=1)

    assert received, "expected the traced event to be consumed"
    parent = extract_trace_context(received[0].trace_context)
    with tracer.start_as_current_span(
        "consumer", context=parent
    ) as consumer_span:
        consumer_trace_id = consumer_span.get_span_context().trace_id

    assert consumer_trace_id == producer_trace_id


def test_unregistered_event_type_fails_validation(
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    unique_topic: str,
) -> None:
    """Producing an event whose type has no registered schema is refused.

    Schema discipline (§7b): an event type with nothing in the registry cannot
    be validated, so it cannot be produced.
    """
    from trustlist_event_bus.errors import SchemaRegistryError

    with EventProducer(
        "it-collector",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as producer:
        with pytest.raises(SchemaRegistryError):
            producer.produce(
                unique_topic,
                {"anything": True},
                event_type=f"signal.unregistered.{uuid.uuid4().hex}",
                idempotency_key="k",
                provenance=Provenance(source="system", method="probe"),
            )


def test_new_envelope_explicit_fields_are_preserved_over_the_bus(
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    unique_topic: str,
) -> None:
    """An envelope built with explicit ids survives the bus byte-identically.

    Underpins the reproducibility discipline: a fixture event with a fixed
    event_id and produced_at decodes to exactly those values on the far side.
    """
    from datetime import UTC, datetime

    ensure_topic(unique_topic, config=event_bus_config)
    fixed_id = uuid.uuid4()
    fixed_time = datetime(2026, 5, 20, 9, 30, 0, tzinfo=UTC)
    domain_id = str(uuid.uuid4())
    payload = _signal_payload(domain_id)

    # Build the envelope explicitly to assert the bus preserves every field.
    envelope = new_envelope(
        event_type=_EVENT_TYPE,
        payload=payload,
        producer_id="it-collector",
        idempotency_key="fixed-key",
        provenance=Provenance(source="system", method="dns-probe"),
        event_id=fixed_id,
        produced_at=fixed_time,
    )
    schema_registry.validate(_EVENT_TYPE, payload)

    from confluent_kafka import Producer

    raw_producer = Producer({"bootstrap.servers": event_bus_config.brokers})
    raw_producer.produce(
        topic=unique_topic,
        key=domain_id.encode("utf-8"),
        value=envelope.to_bytes(),
    )
    raw_producer.flush()

    received: list[EventEnvelope] = []
    with EventConsumer(
        f"it-group-{uuid.uuid4().hex[:8]}",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as consumer:
        consumer.subscribe([unique_topic])
        consumer.run(received.append, poll_timeout=2.0, max_events=1)

    assert len(received) == 1
    assert received[0].event_id == fixed_id
    assert received[0].produced_at == fixed_time
