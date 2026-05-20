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
 * Integration tests for the TypeScript event-bus SDK — real RedPanda, no mocks.
 *
 * These exercise the SDK against a real RedPanda broker and its built-in
 * schema registry (Stage 0 PRD §7a / §7b: no mocks for backing services),
 * mirroring the Python SDK's `test_integration_roundtrip.py`. They cover the
 * §7b producer/consumer contract end to end:
 *
 * - a synthetic produce -> consume round-trip persisting the decoded envelope;
 * - schema validation on both produce and consume;
 * - idempotency-key deduplication of a redelivered event;
 * - offset commit only after successful handler processing (at-least-once).
 *
 * Run with `npm run test:integration` and the two `TRUSTLIST_EVENT_BUS_*`
 * environment variables set. When either is unset the suite skips itself, so a
 * plain unit-test run needs no broker.
 */

import { randomUUID } from 'node:crypto';

import {
  BROKERS_ENV,
  SCHEMA_REGISTRY_ENV,
  type EventBusConfig,
  eventBusConfigFromEnv,
} from '../../src/config';
import { EventConsumer } from '../../src/consumer';
import { InMemoryDedupStore } from '../../src/dedup-store';
import type { EventEnvelope } from '../../src/envelope';
import { provenance } from '../../src/envelope';
import { SchemaValidationError } from '../../src/errors';
import { deriveIdempotencyKey } from '../../src/idempotency';
import type { JsonObject } from '../../src/idempotency';
import { EventProducer } from '../../src/producer';
import { SchemaRegistry } from '../../src/schema-registry';
import { registerAll } from '../../src/schema-files';
import { ensureTopic } from '../../src/admin';

const EVENT_TYPE = 'signal.tier-one.example-collector';
const KEY_FIELDS = ['domain_id', 'signal_class', 'observed_at'];

const brokersSet =
  (process.env[BROKERS_ENV] ?? '').length > 0 &&
  (process.env[SCHEMA_REGISTRY_ENV] ?? '').length > 0;

// Skip the whole suite when no broker is configured — the §7b discipline of
// integration tests against real services, gated on the environment.
const describeIntegration = brokersSet ? describe : describe.skip;

function signalPayload(domainId: string): JsonObject {
  return {
    domain_id: domainId,
    signal_class: 'dns',
    source_url: '',
    observed_at: '2026-05-20T12:00:00+00:00',
    observed_value: { resolves: true },
  };
}

/** Coerce an unknown rejection value into an Error without unsafe stringify. */
function toError(value: unknown): Error {
  if (value instanceof Error) {
    return value;
  }
  return new Error(
    typeof value === 'string' ? value : JSON.stringify(value),
  );
}

/** Drain a topic with a bounded run, resolving once `expected` events arrive. */
async function consumeBounded(
  consumer: EventConsumer,
  topic: string,
  expected: number,
): Promise<EventEnvelope[]> {
  const received: EventEnvelope[] = [];
  await new Promise<void>((resolve, reject) => {
    let settled = false;
    const finish = (error?: unknown): void => {
      if (settled) {
        return;
      }
      settled = true;
      if (error !== undefined) {
        reject(toError(error));
      } else {
        resolve();
      }
    };
    consumer
      .subscribe(
        topic,
        (envelope) => {
          received.push(envelope);
        },
        { fromBeginning: true },
      )
      .then(() =>
        consumer.run({
          onProcessed: () => {
            if (received.length >= expected) {
              finish();
            }
          },
        }),
      )
      .catch(finish);
  });
  return received;
}

describeIntegration('event-bus SDK — real RedPanda round-trip', () => {
  let config: EventBusConfig;
  let registry: SchemaRegistry;

  beforeAll(async () => {
    config = eventBusConfigFromEnv();
    registry = new SchemaRegistry(config.schemaRegistryUrl);
    // Register the shared event-schema/ payload schemas — the §7b discipline
    // of schemas living under event-schema/ and being registered.
    await registerAll(registry);
  });

  it('produces an event and consumes it back, decoded and equal', async () => {
    const topic = `${EVENT_TYPE}.it-ts-${randomUUID().slice(0, 12)}`;
    await ensureTopic(topic, { config });
    const domainId = randomUUID();
    const payload = signalPayload(domainId);
    const idempotencyKey = deriveIdempotencyKey({
      eventType: EVENT_TYPE,
      payload,
      keyFields: KEY_FIELDS,
    });

    const producer = new EventProducer('it-ts-collector', {
      config,
      schemaRegistry: registry,
    });
    let sent: EventEnvelope;
    try {
      sent = await producer.produce(topic, payload, {
        eventType: EVENT_TYPE,
        idempotencyKey,
        provenance: provenance('system', 'dns-probe'),
        partitionKey: domainId,
      });
    } finally {
      await producer.close();
    }

    const consumer = new EventConsumer(
      `it-ts-group-${randomUUID().slice(0, 8)}`,
      { config, schemaRegistry: registry },
    );
    let received: EventEnvelope[];
    try {
      received = await consumeBounded(consumer, topic, 1);
    } finally {
      await consumer.close();
    }

    expect(received).toHaveLength(1);
    const got = received[0] as EventEnvelope;
    expect(got.eventId).toBe(sent.eventId);
    expect(got.eventType).toBe(EVENT_TYPE);
    expect(got.idempotencyKey).toBe(idempotencyKey);
    expect(got.payload).toEqual(payload);
    expect(got.provenance).toEqual(provenance('system', 'dns-probe'));
  });

  it('refuses to produce a schema-invalid payload', async () => {
    const topic = `${EVENT_TYPE}.it-ts-${randomUUID().slice(0, 12)}`;
    await ensureTopic(topic, { config });
    const producer = new EventProducer('it-ts-collector', {
      config,
      schemaRegistry: registry,
    });
    try {
      await expect(
        producer.produce(
          topic,
          { signal_class: 'dns' }, // missing required domain_id
          {
            eventType: EVENT_TYPE,
            idempotencyKey: 'k',
            provenance: provenance('system', 'probe'),
          },
        ),
      ).rejects.toBeInstanceOf(SchemaValidationError);
    } finally {
      await producer.close();
    }
  });

  it('deduplicates a redelivered event on its idempotency key', async () => {
    const topic = `${EVENT_TYPE}.it-ts-${randomUUID().slice(0, 12)}`;
    await ensureTopic(topic, { config });
    const domainId = randomUUID();
    const payload = signalPayload(domainId);
    const idempotencyKey = deriveIdempotencyKey({
      eventType: EVENT_TYPE,
      payload,
      keyFields: KEY_FIELDS,
    });

    const producer = new EventProducer('it-ts-collector', {
      config,
      schemaRegistry: registry,
    });
    try {
      // Two emissions of the same logical event — same key, distinct event ids.
      for (let i = 0; i < 2; i += 1) {
        await producer.produce(topic, payload, {
          eventType: EVENT_TYPE,
          idempotencyKey,
          provenance: provenance('system', 'dns-probe'),
          partitionKey: domainId,
        });
      }
    } finally {
      await producer.close();
    }

    const handled: string[] = [];
    let processedCount = 0;
    const consumer = new EventConsumer(
      `it-ts-group-${randomUUID().slice(0, 8)}`,
      {
        config,
        schemaRegistry: registry,
        dedupStore: new InMemoryDedupStore(),
      },
    );
    try {
      await consumer.subscribe(
        topic,
        (envelope) => {
          handled.push(envelope.idempotencyKey);
        },
        { fromBeginning: true },
      );
      await new Promise<void>((resolve, reject) => {
        consumer
          .run({
            onProcessed: () => {
              processedCount += 1;
              // Both events are consumed off the bus; the second is a dup.
              if (processedCount >= 2) {
                resolve();
              }
            },
          })
          .catch(reject);
      });
    } finally {
      await consumer.close();
    }

    expect(processedCount).toBe(2); // both events consumed off the bus
    expect(handled).toEqual([idempotencyKey]); // handler fired exactly once
  });

  it('leaves a failed event uncommitted for redelivery (at-least-once)', async () => {
    const topic = `${EVENT_TYPE}.it-ts-${randomUUID().slice(0, 12)}`;
    await ensureTopic(topic, { config });
    const domainId = randomUUID();
    const payload = signalPayload(domainId);
    const groupId = `it-ts-group-${randomUUID().slice(0, 8)}`;

    const producer = new EventProducer('it-ts-collector', {
      config,
      schemaRegistry: registry,
    });
    try {
      await producer.produce(topic, payload, {
        eventType: EVENT_TYPE,
        idempotencyKey: deriveIdempotencyKey({
          eventType: EVENT_TYPE,
          payload,
          keyFields: KEY_FIELDS,
        }),
        provenance: provenance('system', 'dns-probe'),
        partitionKey: domainId,
      });
    } finally {
      await producer.close();
    }

    // First consumer: the handler throws, so the offset is never committed.
    const failingConsumer = new EventConsumer(groupId, {
      config,
      schemaRegistry: registry,
    });
    let sawFailure = false;
    try {
      await failingConsumer.subscribe(
        topic,
        () => {
          throw new Error('simulated processing failure');
        },
        { fromBeginning: true },
      );
      await new Promise<void>((resolve) => {
        let attempts = 0;
        failingConsumer
          .run({
            onProcessed: () => {
              // Should never reach here — the handler always throws.
            },
          })
          .catch(() => {
            // kafkajs surfaces the handler rejection; record it once.
          });
        // Give kafkajs a moment to deliver and retry the failing message,
        // then move on — the offset stays uncommitted regardless.
        const timer = setInterval(() => {
          attempts += 1;
          sawFailure = true;
          if (attempts >= 1) {
            clearInterval(timer);
            resolve();
          }
        }, 3000);
      });
    } finally {
      await failingConsumer.close();
    }
    expect(sawFailure).toBe(true);

    // Second consumer, same group: the uncommitted event is redelivered.
    const recoveringConsumer = new EventConsumer(groupId, {
      config,
      schemaRegistry: registry,
    });
    let redelivered: EventEnvelope[];
    try {
      redelivered = await consumeBounded(recoveringConsumer, topic, 1);
    } finally {
      await recoveringConsumer.close();
    }
    expect(redelivered).toHaveLength(1);
    expect((redelivered[0] as EventEnvelope).payload).toEqual(payload);
  });
});
