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
 * Cross-language parity vectors — reference values produced by the **Python**
 * event-bus SDK (`trustlist_event_bus`).
 *
 * Each vector's `pythonKey` / `canonical` was computed by running the Python
 * SDK's `derive_idempotency_key` (and its `_canonical_json`) over the listed
 * input. The TypeScript SDK's unit tests assert it reproduces these values
 * byte-for-byte — proving the two SDKs derive idempotency keys identically, so
 * the same logical event deduplicates across a Python producer and a
 * TypeScript consumer (and vice versa). This is the §7b cross-language
 * deduplication contract.
 *
 * To regenerate after a deliberate change to the derivation, run the Python
 * SDK's `derive_idempotency_key` / `_canonical_json` over the same inputs —
 * the snippet is recorded in `event-bus-sdk/typescript/README.md`.
 */

import type { JsonObject, JsonValue } from '../../src/idempotency';

/** One idempotency-key parity vector. */
export interface IdempotencyVector {
  /** A human label for the vector. */
  readonly name: string;
  /** The event type passed to the derivation. */
  readonly eventType: string;
  /** The event payload. */
  readonly payload: JsonObject;
  /** The payload fields selected as the key fields. */
  readonly keyFields: readonly string[];
  /** The 64-char hex key the **Python SDK** derives for this input. */
  readonly pythonKey: string;
}

/** One canonical-JSON parity vector. */
export interface CanonicalJsonVector {
  /** The value to canonicalise. */
  readonly value: JsonValue;
  /** The exact string the **Python SDK**'s `_canonical_json` produces. */
  readonly canonical: string;
}

/**
 * Idempotency-key vectors. Keys verified against the Python SDK on 2026-05-20
 * (Python 3.12, `trustlist_event_bus` 0.1.0).
 */
export const PYTHON_IDEMPOTENCY_VECTORS: readonly IdempotencyVector[] = [
  {
    name: 'tier-one example-collector signal',
    eventType: 'signal.tier-one.example-collector',
    payload: {
      domain_id: '3f8b9c2a-1d4e-4f6a-9b8c-2d1e3f4a5b6c',
      signal_class: 'dns',
      source_url: '',
      observed_at: '2026-05-20T12:00:00+00:00',
      observed_value: { resolves: true },
    },
    keyFields: ['domain_id', 'signal_class', 'observed_at'],
    pythonKey:
      '94db82fcfb28155e4e3e546c6ff0742388377bb811307e447b3d8cf0e01e257f',
  },
  {
    name: 'score.update with an integer keyed field',
    eventType: 'score.update',
    payload: { domain_id: 'd-001', composite_score: 72, verdict: 'Green' },
    keyFields: ['domain_id', 'composite_score'],
    pythonKey:
      '0a17fab5d5659e3a8b6f84b8166b5cfcd0bde07af6a46f9518c775ae4d3a9760',
  },
  {
    name: 'nested object, array, integer, boolean and null key fields',
    eventType: 't',
    payload: {
      a: { z: 1, a: 2 },
      b: [3, 1, 2],
      c: true,
      d: null,
      e: 42,
      f: 'hello',
    },
    keyFields: ['a', 'b', 'c', 'd', 'e', 'f'],
    pythonKey:
      '1b94f426332e54fbf4563c36e99cafb4d363a46b23b5adc30853a17574315068',
  },
  {
    name: 'non-ASCII (unicode) field values — ensure_ascii escaping',
    eventType: 'signal.tier-two.example',
    payload: { domain_id: 'd-éçñ', note: 'café' },
    keyFields: ['domain_id', 'note'],
    pythonKey:
      'f15fbc3679f2287667fecddef329d2909c8406c1f4b537c97d20c2277dc85fbf',
  },
];

/**
 * Canonical-JSON vectors. Strings verified against the Python SDK's
 * `_canonical_json` on 2026-05-20.
 */
export const PYTHON_CANONICAL_JSON_VECTORS: readonly CanonicalJsonVector[] = [
  {
    value: { b: 1, a: 2 },
    canonical: '{"a":2,"b":1}',
  },
  {
    value: {
      nested: { y: 1, x: 2 },
      arr: [5, 4, 3],
      flag: false,
      none: null,
      n: 7,
    },
    canonical:
      '{"arr":[5,4,3],"flag":false,"n":7,"nested":{"x":2,"y":1},"none":null}',
  },
  {
    value: { uni: 'café', emoji: '✨' },
    canonical: '{"emoji":"\\u2728","uni":"caf\\u00e9"}',
  },
  {
    value: [{ k: 1 }, { k: 0 }],
    canonical: '[{"k":1},{"k":0}]',
  },
];
