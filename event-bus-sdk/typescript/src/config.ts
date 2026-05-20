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
 * Environment-driven configuration for the event-bus SDK.
 *
 * Every connection detail is read from the environment — never hard-coded — so
 * the same SDK code runs unchanged against a local RedPanda container, the CI
 * RedPanda service and RedPanda Cloud BYOC in production (ADR-0011). No
 * credential is ever embedded in code (PRD §7g). This mirrors the Python SDK's
 * `config.py` exactly, including the two environment-variable names, so both
 * SDKs are configured identically.
 *
 * The two environment variables that matter:
 *
 * - `TRUSTLIST_EVENT_BUS_BROKERS` — the Kafka bootstrap servers, a
 *   comma-separated `host:port` list.
 * - `TRUSTLIST_SCHEMA_REGISTRY_URL` — the base URL of the Confluent-API
 *   schema registry (RedPanda's built-in registry).
 */

/** Environment variable naming the Kafka bootstrap-server list. */
export const BROKERS_ENV = 'TRUSTLIST_EVENT_BUS_BROKERS';

/** Environment variable naming the schema-registry base URL. */
export const SCHEMA_REGISTRY_ENV = 'TRUSTLIST_SCHEMA_REGISTRY_URL';

/**
 * Resolved connection settings for the event bus and schema registry.
 *
 * Immutable once built. Construct it from the environment with
 * {@link eventBusConfigFromEnv}; tests may build one directly.
 */
export interface EventBusConfig {
  /** The Kafka bootstrap servers, as a `host:port` list. */
  readonly brokers: readonly string[];
  /** The base URL of the schema registry, with any trailing slash stripped. */
  readonly schemaRegistryUrl: string;
}

/**
 * Build the configuration from environment variables.
 *
 * Failing loudly at start-up is deliberate — a silently-defaulted broker
 * address would let a producer appear healthy while writing nowhere.
 *
 * @throws {Error} when either required variable is unset or empty.
 */
export function eventBusConfigFromEnv(): EventBusConfig {
  const brokersRaw = (process.env[BROKERS_ENV] ?? '').trim();
  const registryRaw = (process.env[SCHEMA_REGISTRY_ENV] ?? '').trim();
  if (brokersRaw.length === 0) {
    throw new Error(
      `${BROKERS_ENV} is unset; the event-bus SDK reads every connection ` +
        'detail from the environment and never hard-codes a broker address.',
    );
  }
  if (registryRaw.length === 0) {
    throw new Error(
      `${SCHEMA_REGISTRY_ENV} is unset; the event-bus SDK reads the ` +
        'schema-registry URL from the environment.',
    );
  }
  const brokers = brokersRaw
    .split(',')
    .map((entry) => entry.trim())
    .filter((entry) => entry.length > 0);
  if (brokers.length === 0) {
    throw new Error(
      `${BROKERS_ENV} contained no usable host:port entries after parsing.`,
    );
  }
  return {
    brokers,
    schemaRegistryUrl: registryRaw.replace(/\/+$/, ''),
  };
}
