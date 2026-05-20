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
 * The event-bus consumer (Stage 0 PRD §7b).
 *
 * {@link EventConsumer} wraps a kafkajs `Consumer` with the consumer-side §7b
 * responsibilities, mirroring the Python SDK's `consumer.py`:
 *
 * 1. **Typed event reconstruction** — each Kafka message is decoded back into
 *    an {@link EventEnvelope}.
 * 2. **Schema validation on consume** — the payload is validated against the
 *    registered JSON Schema for its `event_type` (§7b: "schema validation on
 *    both produce and consume").
 * 3. **Distributed-tracing context propagation** — the envelope's
 *    `trace_context` field is surfaced on the decoded envelope so the handler
 *    can re-parent its span and join the producer's trace.
 * 4. **Idempotency / deduplication** — the consumer tracks recently-seen
 *    `idempotency_key` values via a {@link DedupStore} and skips a redelivery
 *    (§7b: "deduplication at the consumer driven by the idempotency key").
 * 5. **Offset commit after success** — the offset is committed only *after*
 *    the handler returns successfully (§7b: at-least-once delivery; "consumers
 *    commit offsets after successful processing"). A handler exception leaves
 *    the offset uncommitted, so the event is redelivered.
 *
 * Offset discipline. kafkajs auto-commit is disabled; the consumer commits the
 * offset explicitly once the handler resolves. When the handler rejects, the
 * offset stays uncommitted and the message is re-fed — the at-least-once
 * contract. The kafkajs `eachMessage` runner is left to manage the poll loop.
 */

import { Kafka } from 'kafkajs';
import type { Consumer, ConsumerConfig, EachMessagePayload } from 'kafkajs';

import type { EventBusConfig } from './config';
import { eventBusConfigFromEnv } from './config';
import type { DedupStore } from './dedup-store';
import { InMemoryDedupStore } from './dedup-store';
import { EventEnvelope } from './envelope';
import { EventBusError } from './errors';
import { SchemaRegistry } from './schema-registry';

/**
 * A consumer handler — invoked with each decoded, schema-valid envelope.
 *
 * It returns nothing (or a promise of nothing); rejecting or throwing signals
 * a processing failure and leaves the offset uncommitted so the event is
 * redelivered.
 */
export type EventHandler = (envelope: EventEnvelope) => void | Promise<void>;

/** Options accepted by the {@link EventConsumer} constructor. */
export interface EventConsumerOptions {
  /**
   * Resolved connection settings. Defaults to {@link eventBusConfigFromEnv}.
   */
  readonly config?: EventBusConfig;
  /**
   * An explicit {@link SchemaRegistry}; constructed from `config` when omitted.
   */
  readonly schemaRegistry?: SchemaRegistry;
  /**
   * The deduplication store; an {@link InMemoryDedupStore} is used when
   * omitted.
   */
  readonly dedupStore?: DedupStore;
  /**
   * Additional kafkajs consumer settings merged over the SDK defaults.
   */
  readonly extraConfig?: Partial<ConsumerConfig>;
  /** A kafkajs client id; defaults to the `groupId`. */
  readonly clientId?: string;
}

/** Per-subscription options for {@link EventConsumer.subscribe}. */
export interface SubscribeOptions {
  /**
   * When `true` the consumer reads the topic from its beginning on a fresh
   * group; defaults to `true` so no already-published event is missed.
   */
  readonly fromBeginning?: boolean;
}

/**
 * A typed, schema-validating, deduplicating event-bus consumer.
 *
 * Construct it with a Kafka consumer-group id, {@link EventConsumer.subscribe}
 * to one or more topics with a handler, then {@link EventConsumer.run} the
 * poll loop. {@link EventConsumer.close} leaves the group cleanly.
 */
export class EventConsumer {
  private readonly groupId: string;
  private readonly registry: SchemaRegistry;
  private readonly dedup: DedupStore;
  private readonly consumer: Consumer;
  private connected = false;
  private running = false;
  private readonly subscriptions: { topic: string; handler: EventHandler }[] =
    [];

  /**
   * Build a consumer bound to the resolved event-bus configuration.
   *
   * @param groupId the Kafka consumer-group id — co-operating consumer
   *   instances share one group and the partitions are split across them.
   * @param options connection settings, registry, dedup store and overrides.
   */
  public constructor(groupId: string, options: EventConsumerOptions = {}) {
    this.groupId = groupId;
    const resolved = options.config ?? eventBusConfigFromEnv();
    this.registry =
      options.schemaRegistry ?? new SchemaRegistry(resolved.schemaRegistryUrl);
    this.dedup = options.dedupStore ?? new InMemoryDedupStore();
    const kafka = new Kafka({
      clientId: options.clientId ?? groupId,
      brokers: [...resolved.brokers],
    });
    this.consumer = kafka.consumer({
      groupId,
      ...options.extraConfig,
    });
  }

  /** The Kafka consumer-group id this consumer belongs to. */
  public get group(): string {
    return this.groupId;
  }

  /**
   * Subscribe to `topic`, routing its events to `handler`.
   *
   * Call once per topic before {@link EventConsumer.run}. Mirrors the Python
   * SDK's `subscribe(topic, handler, options?)` shape (issue 14 acceptance
   * criteria) — each topic gets its own handler.
   *
   * @param topic the topic to subscribe to.
   * @param handler the callable invoked with each decoded envelope.
   * @param options whether to read the topic from its beginning.
   */
  public async subscribe(
    topic: string,
    handler: EventHandler,
    options: SubscribeOptions = {},
  ): Promise<void> {
    if (this.running) {
      throw new EventBusError(
        'cannot subscribe after the consumer poll loop has started; ' +
          'subscribe to every topic before calling run().',
      );
    }
    await this.ensureConnected();
    await this.consumer.subscribe({
      topic,
      fromBeginning: options.fromBeginning ?? true,
    });
    this.subscriptions.push({ topic, handler });
  }

  /**
   * Start the poll loop and process events until {@link EventConsumer.close}.
   *
   * Each message runs the full §7b consume contract: decode -> schema-validate
   * -> deduplicate -> handle -> commit. A handler rejection leaves the offset
   * uncommitted; kafkajs re-feeds the message — the at-least-once contract.
   *
   * @param options.onProcessed an optional callback fired after each event is
   *   processed or recognised as a duplicate — the integration tests use it to
   *   count a deterministic round-trip.
   */
  public async run(options: {
    onProcessed?: (envelope: EventEnvelope, deduplicated: boolean) => void;
  } = {}): Promise<void> {
    if (this.subscriptions.length === 0) {
      throw new EventBusError(
        'run() called with no subscriptions; subscribe to a topic first.',
      );
    }
    this.running = true;
    const handlers = new Map(
      this.subscriptions.map((sub) => [sub.topic, sub.handler]),
    );
    await this.consumer.run({
      // Auto-commit is disabled: the SDK commits explicitly *after* the
      // handler succeeds (PRD §7b at-least-once).
      autoCommit: false,
      eachMessage: async (payload: EachMessagePayload): Promise<void> => {
        await this.processMessage(payload, handlers, options.onProcessed);
      },
    });
  }

  private async processMessage(
    payload: EachMessagePayload,
    handlers: Map<string, EventHandler>,
    onProcessed?: (envelope: EventEnvelope, deduplicated: boolean) => void,
  ): Promise<void> {
    const { topic, partition, message } = payload;
    const handler = handlers.get(topic);
    if (handler === undefined) {
      // A topic with no registered handler should not be reachable, but skip
      // defensively rather than crash the loop.
      return;
    }
    const envelope = this.decode(message.value);

    // Schema validation on consume (PRD §7b). A schema-invalid event is not
    // committed; the rejection re-feeds it for operator triage rather than
    // silently advancing past it.
    await this.registry.validate(envelope.eventType, envelope.payload);

    if (await this.dedup.seen(envelope.idempotencyKey)) {
      // A redelivery of an already-processed event. It has been handled;
      // commit the offset so the consumer moves past it.
      await this.commit(topic, partition, message.offset);
      onProcessed?.(envelope, true);
      return;
    }

    // Run the handler. On rejection the offset stays uncommitted, so kafkajs
    // re-feeds the message — the at-least-once contract (PRD §7b).
    await handler(envelope);

    await this.dedup.record(envelope.idempotencyKey);
    await this.commit(topic, partition, message.offset);
    onProcessed?.(envelope, false);
  }

  private decode(value: Buffer | null): EventEnvelope {
    if (value === null) {
      throw new EventBusError(
        'event-bus message has an empty body; cannot decode an envelope.',
      );
    }
    try {
      return EventEnvelope.fromBytes(value);
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      throw new EventBusError(
        `event-bus message is not a valid envelope: ${message}`,
      );
    }
  }

  private async commit(
    topic: string,
    partition: number,
    offset: string,
  ): Promise<void> {
    // Commit the *next* offset to read — Kafka commits mark the position to
    // resume from, which is one past the message just processed.
    await this.consumer.commitOffsets([
      { topic, partition, offset: (BigInt(offset) + 1n).toString() },
    ]);
  }

  private async ensureConnected(): Promise<void> {
    if (this.connected) {
      return;
    }
    await this.consumer.connect();
    this.connected = true;
  }

  /**
   * Stop the poll loop and leave the consumer group cleanly.
   */
  public async close(): Promise<void> {
    this.running = false;
    if (this.connected) {
      await this.consumer.disconnect();
      this.connected = false;
    }
  }
}
