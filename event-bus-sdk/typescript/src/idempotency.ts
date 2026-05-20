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
 * Idempotency-key derivation (Stage 0 PRD §7b).
 *
 * The event bus is at-least-once: a producer may emit the same logical event
 * more than once, and the broker may redeliver. PRD §7b requires the
 * `idempotency_key` to be *derived from payload-specific fields* so that two
 * emissions of the same logical observation collide on the same key, and a
 * consumer can deduplicate on it.
 *
 * Cross-language parity. This implementation is **byte-identical** to the
 * Python SDK's `idempotency.py` so the same logical event yields the same key
 * in either language — critical for cross-language deduplication. The Python
 * SDK derives the key as:
 *
 *     sha256(json.dumps(document, sort_keys=True, separators=(",", ":"),
 *                       default=str))
 *
 * over the document `{"event_type": ..., "key_fields": {...selected fields}}`.
 *
 * {@link canonicalJson} reproduces that exact serialisation:
 *
 * - object keys sorted recursively by Unicode code point (Python `sort_keys`
 *   sorts string keys with the same ordering JavaScript's `Array.prototype.sort`
 *   uses for strings — lexicographic by UTF-16 code unit, which agrees with
 *   code-point order for the basic-multilingual-plane characters event-type
 *   and field names use);
 * - the minimal `(",", ":")` separators — no incidental whitespace;
 * - `null` for JSON null, `true` / `false` for booleans;
 * - integers emitted with no decimal point, exactly as Python's `json` and
 *   JavaScript's `JSON.stringify` both do;
 * - **strings escaped `ensure_ascii`-style** — Python's `json.dumps` defaults
 *   to `ensure_ascii=True`, escaping every non-ASCII character as a `\uXXXX`
 *   sequence (a surrogate pair for characters beyond the BMP). JavaScript's
 *   native `JSON.stringify` emits non-ASCII characters literally, so
 *   {@link encodeString} re-escapes them to match Python byte-for-byte;
 * - any value that is not a plain JSON value (a JavaScript `Date`, say) is
 *   coerced with `String(value)`, matching Python's `default=str` hook.
 *
 * The one cross-language hazard is floating-point formatting: Python's `repr`
 * and JavaScript's `Number.prototype.toString` can differ for some non-integer
 * doubles. The §7b idempotency fields in practice are strings and integers
 * (`domain_id`, `signal_class`, `observed_at`); a non-integer `number` in a
 * `key_fields` value is therefore rejected outright rather than risk a silent
 * cross-language key divergence — see {@link assertCanonicalisable}.
 */

import { createHash } from 'node:crypto';

/**
 * A value that may appear inside an event payload — the JSON value space.
 * `bigint` is admitted because integer ids beyond `Number.MAX_SAFE_INTEGER`
 * are canonicalised losslessly through it.
 */
export type JsonValue =
  | string
  | number
  | bigint
  | boolean
  | null
  | undefined
  | JsonValue[]
  | { [key: string]: JsonValue };

/** A JSON object payload, the shape every event body takes. */
export type JsonObject = { [key: string]: JsonValue };

/**
 * Render `value` as canonical JSON — recursively sorted keys, minimal
 * separators — byte-identical to the Python SDK's `_canonical_json`.
 *
 * @param value the value to canonicalise.
 * @returns the canonical JSON string.
 * @throws {Error} when `value` holds a number that cannot be canonicalised
 *   identically across Python and JavaScript (a non-finite or non-integer
 *   `number`); see the module docstring.
 */
export function canonicalJson(value: JsonValue): string {
  return encode(value);
}

function encode(value: JsonValue): string {
  if (value === null || value === undefined) {
    // Python's json renders `None` as `null`; `default=str` is never reached
    // for `None`. `undefined` has no Python analogue and is treated as null.
    return 'null';
  }
  if (typeof value === 'string') {
    return encodeString(value);
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false';
  }
  if (typeof value === 'bigint') {
    return value.toString();
  }
  if (typeof value === 'number') {
    return encodeNumber(value);
  }
  if (Array.isArray(value)) {
    return `[${value.map((item) => encode(item)).join(',')}]`;
  }
  if (typeof value === 'object') {
    const obj = value;
    const keys = Object.keys(obj).sort();
    const members = keys.map(
      (key) => `${encodeString(key)}:${encode(obj[key])}`,
    );
    return `{${members.join(',')}}`;
  }
  // A non-JSON value (Date, function, symbol). Python's `default=str` coerces
  // it with `str()`; mirror that with `String(value)`.
  return encodeString(String(value));
}

/**
 * Escape a string exactly as Python's `json.dumps` does with its default
 * `ensure_ascii=True`: the standard JSON control-character escapes, plus a
 * `\uXXXX` escape for every non-ASCII code unit (a character beyond the BMP
 * becomes its two surrogate-pair `\uXXXX` escapes — which is precisely what
 * iterating over a JavaScript string's UTF-16 code units yields).
 */
function encodeString(value: string): string {
  let out = '"';
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    const char = value[i] as string;
    switch (char) {
      case '"':
        out += '\\"';
        break;
      case '\\':
        out += '\\\\';
        break;
      case '\b':
        out += '\\b';
        break;
      case '\f':
        out += '\\f';
        break;
      case '\n':
        out += '\\n';
        break;
      case '\r':
        out += '\\r';
        break;
      case '\t':
        out += '\\t';
        break;
      default:
        if (code < 0x20 || code > 0x7e) {
          // Control characters and every non-ASCII code unit: Python's
          // ensure_ascii=True emits a lowercase-hex \uXXXX escape.
          out += `\\u${code.toString(16).padStart(4, '0')}`;
        } else {
          out += char;
        }
        break;
    }
  }
  return `${out}"`;
}

function encodeNumber(value: number): string {
  if (!Number.isFinite(value)) {
    throw new Error(
      'cannot derive an idempotency key over a non-finite number; ' +
        'Python json.dumps rejects NaN/Infinity by default and a key built ' +
        'over one would not be cross-language stable.',
    );
  }
  if (!Number.isInteger(value)) {
    throw new Error(
      'cannot derive an idempotency key over a non-integer number ' +
        `(${String(value)}); float formatting can differ between Python and ` +
        'JavaScript, so a non-integer key value risks a silent cross-language ' +
        'key divergence. Pass the value as a string instead.',
    );
  }
  // An integer-valued double. Python's json and JS both render it with no
  // decimal point; `-0` is normalised to `0` to match Python.
  return Object.is(value, -0) ? '0' : value.toString();
}

/**
 * Assert that `value` can be canonicalised identically across both SDKs.
 *
 * Throws the same error {@link canonicalJson} would throw; useful to fail fast
 * at a payload-construction site before a produce call.
 *
 * @param value the value to check.
 */
export function assertCanonicalisable(value: JsonValue): void {
  canonicalJson(value);
}

/** Options for {@link deriveIdempotencyKey}. */
export interface DeriveIdempotencyKeyOptions {
  /** The event type; namespaces the derived key. */
  readonly eventType: string;
  /** The event payload. */
  readonly payload: JsonObject;
  /**
   * The payload keys whose values identify the logical event — for a tier-one
   * signal, typically `["domain_id", "signal_class", "observed_at"]`. Order
   * does not matter; the values are gathered into a sorted-key document.
   */
  readonly keyFields: readonly string[];
}

/**
 * Derive a stable idempotency key from payload-specific fields.
 *
 * The key is the lowercase hex SHA-256 of a canonical JSON document containing
 * the `eventType` and the selected payload fields. Including the event type
 * namespaces the key, so the same field values under two different event types
 * do not collide.
 *
 * The derivation is byte-identical to the Python SDK's `derive_idempotency_key`
 * — the same logical event yields the same 64-character key in either language.
 *
 * @param options the event type, payload and key-field selection.
 * @returns a 64-character lowercase hex SHA-256 digest.
 * @throws {Error} when `keyFields` is empty (an unkeyed event could never be
 *   deduplicated) or names a field absent from `payload` (a silent miss would
 *   produce a key that does not identify the event).
 */
export function deriveIdempotencyKey(
  options: DeriveIdempotencyKeyOptions,
): string {
  const { eventType, payload, keyFields } = options;
  if (keyFields.length === 0) {
    throw new Error(
      'keyFields must name at least one payload field; an event with no ' +
        'idempotency-defining fields cannot be deduplicated (PRD §7b).',
    );
  }
  const missing = keyFields.filter(
    (name) => !Object.prototype.hasOwnProperty.call(payload, name),
  );
  if (missing.length > 0) {
    throw new Error(
      'keyFields names payload field(s) not present in the payload: ' +
        `${[...missing].sort().join(', ')}.`,
    );
  }
  const selected: JsonObject = {};
  for (const name of keyFields) {
    selected[name] = payload[name];
  }
  const document: JsonObject = {
    event_type: eventType,
    key_fields: selected,
  };
  return createHash('sha256').update(canonicalJson(document), 'utf8').digest('hex');
}
