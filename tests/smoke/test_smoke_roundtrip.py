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

"""Stage 0 end-to-end smoke test — the synthetic-signal round-trip (PRD §8.2).

This is the headline Stage 0 milestone: a runnable, demoable pipeline that
proves a synthetic tier-one signal flows end-to-end through the foundation. The
test exercises the full event-bus → canonical-store path against *real* backing
services (a real RedPanda and a real, migrated Postgres — no mocks, PRD §7a /
§7b discipline):

1. **Produce.** A test harness constructs a synthetic tier-one signal event
   with the Python event-bus SDK and produces it to
   ``signal.tier-one.example-collector`` (PRD §7b topic).
2. **Consume + write.** The evidence-writer service stub
   (:mod:`tests.smoke.evidence_writer`) subscribes via the SDK, reads the
   event, and writes one ``provenance`` row then one ``evidence`` row
   referencing it — using the data-model schema, in one transaction.
3. **Assert.** The test queries the canonical store and asserts the
   ``evidence`` and ``provenance`` rows match the synthetic payload.

The test additionally proves three properties the acceptance criteria call
for:

- **Idempotency** (PRD §8 criterion 5). The same logical signal delivered
  twice — and the whole test re-run — converges the canonical store to exactly
  one ``provenance`` + ``evidence`` pair. The synthetic payload is *fixed*
  (a constant ``domain_id`` and ``observed_at``), so the derived
  ``idempotency_key`` is stable across runs and the writer's database-level
  deduplication recognises a re-run.
- **Trace-context propagation** (PRD §7f, §8 criterion 6). The W3C trace
  context set on the produced envelope is carried through the bus and is
  visible to the consumer: the evidence-writer's consume span (created by
  :func:`observability.instrument_consume`) joins the producer's trace. This
  is asserted with an in-memory span exporter — locally verifiable. Full
  verification of the spans in Honeycomb is deferred until the observability
  platform is wired (no account yet); see the trace-propagation test below.

Run with ``pytest -m integration`` and the three ``TRUSTLIST_*`` connection
variables set (see ``conftest.py``).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import Engine, text

from observability import init_tracing
from tests.smoke.evidence_writer import EvidenceWriter
from trustlist_event_bus import (
    EventConsumer,
    EventProducer,
    Provenance,
    derive_idempotency_key,
    ensure_topic,
)
from trustlist_event_bus.config import EventBusConfig
from trustlist_event_bus.schema_registry import SchemaRegistry

pytestmark = pytest.mark.integration

# The synthetic tier-one signal event type — the §7b topic plus payload-type
# qualifier, and the schema-registry key for the example-collector schema.
_EVENT_TYPE = "signal.tier-one.example-collector"

# The payload fields the idempotency key is derived from (PRD §7b: the key is
# derived from payload-specific fields so two emissions of the same logical
# observation collide).
_KEY_FIELDS = ("domain_id", "signal_class", "observed_at")

# A *fixed* synthetic domain id. Holding it constant — rather than generating a
# fresh UUID per run — is what makes the smoke test idempotent across runs:
# the derived idempotency key is stable, so a re-run is recognised as a
# duplicate by the evidence-writer's database-level deduplication (PRD §8
# criterion 5). The UUID is arbitrary but pinned; the trailing ``022`` nods to
# this being the issue-22 smoke-test fixture.
_FIXED_DOMAIN_ID = "00000000-0000-4000-8000-000000000022"

# A schema-valid, fully-deterministic example-collector payload. Every field is
# fixed so the round-trip is byte-reproducible — this is the fixture the
# reproducibility test (issue 24) will reuse.
_SYNTHETIC_PAYLOAD: dict[str, object] = {
    "domain_id": _FIXED_DOMAIN_ID,
    "signal_class": "dns",
    "source_url": "",
    "observed_at": "2026-05-20T12:00:00+00:00",
    "observed_value": {"resolves": True, "record_count": 3},
}

# The provenance of the synthetic observation (PRD §7a provenance triple).
_SYNTHETIC_PROVENANCE = Provenance(
    source="system",
    method="dns-probe",
    contributor_identity=None,
)


def _idempotency_key() -> str:
    """Return the stable idempotency key for the fixed synthetic payload."""
    return derive_idempotency_key(
        event_type=_EVENT_TYPE,
        payload=_SYNTHETIC_PAYLOAD,
        key_fields=_KEY_FIELDS,
    )


def _delete_smoke_rows(engine: Engine) -> None:
    """Remove the smoke test's own canonical-store rows.

    Run before and after the test so the suite stays hermetic and the test can
    re-run from a known-clean state. The evidence-writer commits its own
    transactions (it must, to be a realistic consumer), so the repository-level
    transaction-rollback fixture cannot undo them — explicit teardown does.

    Deletion is keyed on the fixed synthetic ``domain_id``; it cascades by hand
    through ``evidence`` → ``provenance`` → ``domain`` in foreign-key order.
    This uses the privileged migration-owner connection, not the append-only
    application role — cleanup is a test-harness concern, not a production code
    path.
    """
    with engine.begin() as conn:
        provenance_ids = [
            str(row[0])
            for row in conn.execute(
                text("SELECT provenance_id FROM evidence WHERE domain_id = :d"),
                {"d": _FIXED_DOMAIN_ID},
            ).all()
        ]
        conn.execute(
            text("DELETE FROM evidence WHERE domain_id = :d"),
            {"d": _FIXED_DOMAIN_ID},
        )
        if provenance_ids:
            conn.execute(
                text("DELETE FROM provenance WHERE provenance_id = ANY(:ids)"),
                {"ids": provenance_ids},
            )
        conn.execute(
            text("DELETE FROM domain WHERE domain_id = :d"),
            {"d": _FIXED_DOMAIN_ID},
        )


@pytest.fixture
def clean_canonical_store(engine: Engine) -> Iterator[Engine]:
    """Yield the canonical-store engine with the smoke test's rows removed.

    The pre-test delete gives every run a known-clean starting point — the
    "freshly-migrated database" of PRD §8 criterion 5; the post-test delete
    keeps the suite hermetic for whatever runs next.
    """
    _delete_smoke_rows(engine)
    try:
        yield engine
    finally:
        _delete_smoke_rows(engine)


def _reset_global_provider() -> None:
    """Clear OpenTelemetry's process-global tracer provider between tests.

    ``init_tracing`` installs a process-global provider; the global is a
    write-once cell, so a test that needs its own provider must first clear the
    cell. This mirrors the event-bus SDK integration suite's helper.
    """
    once_cls = type(trace._TRACER_PROVIDER_SET_ONCE)
    trace._TRACER_PROVIDER_SET_ONCE = once_cls()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """An in-memory span exporter wired as the global tracer provider.

    The smoke test verifies trace propagation *locally* — it asserts against
    the spans this exporter captures. Export to Honeycomb (ADR-0012) is
    deferred until the observability platform is wired.
    """
    _reset_global_provider()
    exporter = InMemorySpanExporter()
    init_tracing("trustlist-smoke-test", exporter=exporter)
    yield exporter
    exporter.clear()
    _reset_global_provider()


def _produce_synthetic_signal(
    topic: str,
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
) -> str:
    """Produce one synthetic tier-one signal to ``topic``; return its event id.

    This is the "stub collector" half of the round-trip: it constructs the
    fixed synthetic event with the Python SDK and publishes it, flushing so the
    broker has acknowledged it before the consumer starts.
    """
    with EventProducer(
        "smoke-collector",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as producer:
        sent = producer.produce(
            topic,
            _SYNTHETIC_PAYLOAD,
            event_type=_EVENT_TYPE,
            idempotency_key=_idempotency_key(),
            provenance=_SYNTHETIC_PROVENANCE,
            partition_key=_FIXED_DOMAIN_ID,
            flush=True,
        )
    return str(sent.event_id)


def test_synthetic_signal_round_trip_to_canonical_store(
    clean_canonical_store: Engine,
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    smoke_topic: str,
) -> None:
    """A synthetic signal flows producer → bus → evidence-writer → canonical store.

    The headline Stage 0 round-trip (PRD §8 acceptance criterion 2): produce a
    tier-one signal with the SDK, run the evidence-writer stub as the consumer,
    then query the canonical store and assert the persisted ``provenance`` and
    ``evidence`` rows match the synthetic payload exactly.
    """
    engine = clean_canonical_store
    ensure_topic(smoke_topic, config=event_bus_config)

    # --- produce -----------------------------------------------------------
    _produce_synthetic_signal(smoke_topic, event_bus_config, schema_registry)

    # --- consume + write ---------------------------------------------------
    writer = EvidenceWriter(engine)
    with EventConsumer(
        f"smoke-evidence-writer-{uuid.uuid4().hex[:8]}",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as consumer:
        consumer.subscribe([smoke_topic])
        processed = consumer.run(writer, poll_timeout=2.0, max_events=1)

    assert processed == 1, "the evidence-writer should consume exactly one event"
    assert len(writer.results) == 1
    result = writer.results[0]
    assert result.created is True, "the first delivery must insert new rows"

    # --- assert: the canonical store matches the synthetic payload ---------
    with engine.connect() as conn:
        provenance = conn.execute(
            text(
                "SELECT source, observed_at, contributor_identity "
                "FROM provenance WHERE provenance_id = :id"
            ),
            {"id": result.provenance_id},
        ).one()
        evidence = conn.execute(
            text(
                "SELECT domain_id, signal_class, source, source_url, "
                "       observed_at, observed_value, provenance_id "
                "FROM evidence WHERE evidence_id = :id"
            ),
            {"id": result.evidence_id},
        ).one()

    # The provenance row carries the envelope's §7a provenance triple.
    assert provenance.source == _SYNTHETIC_PROVENANCE.source
    assert provenance.observed_at == datetime(
        2026, 5, 20, 12, 0, 0, tzinfo=UTC
    )
    assert provenance.contributor_identity is None

    # The evidence row carries the synthetic payload and references the
    # provenance row — the §7a "every evidence row references one provenance
    # row" invariant, proven end-to-end through the bus.
    assert str(evidence.domain_id) == _FIXED_DOMAIN_ID
    assert evidence.signal_class == _SYNTHETIC_PAYLOAD["signal_class"]
    assert evidence.source == _SYNTHETIC_PROVENANCE.source
    assert evidence.source_url == _SYNTHETIC_PAYLOAD["source_url"]
    assert evidence.observed_at == datetime(2026, 5, 20, 12, 0, 0, tzinfo=UTC)
    assert evidence.observed_value == _SYNTHETIC_PAYLOAD["observed_value"]
    assert str(evidence.provenance_id) == result.provenance_id


def test_round_trip_is_idempotent(
    clean_canonical_store: Engine,
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    smoke_topic: str,
) -> None:
    """Re-running the round-trip leaves the canonical store in one final state.

    PRD §8 criterion 5: the smoke test is idempotent — re-running produces the
    same final canonical-store state. The ``clean_canonical_store`` fixture has
    already given this run a freshly-cleaned start; this test then delivers the
    *same logical signal twice* and asserts the canonical store converges to
    exactly one ``provenance`` + ``evidence`` pair. The second delivery is
    recognised as a duplicate by the evidence-writer's database-level
    deduplication (keyed on the stable idempotency key) and inserts nothing.

    This is also the property that underwrites the reproducibility test
    (issue 24): a fixture signal replayed against a freshly-migrated database
    yields identical rows.
    """
    engine = clean_canonical_store
    ensure_topic(smoke_topic, config=event_bus_config)
    writer = EvidenceWriter(engine)

    # Deliver the same logical signal twice — two produce/consume cycles, one
    # logical event (same fixed payload, same derived idempotency key).
    for _ in range(2):
        _produce_synthetic_signal(
            smoke_topic, event_bus_config, schema_registry
        )
        with EventConsumer(
            f"smoke-idem-{uuid.uuid4().hex[:8]}",
            config=event_bus_config,
            schema_registry=schema_registry,
        ) as consumer:
            consumer.subscribe([smoke_topic])
            consumer.run(writer, poll_timeout=2.0, max_events=1)

    # Two events were handled, but the second was recognised as a duplicate.
    assert len(writer.results) == 2
    assert writer.results[0].created is True, "first delivery inserts"
    assert writer.results[1].created is False, "second delivery is a no-op"
    # Both deliveries resolve to the *same* canonical rows.
    assert writer.results[0].evidence_id == writer.results[1].evidence_id
    assert writer.results[0].provenance_id == writer.results[1].provenance_id

    # The canonical store holds exactly one pair for the synthetic domain.
    with engine.connect() as conn:
        evidence_count = conn.execute(
            text("SELECT count(*) FROM evidence WHERE domain_id = :d"),
            {"d": _FIXED_DOMAIN_ID},
        ).scalar()
        provenance_count = conn.execute(
            text(
                "SELECT count(*) FROM provenance p "
                "JOIN evidence e ON e.provenance_id = p.provenance_id "
                "WHERE e.domain_id = :d"
            ),
            {"d": _FIXED_DOMAIN_ID},
        ).scalar()
        domain_count = conn.execute(
            text("SELECT count(*) FROM domain WHERE domain_id = :d"),
            {"d": _FIXED_DOMAIN_ID},
        ).scalar()

    assert evidence_count == 1, "idempotent: exactly one evidence row"
    assert provenance_count == 1, "idempotent: exactly one provenance row"
    assert domain_count == 1, "idempotent: exactly one domain row"


def test_trace_context_propagates_producer_to_consumer(
    clean_canonical_store: Engine,
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
    smoke_topic: str,
    span_exporter: InMemorySpanExporter,
) -> None:
    """W3C trace context survives the bus hop: one trace, producer → consumer.

    PRD §7f and §8 criterion 6: the distributed trace of a signal's flow must
    span the producer, the bus hop and the consumer write. This asserts the
    *locally verifiable* part of that criterion:

    - the producer injects the active W3C trace context into the envelope's
      ``trace_context`` field (the event-bus SDK does this internally);
    - after a real produce → consume over RedPanda, the evidence-writer's
      consume span — created by :func:`observability.instrument_consume`,
      which reads the carrier — shares the producer span's ``trace_id``.

    **Deferred.** Full verification of these spans in the observability
    platform (Honeycomb, ADR-0012) — confirming the producer span, the bus-hop
    span and the consumer-write span all appear under one trace in the
    Honeycomb UI — is deferred until the observability platform is wired; there
    is no Honeycomb account yet. The in-memory span exporter used here proves
    the propagation mechanism end-to-end; only the platform-side rendering is
    outstanding.
    """
    engine = clean_canonical_store
    ensure_topic(smoke_topic, config=event_bus_config)
    tracer = trace.get_tracer("trustlist.smoke")

    # --- produce inside a span; the SDK injects its trace context ----------
    with tracer.start_as_current_span("smoke-producer") as producer_span:
        producer_trace_id = producer_span.get_span_context().trace_id
        _produce_synthetic_signal(
            smoke_topic, event_bus_config, schema_registry
        )

    # --- consume; the evidence-writer's instrumented handler joins the trace
    writer = EvidenceWriter(engine)
    with EventConsumer(
        f"smoke-trace-{uuid.uuid4().hex[:8]}",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as consumer:
        consumer.subscribe([smoke_topic])
        processed = consumer.run(writer, poll_timeout=2.0, max_events=1)

    assert processed == 1
    assert len(writer.results) == 1

    # The envelope must have carried a W3C traceparent across the bus.
    # (The evidence-writer consumed it; we re-derive the assertion from the
    # captured spans.)
    finished: tuple[ReadableSpan, ...] = span_exporter.get_finished_spans()
    by_name = {span.name: span for span in finished}

    # The producer span ended; the evidence-writer's consume span was created
    # by instrument_consume("evidence-writer.handle").
    assert "smoke-producer" in by_name, "the producer span should be recorded"
    consume_span_name = "CONSUME evidence-writer.handle"
    assert consume_span_name in by_name, (
        f"the evidence-writer consume span {consume_span_name!r} should be "
        f"recorded; got {sorted(by_name)}"
    )

    consume_span = by_name[consume_span_name]
    consume_ctx = consume_span.get_span_context()
    assert consume_ctx is not None, "the consume span must carry a span context"

    # The headline assertion: the consumer span shares the producer's trace id
    # — collector → bus → consumer is a single distributed trace.
    assert consume_ctx.trace_id == producer_trace_id, (
        "the evidence-writer consume span must join the producer's trace; "
        "the W3C trace context did not propagate across the bus hop"
    )

    # The consume span is parented to the producer's trace (a remote parent),
    # confirming instrument_consume read the envelope's trace_context carrier.
    assert consume_span.parent is not None
    assert consume_span.parent.trace_id == producer_trace_id
