// Copyright 2026 The TrustList Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * TrustList event-bus SDK — TypeScript producer and consumer (Stage 0 PRD §7b).
 *
 * This package is the TypeScript half of the event-bus SDK; the Python half is
 * `trustlist_event_bus`. It wraps the RedPanda event bus (ADR-0011, Kafka-wire
 * compatible) with the responsibilities §7b puts on an SDK, and is
 * **wire-interoperable** with the Python SDK: a Python-produced event is
 * consumable by this TypeScript consumer and vice versa, and the same logical
 * event derives a byte-identical idempotency key in either language.
 *
 * - **Typed event-envelope construction** — {@link EventEnvelope} and
 *   {@link newEnvelope} carry every §7b field: `event_id`, `event_type`,
 *   `event_version`, `produced_at`, `producer_id`, `trace_context`,
 *   `idempotency_key`, `provenance` and `payload`.
 * - **Schema-registry integration** — {@link SchemaRegistry} registers and
 *   validates payload JSON Schemas against RedPanda's built-in
 *   Confluent-API-compatible registry; the schema files live under the shared
 *   `event-schema/` directory.
 * - **Idempotency-key derivation** — {@link deriveIdempotencyKey} builds a
 *   stable key from payload-specific fields, byte-identical to Python's.
 * - **Distributed-tracing propagation** — the producer injects the active W3C
 *   trace context into the envelope; the consumer surfaces it to the handler.
 * - **Back-pressure as a typed error** — {@link BackPressureError}; the
 *   producer never blocks silently.
 * - **At-least-once with consumer-side dedup** — {@link EventConsumer} commits
 *   offsets only after the handler succeeds and deduplicates on the
 *   `idempotency_key`.
 *
 * The SDK is versioned independently of `trustlist-core` and follows semver;
 * see `event-bus-sdk/typescript/README.md`.
 */

export { BROKERS_ENV, SCHEMA_REGISTRY_ENV, eventBusConfigFromEnv } from './config';
export type { EventBusConfig } from './config';

export {
  ENVELOPE_VERSION,
  EventEnvelope,
  newEnvelope,
  provenance,
  provenanceFromWire,
  provenanceToWire,
} from './envelope';
export type {
  EventEnvelopeWire,
  NewEnvelopeOptions,
  Provenance,
  ProvenanceWire,
  TraceContext,
} from './envelope';

export {
  assertCanonicalisable,
  canonicalJson,
  deriveIdempotencyKey,
} from './idempotency';
export type {
  DeriveIdempotencyKeyOptions,
  JsonObject,
  JsonValue,
} from './idempotency';

export {
  BackPressureError,
  EventBusError,
  ProduceError,
  SchemaRegistryError,
  SchemaValidationError,
} from './errors';

export { SchemaRegistry, subjectFor } from './schema-registry';

export { listSchemaFiles, loadSchema, registerAll } from './schema-files';
export type { SchemaFile } from './schema-files';

export { ensureTopic } from './admin';
export type { EnsureTopicOptions } from './admin';

export { EventProducer } from './producer';
export type { EventProducerOptions, ProduceOptions } from './producer';

export { EventConsumer } from './consumer';
export type {
  EventConsumerOptions,
  EventHandler,
  SubscribeOptions,
} from './consumer';

export { InMemoryDedupStore } from './dedup-store';
export type { DedupStore } from './dedup-store';

export {
  emptyTraceContextProvider,
  hasValidTraceParent,
  injectTraceContext,
  setTraceContextProvider,
} from './trace-context';
export type { TraceContextProvider } from './trace-context';

// Exhaustive payload types, generated per topic from the shared event-schema/
// JSON Schemas (issue 14 acceptance criteria). Regenerate with
// `npm run generate-types`; the file is committed and CI guards against drift.
export type * from './generated/payloads';

/** The SDK's own semver version, independent of trustlist-core (PRD §7b). */
export const VERSION = '0.1.0';
