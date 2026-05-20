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
 * Schema-registry integration for the event-bus SDK (Stage 0 PRD §7b).
 *
 * RedPanda ships a built-in, Confluent-API-compatible schema registry
 * (ADR-0011). This module wraps it, mirroring the Python SDK's
 * `schema_registry.py`:
 *
 * - registering a payload's JSON Schema under a subject keyed by `event_type`;
 * - fetching the latest registered schema for an `event_type`;
 * - validating a payload against that schema, on both the produce and the
 *   consume path (PRD §7b: "schema validation on both produce and consume").
 *
 * Design choice — the *wire* format stays plain JSON. The SDK does not use the
 * Confluent magic-byte / schema-id framing: the envelope (see `envelope.ts`)
 * is self-describing JSON, and coupling the wire bytes to a registry
 * round-trip would make an event un-decodable without the registry. Instead
 * the registry is used as the *contract store* — schemas are registered there
 * and CI validates `event-schema/` against it (PRD §7b) — while validation at
 * runtime is done locally with Ajv against the schema the registry holds. The
 * schema is fetched once per `event_type` and cached for the registry
 * instance's lifetime.
 *
 * The subject-naming strategy is the Confluent `TopicNameStrategy` analogue
 * keyed on `event_type`: the subject is `"<event_type>-value"`, identical to
 * the Python SDK's `subject_for`.
 */

import Ajv2020 from 'ajv/dist/2020';
import type { ValidateFunction } from 'ajv';
import addFormats from 'ajv-formats';

import { SchemaRegistryError, SchemaValidationError } from './errors';
import type { JsonObject } from './idempotency';

/** The schema-type string the Confluent / RedPanda registry uses for JSON Schema. */
const JSON_SCHEMA_TYPE = 'JSON';

/** The Confluent registry's JSON content type. */
const SR_CONTENT_TYPE = 'application/vnd.schemaregistry.v1+json';

/**
 * Return the registry subject name for an `eventType`.
 *
 * Mirrors Confluent's `TopicNameStrategy` `-value` suffix convention — and the
 * Python SDK's `subject_for` — so the subjects are legible to any standard
 * schema-registry tooling and identical across the two SDKs.
 *
 * @param eventType the event type.
 * @returns the registry subject name.
 */
export function subjectFor(eventType: string): string {
  return `${eventType}-value`;
}

interface RegisteredSchemaResponse {
  readonly id: number;
}

interface LatestVersionResponse {
  readonly schema: string;
  readonly schemaType?: string;
}

/**
 * A thin wrapper over the RedPanda / Confluent schema registry.
 *
 * One instance is shared by a producer or a consumer. It owns the base URL of
 * the registry and an in-process cache of compiled JSON Schema validators,
 * keyed by `event_type`.
 */
export class SchemaRegistry {
  private readonly baseUrl: string;
  private readonly ajv: Ajv2020;
  private readonly validatorCache = new Map<string, ValidateFunction>();

  /**
   * Open a registry client against `url`.
   *
   * @param url the registry base URL — pass `EventBusConfig.schemaRegistryUrl`.
   */
  public constructor(url: string) {
    this.baseUrl = url.replace(/\/+$/, '');
    this.ajv = new Ajv2020({ allErrors: true, strict: false });
    addFormats(this.ajv);
  }

  /**
   * Register `schema` as the JSON Schema for `eventType`.
   *
   * Registration is idempotent at the registry: re-registering an identical
   * schema returns the existing schema id. This is what the CI `event-schema`
   * validation step calls to keep the registry in step with the JSON Schema
   * files committed under `event-schema/`.
   *
   * @param eventType the event type the schema describes.
   * @param schema the JSON Schema document.
   * @returns the registry-assigned schema id.
   * @throws {SchemaRegistryError} when the registry cannot be reached or
   *   rejects the schema.
   */
  public async register(eventType: string, schema: JsonObject): Promise<number> {
    const subject = subjectFor(eventType);
    const body = JSON.stringify({
      schema: JSON.stringify(schema),
      schemaType: JSON_SCHEMA_TYPE,
    });
    let response: Response;
    try {
      response = await fetch(
        `${this.baseUrl}/subjects/${encodeURIComponent(subject)}/versions`,
        {
          method: 'POST',
          headers: { 'Content-Type': SR_CONTENT_TYPE },
          body,
        },
      );
    } catch (cause) {
      throw new SchemaRegistryError(
        `failed to reach the schema registry while registering schema for ` +
          `event_type '${eventType}': ${describeCause(cause)}`,
      );
    }
    if (!response.ok) {
      throw new SchemaRegistryError(
        `failed to register schema for event_type '${eventType}': ` +
          `registry responded ${String(response.status)} ` +
          `${await safeBody(response)}`,
      );
    }
    const parsed = (await response.json()) as RegisteredSchemaResponse;
    return parsed.id;
  }

  /**
   * Validate `payload` against the registered schema for its type.
   *
   * Called by the producer before publishing and by the consumer before the
   * handler runs (PRD §7b).
   *
   * @param eventType the event type whose schema to validate against.
   * @param payload the payload to validate.
   * @throws {SchemaValidationError} when the payload does not conform.
   * @throws {SchemaRegistryError} when the schema cannot be retrieved.
   */
  public async validate(eventType: string, payload: JsonObject): Promise<void> {
    const validator = await this.getValidator(eventType);
    if (!validator(payload)) {
      const detail = (validator.errors ?? [])
        .map((err) => `${err.instancePath || '/'} ${err.message ?? ''}`.trim())
        .join('; ');
      throw new SchemaValidationError(
        `payload for event_type '${eventType}' failed schema validation: ` +
          `${detail || 'payload did not conform to the registered schema'}`,
      );
    }
  }

  /**
   * Return a compiled validator for `eventType`, fetching the schema once.
   *
   * The latest schema registered for the `eventType` subject is fetched from
   * the registry, compiled into an Ajv validator and cached for the registry
   * instance's lifetime.
   *
   * @param eventType the event type whose validator to obtain.
   * @returns the compiled validator.
   * @throws {SchemaRegistryError} when no schema is registered for the event
   *   type, or the registry is unreachable.
   */
  private async getValidator(eventType: string): Promise<ValidateFunction> {
    const cached = this.validatorCache.get(eventType);
    if (cached !== undefined) {
      return cached;
    }
    const subject = subjectFor(eventType);
    let response: Response;
    try {
      response = await fetch(
        `${this.baseUrl}/subjects/${encodeURIComponent(subject)}/versions/latest`,
      );
    } catch (cause) {
      throw new SchemaRegistryError(
        `failed to reach the schema registry for event_type '${eventType}' ` +
          `(subject '${subject}'): ${describeCause(cause)}`,
      );
    }
    if (!response.ok) {
      throw new SchemaRegistryError(
        `no schema registered for event_type '${eventType}' ` +
          `(subject '${subject}'): registry responded ` +
          `${String(response.status)} ${await safeBody(response)}`,
      );
    }
    const parsed = (await response.json()) as LatestVersionResponse;
    let schemaDoc: JsonObject;
    try {
      schemaDoc = JSON.parse(parsed.schema) as JsonObject;
    } catch (cause) {
      throw new SchemaRegistryError(
        `registry returned a malformed schema body for event_type ` +
          `'${eventType}': ${describeCause(cause)}`,
      );
    }
    let validator: ValidateFunction;
    try {
      validator = this.ajv.compile(schemaDoc);
    } catch (cause) {
      throw new SchemaRegistryError(
        `registered schema for event_type '${eventType}' did not compile: ` +
          `${describeCause(cause)}`,
      );
    }
    this.validatorCache.set(eventType, validator);
    return validator;
  }
}

function describeCause(cause: unknown): string {
  if (cause instanceof Error) {
    return cause.message;
  }
  return String(cause);
}

async function safeBody(response: Response): Promise<string> {
  try {
    return (await response.text()).slice(0, 500);
  } catch {
    return '<unreadable response body>';
  }
}
