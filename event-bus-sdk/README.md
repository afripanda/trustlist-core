# event-bus-sdk

Producer and consumer SDKs for the RedPanda event bus (ADR-0011), in Python and
TypeScript. The SDKs implement typed event construction, schema-registry
integration, idempotency-key derivation, distributed-tracing context
propagation and back-pressure handling, per Stage 0 PRD §7b.

| Package | Language | Directory | Status |
| --- | --- | --- | --- |
| `trustlist_event_bus` | Python | `trustlist_event_bus/` | Implemented — Stage 0 issue 13. |
| `@trustlist/event-bus-sdk` | TypeScript | `typescript/` | Implemented — Stage 0 issue 14. |

The Python package name is `trustlist_event_bus` (a valid Python identifier)
inside the hyphenated `event-bus-sdk/` directory, mirroring the
`data-model/trustlist_data_model/` precedent. The TypeScript package lives in
the `typescript/` subdirectory, cleanly separate from the Python package. Both
SDKs are versioned independently of `trustlist-core` and follow semver.

## Wire interoperability

The two SDKs are **wire-interoperable**: a Python-produced event is consumable
by the TypeScript consumer and vice versa. The event envelope serialises to
byte-identical JSON in both languages, and the idempotency-key derivation is
byte-identical — the same logical event collides on one key regardless of which
SDK produced it, which is what makes cross-language consumer-side
deduplication work. The TypeScript SDK's unit suite asserts this parity
against reference values produced by the Python SDK.

## Python SDK — `trustlist_event_bus`

### What it does

The SDK wraps the RedPanda event bus (Kafka-wire compatible) with the five §7b
SDK responsibilities:

1. **Typed event-envelope construction.** `EventEnvelope` / `new_envelope`
   carry every §7b field: `event_id`, `event_type`, `event_version`,
   `produced_at`, `producer_id`, `trace_context`, `idempotency_key`,
   `provenance`, `payload`.
2. **Schema-registry integration.** `SchemaRegistry` registers and validates
   payload JSON Schemas against RedPanda's built-in,
   Confluent-API-compatible registry. Payload schemas live under
   `event-schema/` and are validated on both produce and consume.
3. **Idempotency-key derivation.** `derive_idempotency_key` builds a stable
   SHA-256 key from payload-specific fields, so two emissions of the same
   logical event collide on one key.
4. **Distributed-tracing propagation.** The producer injects the active W3C
   trace context (via `observability.inject_trace_context`) into the
   envelope's `trace_context` field; the consumer surfaces it so a handler's
   span joins the producer's trace — collector → bus → consumer is one trace.
5. **Back-pressure as a typed error.** A full producer queue raises
   `BackPressureError`; the producer never blocks silently. Consumers commit
   offsets only after the handler succeeds (at-least-once) and deduplicate on
   the `idempotency_key`.

### Kafka client library

The SDK uses **`confluent-kafka`** (the librdkafka-based client). It is the
most robust Python Kafka client, ships pre-built wheels with librdkafka
bundled (no system package, no compiler), and bundles a Confluent-API schema
registry client that talks to RedPanda's built-in registry unchanged.

The envelope is plain JSON on the wire — the SDK deliberately does **not** use
the Confluent magic-byte / schema-id framing, so an event is always decodable
without a registry round-trip. The registry is used as the *contract store*:
schemas are registered there and validation at runtime is done locally with
`jsonschema` against the schema the registry holds.

### Usage

```python
from trustlist_event_bus import (
    EventProducer, EventConsumer, Provenance, derive_idempotency_key,
)

payload = {"domain_id": domain_id, "signal_class": "dns",
           "observed_at": "2026-05-20T12:00:00+00:00",
           "observed_value": {"resolves": True}}

# Produce.
with EventProducer("example-collector") as producer:
    producer.produce(
        "signal.tier-one.example-collector",
        payload,
        event_type="signal.tier-one.example-collector",
        idempotency_key=derive_idempotency_key(
            event_type="signal.tier-one.example-collector",
            payload=payload,
            key_fields=("domain_id", "signal_class", "observed_at"),
        ),
        provenance=Provenance(source="system", method="dns-probe"),
        partition_key=domain_id,   # per-domain ordering (§7b)
    )

# Consume.
with EventConsumer("evidence-writer") as consumer:
    consumer.subscribe(["signal.tier-one.example-collector"])
    consumer.run(lambda envelope: write_evidence(envelope))
```

### Configuration

Every connection detail is read from the environment — never hard-coded
(PRD §7b / §7g):

| Variable | Meaning |
| --- | --- |
| `TRUSTLIST_EVENT_BUS_BROKERS` | Kafka bootstrap servers, `host:port` comma-list. |
| `TRUSTLIST_SCHEMA_REGISTRY_URL` | Base URL of the schema registry. |

### Tests

- **Unit tests** (`tests/event_bus/`, run with `pytest -m "not integration"`)
  cover envelope construction, idempotency-key derivation, trace-context
  round-trip, configuration, the dedup store and the `event-schema/` files.
  They need no broker.
- **Integration tests** (`@pytest.mark.integration`) run a synthetic
  produce → consume round-trip against a **real RedPanda** — no mocks
  (PRD §7a / §7b). They are skipped when the two `TRUSTLIST_EVENT_BUS_*`
  variables are unset.

Local development — stand up an isolated RedPanda (the `docker-compose.dev.yml`
ports are occupied by the `mvp0-*` containers; use distinct high ports):

```sh
docker run -d --name trustlist-issue13-redpanda \
  -p 19292:19292 -p 18191:18191 -p 18192:18192 \
  redpandadata/redpanda:v24.2.7 \
  redpanda start --mode dev-container --smp 1 \
  --kafka-addr internal://0.0.0.0:9092,external://0.0.0.0:19292 \
  --advertise-kafka-addr internal://localhost:9092,external://localhost:19292 \
  --schema-registry-addr internal://0.0.0.0:8081,external://0.0.0.0:18191 \
  --pandaproxy-addr internal://0.0.0.0:8082,external://0.0.0.0:18192 \
  --rpc-addr 0.0.0.0:33145 --advertise-rpc-addr localhost:33145

export TRUSTLIST_EVENT_BUS_BROKERS=localhost:19292
export TRUSTLIST_SCHEMA_REGISTRY_URL=http://localhost:18191
uv run pytest -m integration tests/event_bus
```

In CI the `integration-tests` job runs inside a container and reaches a
RedPanda service container by its service hostname on the standard ports.
