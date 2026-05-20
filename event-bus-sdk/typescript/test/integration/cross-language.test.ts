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
 * Cross-language wire-interoperability integration test — real RedPanda.
 *
 * Proves the §7b cross-language contract end to end on a real broker: a wire
 * envelope byte-identical to what the **Python** SDK puts on the bus is
 * published to a real topic with a raw kafkajs producer, and the **TypeScript**
 * `EventConsumer` reads it back, schema-validates it and decodes it into a
 * typed envelope. The published bytes are exactly the output of the Python
 * SDK's `EventEnvelope.to_bytes()` for the listed input (verified on
 * 2026-05-20; see event-bus-sdk/typescript/README.md for the regeneration
 * snippet) — so a green run means a Python-produced event is consumable by the
 * TypeScript SDK without translation.
 *
 * The symmetric direction (a TypeScript-produced event consumed by Python) is
 * covered by the byte-identical envelope and idempotency-key parity asserted
 * in the unit suite: both SDKs emit the same wire JSON, so consumption is
 * symmetric by construction.
 */

import { randomUUID } from 'node:crypto';

import { Kafka } from 'kafkajs';

import {
  BROKERS_ENV,
  SCHEMA_REGISTRY_ENV,
  type EventBusConfig,
  eventBusConfigFromEnv,
} from '../../src/config';
import { EventConsumer } from '../../src/consumer';
import { EventEnvelope } from '../../src/envelope';
import { deriveIdempotencyKey } from '../../src/idempotency';
import { SchemaRegistry } from '../../src/schema-registry';
import { registerAll } from '../../src/schema-files';
import { ensureTopic } from '../../src/admin';

const EVENT_TYPE = 'signal.tier-one.example-collector';

const brokersSet =
  (process.env[BROKERS_ENV] ?? '').length > 0 &&
  (process.env[SCHEMA_REGISTRY_ENV] ?? '').length > 0;
const describeIntegration = brokersSet ? describe : describe.skip;

// The exact bytes the Python SDK's EventEnvelope.to_bytes() produces for the
// example-collector signal below (no incidental whitespace, snake_case fields,
// produced_at as an ISO-8601 string with a +00:00 offset, and an
// idempotency_key derived by the Python SDK's derive_idempotency_key).
// Verified by running the Python SDK on 2026-05-20.
const PYTHON_DOMAIN_ID = '3f8b9c2a-1d4e-4f6a-9b8c-2d1e3f4a5b6c';
const PYTHON_IDEMPOTENCY_KEY =
  '94db82fcfb28155e4e3e546c6ff0742388377bb811307e447b3d8cf0e01e257f';
const PYTHON_PRODUCED_WIRE =
  `{"event_id":"${PYTHON_DOMAIN_ID}",` +
  '"event_type":"signal.tier-one.example-collector",' +
  '"event_version":"1.0.0",' +
  '"produced_at":"2026-05-20T12:00:00+00:00",' +
  '"producer_id":"py-collector",' +
  '"trace_context":{"traceparent":' +
  '"00-abcdef0123456789abcdef0123456789-0123456789abcdef-01"},' +
  `"idempotency_key":"${PYTHON_IDEMPOTENCY_KEY}",` +
  '"provenance":{"source":"system","method":"dns-probe",' +
  '"contributor_identity":null},' +
  `"payload":{"domain_id":"${PYTHON_DOMAIN_ID}","signal_class":"dns",` +
  '"source_url":"","observed_at":"2026-05-20T12:00:00+00:00",' +
  '"observed_value":{"resolves":true}}}';

describeIntegration('cross-language wire interoperability', () => {
  let config: EventBusConfig;
  let registry: SchemaRegistry;

  beforeAll(async () => {
    config = eventBusConfigFromEnv();
    registry = new SchemaRegistry(config.schemaRegistryUrl);
    await registerAll(registry);
  });

  it('consumes a Python-produced wire envelope off a real broker', async () => {
    const topic = `${EVENT_TYPE}.xlang-${randomUUID().slice(0, 12)}`;
    await ensureTopic(topic, { config });

    // Publish the exact Python wire bytes with a raw kafkajs producer — the
    // TypeScript SDK plays no part in producing them.
    const kafka = new Kafka({
      clientId: 'xlang-raw-producer',
      brokers: [...config.brokers],
    });
    const rawProducer = kafka.producer();
    await rawProducer.connect();
    try {
      await rawProducer.send({
        topic,
        messages: [
          {
            key: Buffer.from(PYTHON_DOMAIN_ID, 'utf8'),
            value: Buffer.from(PYTHON_PRODUCED_WIRE, 'utf8'),
          },
        ],
      });
    } finally {
      await rawProducer.disconnect();
    }

    // The TypeScript consumer reads, schema-validates and decodes it.
    const received: EventEnvelope[] = [];
    const consumer = new EventConsumer(
      `xlang-group-${randomUUID().slice(0, 8)}`,
      { config, schemaRegistry: registry },
    );
    try {
      await consumer.subscribe(
        topic,
        (envelope) => {
          received.push(envelope);
        },
        { fromBeginning: true },
      );
      await new Promise<void>((resolve, reject) => {
        consumer
          .run({
            onProcessed: () => {
              if (received.length >= 1) {
                resolve();
              }
            },
          })
          .catch(reject);
      });
    } finally {
      await consumer.close();
    }

    expect(received).toHaveLength(1);
    const got = received[0] as EventEnvelope;
    expect(got.eventId).toBe(PYTHON_DOMAIN_ID);
    expect(got.eventType).toBe(EVENT_TYPE);
    expect(got.producerId).toBe('py-collector');
    expect(got.producedAt).toBe('2026-05-20T12:00:00+00:00');
    expect(got.idempotencyKey).toBe(PYTHON_IDEMPOTENCY_KEY);
    expect(got.provenance).toEqual({
      source: 'system',
      method: 'dns-probe',
      contributorIdentity: null,
    });
    expect(got.payload).toEqual({
      domain_id: PYTHON_DOMAIN_ID,
      signal_class: 'dns',
      source_url: '',
      observed_at: '2026-05-20T12:00:00+00:00',
      observed_value: { resolves: true },
    });

    // The TypeScript SDK re-serialises it byte-identically — a TypeScript
    // producer would put the same bytes on the wire a Python consumer reads.
    expect(got.toBytes().toString('utf8')).toBe(PYTHON_PRODUCED_WIRE);

    // ...and the TypeScript SDK derives the same idempotency key the Python
    // SDK stamped into the envelope it produced — proving cross-language
    // deduplication holds for an event flowing Python -> TypeScript.
    expect(
      deriveIdempotencyKey({
        eventType: EVENT_TYPE,
        payload: got.payload,
        keyFields: ['domain_id', 'signal_class', 'observed_at'],
      }),
    ).toBe(got.idempotencyKey);
  });
});
