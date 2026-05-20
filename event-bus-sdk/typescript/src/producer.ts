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
 * The event-bus producer (Stage 0 PRD §7b).
 *
 * {@link EventProducer} wraps a kafkajs `Producer` with the four SDK
 * responsibilities §7b puts on the producer, mirroring the Python SDK's
 * `producer.py`:
 *
 * 1. **Typed event construction** — {@link EventProducer.produce} builds a full
 *    {@link EventEnvelope} (generating `event_id` and `produced_at`).
 * 2. **Schema validation on produce** — the payload is validated against the
 *    registered JSON Schema for its `event_type` before anything is sent.
 * 3. **Distributed-tracing context propagation** — the active W3C trace
 *    context is injected into the envelope's `trace_context` field, so a
 *    consumer's span joins this producer's trace.
 * 4. **Back-pressure as a typed error** — when the underlying client's send
 *    buffer is saturated the SDK raises {@link BackPressureError} rather than
 *    blocking. A producer must *never* block silently (§7b).
 *
 * Partitioning. §7b: topics are partitioned by `domain_id` where applicable to
 * preserve per-domain ordering. The producer uses the supplied `partitionKey`
 * as the Kafka message key; the default partitioner then maps equal keys to
 * the same partition. Callers pass the `domain_id` (or `user_id` for
 * `auth.audit`) as the key.
 *
 * Kafka client. The SDK uses **kafkajs** — the de-facto-standard pure-JavaScript
 * Kafka client, with no native dependency to compile. It is the natural
 * counterpart to the Python SDK's librdkafka-based `confluent-kafka`: both
 * speak the Kafka wire protocol, which is all the cross-language
 * interoperability requires.
 */

import { Kafka } from 'kafkajs';
import type { Producer, ProducerConfig } from 'kafkajs';

import type { EventBusConfig } from './config';
import { eventBusConfigFromEnv } from './config';
import type { Provenance } from './envelope';
import { EventEnvelope, newEnvelope } from './envelope';
import { BackPressureError, ProduceError } from './errors';
import type { JsonObject } from './idempotency';
import { SchemaRegistry } from './schema-registry';
import { injectTraceContext } from './trace-context';

/** Options accepted by the {@link EventProducer} constructor. */
export interface EventProducerOptions {
  /**
   * Resolved connection settings. Defaults to {@link eventBusConfigFromEnv} so
   * production code needs no arguments.
   */
  readonly config?: EventBusConfig;
  /**
   * An explicit {@link SchemaRegistry}; constructed from `config` when omitted.
   * Tests inject one to share a registry.
   */
  readonly schemaRegistry?: SchemaRegistry;
  /**
   * Additional kafkajs producer settings merged over the SDK defaults — an
   * escape hatch, rarely needed.
   */
  readonly extraConfig?: Partial<ProducerConfig>;
  /** A kafkajs client id; defaults to the `producerId`. */
  readonly clientId?: string;
}

/** Per-call options for {@link EventProducer.produce}. */
export interface ProduceOptions {
  /**
   * The topic plus payload-type qualifier; also the schema-registry key.
   */
  readonly eventType: string;
  /**
   * The deduplication key; derive it with `deriveIdempotencyKey`.
   */
  readonly idempotencyKey: string;
  /** The observation's origin (§7a). */
  readonly provenance: Provenance;
  /**
   * The Kafka message key — pass `domain_id` (or `user_id` for `auth.audit`)
   * to preserve per-key ordering.
   */
  readonly partitionKey?: string;
  /**
   * The payload schema version; defaults to the envelope version when omitted.
   */
  readonly eventVersion?: string;
  /**
   * An explicit `event_id`; generated when omitted. Supplying it makes a
   * produce call reproducible in a test fixture.
   */
  readonly eventId?: string;
  /**
   * An explicit `produced_at` ISO-8601 string; the current UTC instant is used
   * when omitted.
   */
  readonly producedAt?: string;
}

/**
 * A typed, schema-validating, trace-propagating event-bus producer.
 *
 * Construct it with a `producerId` (stamped into every envelope) and connect
 * once with {@link EventProducer.connect} before producing; {@link EventProducer.close}
 * flushes and disconnects.
 */
export class EventProducer {
  private readonly producerId: string;
  private readonly registry: SchemaRegistry;
  private readonly producer: Producer;
  private connected = false;

  /**
   * Build a producer bound to the resolved event-bus configuration.
   *
   * @param producerId identifies this component instance; stamped into every
   *   envelope's `producer_id` field.
   * @param options connection settings, registry and kafkajs overrides.
   */
  public constructor(producerId: string, options: EventProducerOptions = {}) {
    this.producerId = producerId;
    const resolved = options.config ?? eventBusConfigFromEnv();
    this.registry =
      options.schemaRegistry ?? new SchemaRegistry(resolved.schemaRegistryUrl);
    const kafka = new Kafka({
      clientId: options.clientId ?? producerId,
      brokers: [...resolved.brokers],
    });
    this.producer = kafka.producer({
      // At-least-once delivery with no silent loss: the idempotent producer
      // gives exactly-once-per-partition semantics over the at-least-once bus
      // (PRD §7b). kafkajs defaults `acks` to -1 (all in-sync replicas) when
      // idempotent is enabled.
      idempotent: true,
      ...options.extraConfig,
    });
  }

  /** The component-instance id stamped into every envelope. */
  public get id(): string {
    return this.producerId;
  }

  /**
   * Connect to the broker. Idempotent — a second call is a no-op.
   *
   * {@link EventProducer.produce} connects lazily on first use, so an explicit
   * call is optional but lets a caller surface connection failures up front.
   */
  public async connect(): Promise<void> {
    if (this.connected) {
      return;
    }
    await this.producer.connect();
    this.connected = true;
  }

  /**
   * Construct, validate and publish an event.
   *
   * @param topic the Kafka topic — one of the §7b topic set.
   * @param payload the event-type-specific body.
   * @param options the event type, idempotency key, provenance and optional
   *   envelope overrides.
   * @returns the {@link EventEnvelope} that was published.
   * @throws {SchemaValidationError} when the payload fails schema validation —
   *   the event is *not* sent.
   * @throws {BackPressureError} when the underlying client's buffer is
   *   saturated. The caller must decide how to degrade; the SDK never blocks
   *   silently.
   * @throws {ProduceError} when the produce call fails for any other reason
   *   (unknown topic, broker unreachable, ...).
   */
  public async produce(
    topic: string,
    payload: JsonObject,
    options: ProduceOptions,
  ): Promise<EventEnvelope> {
    // Schema validation first — a malformed payload never reaches the bus.
    await this.registry.validate(options.eventType, payload);

    // Inject the active W3C trace context so the consumer span joins this
    // trace. With no active span the carrier is simply empty.
    const traceContext = injectTraceContext();

    const envelope = newEnvelope({
      eventType: options.eventType,
      payload,
      producerId: this.producerId,
      idempotencyKey: options.idempotencyKey,
      provenance: options.provenance,
      traceContext,
      eventVersion: options.eventVersion,
      eventId: options.eventId,
      producedAt: options.producedAt,
    });

    await this.connect();

    try {
      await this.producer.send({
        topic,
        messages: [
          {
            key:
              options.partitionKey !== undefined
                ? Buffer.from(options.partitionKey, 'utf8')
                : null,
            value: envelope.toBytes(),
          },
        ],
      });
    } catch (cause) {
      throw classifyProduceFailure(topic, cause);
    }
    return envelope;
  }

  /**
   * Flush and disconnect the producer; call before discarding it.
   *
   * kafkajs's `send` resolves only once the broker has acknowledged, so there
   * is no separate buffered-message flush — `disconnect` is the clean shutdown.
   */
  public async close(): Promise<void> {
    if (!this.connected) {
      return;
    }
    await this.producer.disconnect();
    this.connected = false;
  }
}

/**
 * Map a kafkajs send failure onto the SDK's typed error hierarchy.
 *
 * A saturated send buffer (kafkajs raises `KafkaJSError` with a queue-full
 * retriable signature) becomes {@link BackPressureError} — the §7b
 * back-pressure contract; everything else becomes {@link ProduceError}.
 */
function classifyProduceFailure(topic: string, cause: unknown): ProduceError {
  const message = cause instanceof Error ? cause.message : String(cause);
  const lower = message.toLowerCase();
  if (
    lower.includes('queue full') ||
    lower.includes('buffer') ||
    lower.includes('the producer is busy')
  ) {
    return new BackPressureError(
      `event-bus producer buffer is saturated while producing to ` +
        `'${topic}'; the bus is applying back-pressure. The caller must ` +
        'degrade gracefully — the SDK does not block.',
    );
  }
  return new ProduceError(`failed to produce to '${topic}': ${message}`);
}
