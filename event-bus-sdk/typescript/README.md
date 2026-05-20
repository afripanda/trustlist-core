# @trustlist/event-bus-sdk (TypeScript)

The TypeScript half of the TrustList event-bus SDK (Stage 0 PRD §7b, issue 14).
It wraps the RedPanda event bus (ADR-0011, Kafka-wire compatible) for the
applications layer, and is **wire-interoperable** with the Python SDK
(`trustlist_event_bus`): a Python-produced event is consumable by this
TypeScript consumer and vice versa, and the same logical event derives a
byte-identical idempotency key in either language.

## What it does

The SDK implements the five §7b SDK responsibilities, mirroring the Python SDK:

1. **Typed event-envelope construction.** `EventEnvelope` / `newEnvelope` carry
   every §7b field: `event_id`, `event_type`, `event_version`, `produced_at`,
   `producer_id`, `trace_context`, `idempotency_key`, `provenance`, `payload`.
   The on-the-wire JSON is byte-identical to what the Python SDK emits.
2. **Schema-registry integration.** `SchemaRegistry` registers and validates
   payload JSON Schemas against RedPanda's built-in, Confluent-API-compatible
   registry. Payload schemas live under the shared `event-schema/` directory
   (the same files the Python SDK uses) and are validated on both produce and
   consume.
3. **Idempotency-key derivation.** `deriveIdempotencyKey` builds a stable
   SHA-256 key from payload-specific fields — byte-identical to the Python
   SDK's, so two emissions of the same logical event collide on one key across
   both languages.
4. **Distributed-tracing propagation.** The producer injects the active W3C
   trace context into the envelope's `trace_context` field; the consumer
   surfaces it so a handler's span joins the producer's trace.
5. **Back-pressure as a typed error.** A saturated producer buffer raises
   `BackPressureError`; the producer never blocks silently. Consumers commit
   offsets only after the handler succeeds (at-least-once) and deduplicate on
   the `idempotency_key`.

## Kafka client

The SDK uses **kafkajs** — the de-facto-standard pure-JavaScript Kafka client,
with no native dependency to compile. It is the natural counterpart to the
Python SDK's librdkafka-based `confluent-kafka`: both speak the Kafka wire
protocol, which is all the cross-language interoperability requires.

The envelope is plain JSON on the wire — the SDK deliberately does **not** use
the Confluent magic-byte / schema-id framing, so an event is always decodable
without a registry round-trip. The registry is used as the *contract store*:
schemas are registered there and validation at runtime is done locally with
[Ajv](https://ajv.js.org/) against the schema the registry holds.

## Usage

```ts
import {
  EventProducer,
  EventConsumer,
  provenance,
  deriveIdempotencyKey,
} from '@trustlist/event-bus-sdk';

const payload = {
  domain_id: domainId,
  signal_class: 'dns',
  source_url: '',
  observed_at: '2026-05-20T12:00:00+00:00',
  observed_value: { resolves: true },
};

// Produce.
const producer = new EventProducer('example-collector');
await producer.produce('signal.tier-one.example-collector', payload, {
  eventType: 'signal.tier-one.example-collector',
  idempotencyKey: deriveIdempotencyKey({
    eventType: 'signal.tier-one.example-collector',
    payload,
    keyFields: ['domain_id', 'signal_class', 'observed_at'],
  }),
  provenance: provenance('system', 'dns-probe'),
  partitionKey: domainId, // per-domain ordering (§7b)
});
await producer.close();

// Consume.
const consumer = new EventConsumer('evidence-writer');
await consumer.subscribe('signal.tier-one.example-collector', (envelope) => {
  writeEvidence(envelope);
});
await consumer.run();
```

## Configuration

Every connection detail is read from the environment — never hard-coded
(PRD §7b / §7g), the same two variables the Python SDK reads:

| Variable | Meaning |
| --- | --- |
| `TRUSTLIST_EVENT_BUS_BROKERS` | Kafka bootstrap servers, `host:port` comma-list. |
| `TRUSTLIST_SCHEMA_REGISTRY_URL` | Base URL of the schema registry. |

## Generated payload types

`src/generated/payloads.ts` holds exhaustive TypeScript interfaces, one per
topic, generated from the shared `event-schema/` JSON Schemas via
`json-schema-to-typescript`. The file is **committed**; regenerate it after a
schema change with:

```sh
npm run generate-types
```

CI runs the same command and fails if the committed output drifts from the
schemas (the `typescript-checks` job).

## Tests

- **Unit tests** (`test/unit/`, `npm run test:unit`) cover envelope
  construction, idempotency-key derivation, configuration, the dedup store,
  trace-context propagation and the error hierarchy. They need no broker. The
  cross-language parity suite asserts the derived idempotency keys and
  canonical-JSON renderings match values the **Python SDK** produces for the
  same inputs (`test/unit/cross-language-parity.ts`).
- **Integration tests** (`test/integration/`, `npm run test:integration`) run
  a synthetic produce → consume round-trip against a **real RedPanda** — no
  mocks (PRD §7a / §7b) — plus a cross-language wire-interoperability test that
  publishes a Python-produced wire envelope to a real topic and consumes it
  with the TypeScript SDK. They skip themselves when the two
  `TRUSTLIST_EVENT_BUS_*` variables are unset.

Local development — stand up an isolated RedPanda. The `docker-compose.dev.yml`
ports (19092 / 18081) and the issue-13 container's ports are occupied; use
distinct high ports:

```sh
docker run -d --name trustlist-issue14-redpanda \
  -p 19392:19392 -p 18291:18291 -p 18292:18292 \
  redpandadata/redpanda:v24.2.7 \
  redpanda start --mode dev-container --smp 1 \
  --kafka-addr internal://0.0.0.0:9092,external://0.0.0.0:19392 \
  --advertise-kafka-addr internal://localhost:9092,external://localhost:19392 \
  --schema-registry-addr internal://0.0.0.0:8081,external://0.0.0.0:18291 \
  --pandaproxy-addr internal://0.0.0.0:8082,external://0.0.0.0:18292 \
  --rpc-addr 0.0.0.0:33145 --advertise-rpc-addr localhost:33145

export TRUSTLIST_EVENT_BUS_BROKERS=localhost:19392
export TRUSTLIST_SCHEMA_REGISTRY_URL=http://localhost:18291
npm run test:integration
```

In CI the `typescript-integration` job runs inside a container and reaches a
RedPanda service container by its service hostname on the standard ports.

## Cross-language parity — regenerating the reference vectors

`test/unit/cross-language-parity.ts` holds idempotency-key and canonical-JSON
values produced by the **Python** SDK. After a deliberate change to the
derivation, regenerate them by running the Python SDK over the same inputs:

```python
from trustlist_event_bus.idempotency import derive_idempotency_key, _canonical_json
derive_idempotency_key(event_type=..., payload=..., key_fields=(...))
_canonical_json(value)
```

A divergence between the two SDKs is a wire-incompatibility bug; the parity
suite is the regression guard.

## Versioning

The package is versioned independently of `trustlist-core` and follows semver
(`VERSION`). The current version is `0.1.0`.
