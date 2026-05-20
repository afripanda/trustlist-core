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
 * Unit tests for idempotency-key derivation (PRD §7b).
 *
 * The behavioural tests mirror the Python SDK's `test_idempotency.py`. The
 * cross-language parity test asserts the derived key equals a value the Python
 * SDK produces for the same input — proving the two SDKs deduplicate
 * identically. The Python reference values were computed by running the Python
 * SDK's `derive_idempotency_key` (equivalently, its
 * `hashlib.sha256(json.dumps(..., sort_keys=True, separators=(",", ":")))`)
 * over the same inputs; see test/unit/cross-language-parity.json.
 */

import { canonicalJson, deriveIdempotencyKey } from '../../src/idempotency';
import {
  PYTHON_IDEMPOTENCY_VECTORS,
  PYTHON_CANONICAL_JSON_VECTORS,
} from './cross-language-parity';

describe('deriveIdempotencyKey', () => {
  it('derives the same key for two emissions of the same logical event', () => {
    const payload = { domain_id: 'd1', signal_class: 'dns', observed_at: 't1' };
    const keyA = deriveIdempotencyKey({
      eventType: 'signal.tier-one.example-collector',
      payload,
      keyFields: ['domain_id', 'signal_class', 'observed_at'],
    });
    const keyB = deriveIdempotencyKey({
      eventType: 'signal.tier-one.example-collector',
      payload: { ...payload },
      keyFields: ['domain_id', 'signal_class', 'observed_at'],
    });
    expect(keyA).toBe(keyB);
  });

  it('derives a key independent of key-field order', () => {
    const payload = { domain_id: 'd1', signal_class: 'dns' };
    const keyA = deriveIdempotencyKey({
      eventType: 't',
      payload,
      keyFields: ['domain_id', 'signal_class'],
    });
    const keyB = deriveIdempotencyKey({
      eventType: 't',
      payload,
      keyFields: ['signal_class', 'domain_id'],
    });
    expect(keyA).toBe(keyB);
  });

  it('derives a key independent of unselected payload fields', () => {
    const base = { domain_id: 'd1', signal_class: 'dns' };
    const withExtra = { ...base, observed_value: { resolves: true } };
    const keyBase = deriveIdempotencyKey({
      eventType: 't',
      payload: base,
      keyFields: ['domain_id', 'signal_class'],
    });
    const keyExtra = deriveIdempotencyKey({
      eventType: 't',
      payload: withExtra,
      keyFields: ['domain_id', 'signal_class'],
    });
    expect(keyBase).toBe(keyExtra);
  });

  it('derives different keys when a keyed field changes', () => {
    const keyOne = deriveIdempotencyKey({
      eventType: 't',
      payload: { domain_id: 'd1' },
      keyFields: ['domain_id'],
    });
    const keyTwo = deriveIdempotencyKey({
      eventType: 't',
      payload: { domain_id: 'd2' },
      keyFields: ['domain_id'],
    });
    expect(keyOne).not.toBe(keyTwo);
  });

  it('namespaces the key by event type', () => {
    const payload = { domain_id: 'd1' };
    const keyA = deriveIdempotencyKey({
      eventType: 'signal.tier-one.example-collector',
      payload,
      keyFields: ['domain_id'],
    });
    const keyB = deriveIdempotencyKey({
      eventType: 'score.update',
      payload,
      keyFields: ['domain_id'],
    });
    expect(keyA).not.toBe(keyB);
  });

  it('produces a 64-character lowercase hex SHA-256 digest', () => {
    const key = deriveIdempotencyKey({
      eventType: 't',
      payload: { domain_id: 'd1' },
      keyFields: ['domain_id'],
    });
    expect(key).toMatch(/^[0-9a-f]{64}$/);
  });

  it('rejects an empty keyFields list', () => {
    expect(() =>
      deriveIdempotencyKey({
        eventType: 't',
        payload: { domain_id: 'd1' },
        keyFields: [],
      }),
    ).toThrow(/at least one payload field/);
  });

  it('rejects a key field absent from the payload', () => {
    expect(() =>
      deriveIdempotencyKey({
        eventType: 't',
        payload: { domain_id: 'd1' },
        keyFields: ['domain_id', 'signal_class'],
      }),
    ).toThrow(/not present in the payload/);
  });

  it('rejects a non-integer number in a keyed field — float formatting can ' +
    'diverge across Python and JavaScript', () => {
    expect(() =>
      deriveIdempotencyKey({
        eventType: 't',
        payload: { score: 0.5 },
        keyFields: ['score'],
      }),
    ).toThrow(/non-integer number/);
  });
});

describe('canonicalJson — Python-byte-identical canonicalisation', () => {
  it('sorts object keys recursively', () => {
    expect(canonicalJson({ b: 1, a: { z: 1, a: 2 } })).toBe(
      '{"a":{"a":2,"z":1},"b":1}',
    );
  });

  it('emits minimal separators and renders primitives like Python json', () => {
    expect(canonicalJson({ c: true, d: null, e: 1, f: [3, 1, 2] })).toBe(
      '{"c":true,"d":null,"e":1,"f":[3,1,2]}',
    );
  });

  it('matches the Python json.dumps reference vectors byte-for-byte', () => {
    for (const vector of PYTHON_CANONICAL_JSON_VECTORS) {
      expect(canonicalJson(vector.value)).toBe(vector.canonical);
    }
  });
});

describe('cross-language idempotency-key parity with the Python SDK', () => {
  it('derives keys byte-identical to the Python SDK for shared vectors', () => {
    for (const vector of PYTHON_IDEMPOTENCY_VECTORS) {
      const key = deriveIdempotencyKey({
        eventType: vector.eventType,
        payload: vector.payload,
        keyFields: vector.keyFields,
      });
      expect(key).toBe(vector.pythonKey);
    }
  });
});
