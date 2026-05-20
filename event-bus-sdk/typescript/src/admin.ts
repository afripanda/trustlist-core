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
 * Topic administration for the event-bus SDK.
 *
 * A small wrapper over kafkajs's admin client for creating topics, mirroring
 * the Python SDK's `admin.ensure_topic`. Stage 0 issue 11 owns the
 * *deployment* of the §7b topic set against RedPanda Cloud; this helper gives
 * the SDK a programmatic way to ensure a topic exists, which the integration
 * tests use to provision a deterministic, known-partition topic before a
 * round-trip rather than relying on broker-side auto-creation.
 */

import { Kafka } from 'kafkajs';

import type { EventBusConfig } from './config';
import { eventBusConfigFromEnv } from './config';
import { EventBusError } from './errors';

/** Options for {@link ensureTopic}. */
export interface EnsureTopicOptions {
  /** Resolved connection settings; `eventBusConfigFromEnv()` when omitted. */
  readonly config?: EventBusConfig;
  /**
   * Partition count for a newly-created topic. §7b partitions by `domain_id`;
   * a single partition is enough for the synthetic round-trip, real topics
   * size this for throughput. Defaults to `1`.
   */
  readonly numPartitions?: number;
  /**
   * Replication factor for a newly-created topic. `1` suits a single-broker
   * dev cluster; production uses the cluster default. Defaults to `1`.
   */
  readonly replicationFactor?: number;
}

/**
 * Create `topic` if it does not already exist.
 *
 * Creating an existing topic is treated as success — the operation is
 * idempotent, which is what makes it safe to call from a test set-up step.
 *
 * @param topic the topic name to ensure.
 * @param options partition count, replication factor and connection settings.
 * @throws {EventBusError} when the broker rejects the create for a reason
 *   other than "topic already exists".
 */
export async function ensureTopic(
  topic: string,
  options: EnsureTopicOptions = {},
): Promise<void> {
  const resolved = options.config ?? eventBusConfigFromEnv();
  const kafka = new Kafka({
    clientId: 'trustlist-event-bus-admin',
    brokers: [...resolved.brokers],
  });
  const admin = kafka.admin();
  await admin.connect();
  try {
    // kafkajs's createTopics returns false (not an error) when every named
    // topic already exists — exactly the idempotent-"ensure" semantics.
    await admin.createTopics({
      topics: [
        {
          topic,
          numPartitions: options.numPartitions ?? 1,
          replicationFactor: options.replicationFactor ?? 1,
        },
      ],
      waitForLeaders: true,
    });
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    if (message.toLowerCase().includes('already exists')) {
      return;
    }
    throw new EventBusError(`failed to create topic '${topic}': ${message}`);
  } finally {
    await admin.disconnect();
  }
}
