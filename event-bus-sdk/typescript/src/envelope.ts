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
 * The typed event envelope (Stage 0 PRD §7b).
 *
 * Every event on the TrustList bus, regardless of payload, carries the same
 * envelope. This module defines that envelope and the JSON (de)serialisation
 * that ferries it through Kafka. It mirrors the Python SDK's `envelope.py`
 * exactly: the on-the-wire JSON is identical, so a Python-produced event is
 * consumable by this TypeScript SDK and vice versa.
 *
 * The envelope fields, exactly per §7b:
 *
 * - `event_id` — UUID, for idempotency and deduplication.
 * - `event_type` — text, the topic plus a payload-type qualifier.
 * - `event_version` — semver, for schema evolution.
 * - `produced_at` — an ISO-8601 timestamp string (a `timestamptz` on the wire).
 * - `producer_id` — text, identifies the emitting component instance.
 * - `trace_context` — the W3C `traceparent` / `tracestate` carrier.
 * - `idempotency_key` — text, derived from payload-specific fields.
 * - `provenance` — the source / method / contributor-identity object of §7a.
 * - `payload` — the event-type-specific body, schema-validated separately.
 *
 * Wire form. `EventEnvelope.toBytes()` serialises with no incidental whitespace
 * (`JSON.stringify` with no spacing), matching Python's
 * `json.dumps(..., separators=(",", ":"))`. `produced_at` is carried as the
 * ISO-8601 string the producer (Python or TypeScript) wrote; the envelope does
 * not reparse it into a `Date`, so a Python timestamp survives a TypeScript
 * round-trip byte-identically.
 */

import { randomUUID } from 'node:crypto';

import type { JsonObject } from './idempotency';

/**
 * The envelope schema version. Bumped only when the *envelope* shape changes,
 * independently of any payload's `event_version`. Matches the Python SDK's
 * `ENVELOPE_VERSION`.
 */
export const ENVELOPE_VERSION = '1.0.0';

/**
 * The origin of an observation (Stage 0 PRD §7a `provenance`).
 */
export interface Provenance {
  /**
   * The origin class — `system`, `human`, `contributor` or `cti_partner` per
   * the §7a `evidence.source` enum.
   */
  readonly source: string;
  /**
   * How the observation was made — a free-text method label such as
   * `dns-lookup` or `http-probe`.
   */
  readonly method: string;
  /**
   * The contributor's identity, when the source is a human or a partner;
   * `null` for fully automated system signals.
   */
  readonly contributorIdentity: string | null;
}

/** The JSON (wire) form of a {@link Provenance}, matching Python `to_dict()`. */
export interface ProvenanceWire {
  readonly source: string;
  readonly method: string;
  readonly contributor_identity: string | null;
}

/**
 * Build a {@link Provenance}, defaulting `contributorIdentity` to `null`.
 *
 * @param source the origin class.
 * @param method the observation method label.
 * @param contributorIdentity the contributor identity, omitted for system
 *   signals.
 */
export function provenance(
  source: string,
  method: string,
  contributorIdentity: string | null = null,
): Provenance {
  return { source, method, contributorIdentity };
}

/** Render a {@link Provenance} as its JSON-serialisable wire object. */
export function provenanceToWire(value: Provenance): ProvenanceWire {
  return {
    source: value.source,
    method: value.method,
    contributor_identity: value.contributorIdentity,
  };
}

/** Rebuild a {@link Provenance} from its wire object. */
export function provenanceFromWire(data: ProvenanceWire): Provenance {
  return {
    source: data.source,
    method: data.method,
    contributorIdentity: data.contributor_identity ?? null,
  };
}

/** The W3C trace-context carrier — `traceparent` and optionally `tracestate`. */
export type TraceContext = Record<string, string>;

/** The JSON (wire) form of an {@link EventEnvelope}; the nine §7b fields. */
export interface EventEnvelopeWire {
  readonly event_id: string;
  readonly event_type: string;
  readonly event_version: string;
  readonly produced_at: string;
  readonly producer_id: string;
  readonly trace_context: TraceContext;
  readonly idempotency_key: string;
  readonly provenance: ProvenanceWire;
  readonly payload: JsonObject;
}

/**
 * A fully-constructed event envelope ready to publish, or one just consumed.
 *
 * Instances are immutable (every field is `readonly`). Producers build one via
 * {@link newEnvelope}, which fills the generated fields (`eventId`,
 * `producedAt`); a consumer rebuilds one from the wire via
 * {@link EventEnvelope.fromBytes}.
 */
export class EventEnvelope {
  public readonly eventType: string;
  public readonly eventVersion: string;
  public readonly producerId: string;
  public readonly idempotencyKey: string;
  public readonly provenance: Provenance;
  public readonly payload: JsonObject;
  public readonly traceContext: TraceContext;
  public readonly eventId: string;
  /**
   * The production timestamp, carried as the ISO-8601 string put on the wire.
   * Kept as a string — not a `Date` — so a Python-produced timestamp survives
   * a TypeScript round-trip byte-identically.
   */
  public readonly producedAt: string;

  public constructor(fields: {
    eventType: string;
    eventVersion: string;
    producerId: string;
    idempotencyKey: string;
    provenance: Provenance;
    payload: JsonObject;
    traceContext: TraceContext;
    eventId: string;
    producedAt: string;
  }) {
    this.eventType = fields.eventType;
    this.eventVersion = fields.eventVersion;
    this.producerId = fields.producerId;
    this.idempotencyKey = fields.idempotencyKey;
    this.provenance = fields.provenance;
    this.payload = fields.payload;
    this.traceContext = fields.traceContext;
    this.eventId = fields.eventId;
    this.producedAt = fields.producedAt;
    Object.freeze(this);
  }

  /**
   * Render the envelope as its JSON-serialisable wire object — exactly the
   * nine §7b fields, in the same field names the Python SDK emits.
   */
  public toWire(): EventEnvelopeWire {
    return {
      event_id: this.eventId,
      event_type: this.eventType,
      event_version: this.eventVersion,
      produced_at: this.producedAt,
      producer_id: this.producerId,
      trace_context: { ...this.traceContext },
      idempotency_key: this.idempotencyKey,
      provenance: provenanceToWire(this.provenance),
      payload: this.payload,
    };
  }

  /**
   * Serialise the envelope to the UTF-8 JSON bytes put on the wire.
   *
   * Uses no incidental whitespace, matching Python's
   * `json.dumps(..., separators=(",", ":"))`.
   */
  public toBytes(): Buffer {
    return Buffer.from(JSON.stringify(this.toWire()), 'utf8');
  }

  /**
   * Rebuild an {@link EventEnvelope} from its wire object.
   *
   * @throws {Error} when a required envelope field is absent — a malformed
   *   envelope is a hard error, not a silently-defaulted one.
   */
  public static fromWire(data: Partial<EventEnvelopeWire>): EventEnvelope {
    const required: (keyof EventEnvelopeWire)[] = [
      'event_id',
      'event_type',
      'event_version',
      'produced_at',
      'producer_id',
      'idempotency_key',
      'provenance',
      'payload',
    ];
    for (const field of required) {
      if (data[field] === undefined || data[field] === null) {
        throw new Error(
          `event envelope is missing the required §7b field '${field}'.`,
        );
      }
    }
    return new EventEnvelope({
      eventId: data.event_id as string,
      eventType: data.event_type as string,
      eventVersion: data.event_version as string,
      producedAt: data.produced_at as string,
      producerId: data.producer_id as string,
      traceContext: { ...(data.trace_context ?? {}) },
      idempotencyKey: data.idempotency_key as string,
      provenance: provenanceFromWire(data.provenance as ProvenanceWire),
      payload: data.payload as JsonObject,
    });
  }

  /** Rebuild an {@link EventEnvelope} from on-the-wire JSON bytes. */
  public static fromBytes(raw: Buffer | Uint8Array): EventEnvelope {
    const text = Buffer.from(raw).toString('utf8');
    const parsed = JSON.parse(text) as Partial<EventEnvelopeWire>;
    return EventEnvelope.fromWire(parsed);
  }
}

/** Options for {@link newEnvelope}. */
export interface NewEnvelopeOptions {
  /** The topic plus payload-type qualifier. */
  readonly eventType: string;
  /** The event-type-specific body. Validated by the producer, not here. */
  readonly payload: JsonObject;
  /** Identifies the emitting component instance. */
  readonly producerId: string;
  /** The deduplication key; derive it with `deriveIdempotencyKey`. */
  readonly idempotencyKey: string;
  /** The observation's origin (§7a). */
  readonly provenance: Provenance;
  /**
   * A W3C `traceparent` / `tracestate` carrier; when omitted an empty carrier
   * is used.
   */
  readonly traceContext?: TraceContext;
  /** The payload schema version; defaults to the envelope version. */
  readonly eventVersion?: string;
  /** An explicit event id; a v4 UUID is generated when omitted. */
  readonly eventId?: string;
  /**
   * An explicit production timestamp, as an ISO-8601 string; the current UTC
   * instant is used when omitted. Supplying it is what makes a produce call
   * reproducible in a test fixture.
   */
  readonly producedAt?: string;
}

/**
 * Construct an {@link EventEnvelope}, filling the generated fields.
 *
 * This is the single typed constructor producers use. `eventId` and
 * `producedAt` are generated when not supplied; an explicit value for either
 * is honoured, which is what makes a produce call reproducible in a test
 * fixture. Mirrors the Python SDK's `new_envelope`.
 *
 * @param options the envelope fields.
 * @returns the constructed envelope.
 */
export function newEnvelope(options: NewEnvelopeOptions): EventEnvelope {
  return new EventEnvelope({
    eventType: options.eventType,
    eventVersion: options.eventVersion ?? ENVELOPE_VERSION,
    producerId: options.producerId,
    idempotencyKey: options.idempotencyKey,
    provenance: options.provenance,
    payload: options.payload,
    traceContext: { ...(options.traceContext ?? {}) },
    eventId: options.eventId ?? randomUUID(),
    producedAt: options.producedAt ?? new Date().toISOString(),
  });
}
