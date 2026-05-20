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
 * Unit tests for the typed event envelope (PRD §7b).
 *
 * These tests need no broker and no registry — the envelope is deliberately
 * dependency-free. They mirror the Python SDK's `test_envelope.py`, and add a
 * wire-parity check: an envelope wire object decoded from JSON that a Python
 * producer could have written round-trips through the TypeScript envelope
 * unchanged.
 */

import {
  ENVELOPE_VERSION,
  EventEnvelope,
  type EventEnvelopeWire,
  newEnvelope,
  provenance,
  provenanceFromWire,
  provenanceToWire,
} from '../../src/envelope';

// The nine §7b envelope fields. The envelope must carry exactly these.
const SECTION_7B_FIELDS = [
  'event_id',
  'event_type',
  'event_version',
  'produced_at',
  'producer_id',
  'trace_context',
  'idempotency_key',
  'provenance',
  'payload',
].sort();

describe('newEnvelope', () => {
  it('carries exactly the nine §7b fields when serialised to the wire', () => {
    const envelope = newEnvelope({
      eventType: 'signal.tier-one.example-collector',
      payload: { domain_id: 'd1' },
      producerId: 'collector-1',
      idempotencyKey: 'abc123',
      provenance: provenance('system', 'dns-lookup'),
    });
    expect(Object.keys(envelope.toWire()).sort()).toEqual(SECTION_7B_FIELDS);
  });

  it('generates an event id and a timestamp when not supplied', () => {
    const before = Date.now();
    const envelope = newEnvelope({
      eventType: 'signal.tier-one.example-collector',
      payload: {},
      producerId: 'collector-1',
      idempotencyKey: 'k',
      provenance: provenance('system', 'probe'),
    });
    const after = Date.now();
    expect(envelope.eventId).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/,
    );
    const producedMs = Date.parse(envelope.producedAt);
    expect(producedMs).toBeGreaterThanOrEqual(before);
    expect(producedMs).toBeLessThanOrEqual(after);
  });

  it('honours an explicit event id and timestamp — makes a produce reproducible', () => {
    const fixedId = '00000000-0000-0000-0000-000000000001';
    const fixedTime = '2026-05-20T12:00:00.000Z';
    const envelope = newEnvelope({
      eventType: 'signal.tier-one.example-collector',
      payload: {},
      producerId: 'collector-1',
      idempotencyKey: 'k',
      provenance: provenance('system', 'probe'),
      eventId: fixedId,
      producedAt: fixedTime,
    });
    expect(envelope.eventId).toBe(fixedId);
    expect(envelope.producedAt).toBe(fixedTime);
  });

  it('defaults event_version to the envelope version', () => {
    const envelope = newEnvelope({
      eventType: 'signal.tier-one.example-collector',
      payload: {},
      producerId: 'collector-1',
      idempotencyKey: 'k',
      provenance: provenance('system', 'probe'),
    });
    expect(envelope.eventVersion).toBe(ENVELOPE_VERSION);
  });
});

describe('EventEnvelope serialisation', () => {
  it('round-trips through bytes unchanged', () => {
    const original = newEnvelope({
      eventType: 'signal.tier-one.example-collector',
      payload: { domain_id: 'd1', value: 7 },
      producerId: 'collector-1',
      idempotencyKey: 'abc123',
      provenance: provenance('contributor', 'manual', 'analyst@example.org'),
      traceContext: { traceparent: `00-${'0'.repeat(32)}-${'0'.repeat(16)}-01` },
    });
    const restored = EventEnvelope.fromBytes(original.toBytes());
    expect(restored.eventId).toBe(original.eventId);
    expect(restored.eventType).toBe(original.eventType);
    expect(restored.eventVersion).toBe(original.eventVersion);
    expect(restored.producedAt).toBe(original.producedAt);
    expect(restored.producerId).toBe(original.producerId);
    expect(restored.traceContext).toEqual(original.traceContext);
    expect(restored.idempotencyKey).toBe(original.idempotencyKey);
    expect(restored.provenance).toEqual(original.provenance);
    expect(restored.payload).toEqual(original.payload);
  });

  it('serialises with no incidental whitespace (Python separators parity)', () => {
    const envelope = newEnvelope({
      eventType: 't',
      payload: { a: 1 },
      producerId: 'p',
      idempotencyKey: 'k',
      provenance: provenance('system', 'probe'),
      eventId: '00000000-0000-0000-0000-000000000002',
      producedAt: '2026-05-20T00:00:00.000Z',
    });
    expect(envelope.toBytes().toString('utf8')).not.toMatch(/: |, /);
  });

  it('is immutable — fields cannot be reassigned after construction', () => {
    const envelope = newEnvelope({
      eventType: 't',
      payload: {},
      producerId: 'p',
      idempotencyKey: 'k',
      provenance: provenance('system', 'probe'),
    });
    expect(() => {
      // @ts-expect-error — the envelope is frozen; this assignment must fail.
      envelope.producerId = 'tampered';
    }).toThrow();
  });

  it('rejects a wire object missing a required §7b field', () => {
    const good = newEnvelope({
      eventType: 't',
      payload: {},
      producerId: 'p',
      idempotencyKey: 'k',
      provenance: provenance('system', 'probe'),
    }).toWire();
    const withoutKey: Record<string, unknown> = { ...good };
    delete withoutKey.idempotency_key;
    expect(() =>
      EventEnvelope.fromWire(withoutKey as Partial<EventEnvelopeWire>),
    ).toThrow(/missing the required §7b field 'idempotency_key'/);
  });

  it('decodes a Python-shaped wire envelope — cross-language wire parity', () => {
    // The exact JSON a Python producer puts on the wire: snake_case fields,
    // ISO-8601 produced_at with a +00:00 offset, contributor_identity null.
    const pythonWire =
      '{"event_id":"3f8b9c2a-1d4e-4f6a-9b8c-2d1e3f4a5b6c",' +
      '"event_type":"signal.tier-one.example-collector",' +
      '"event_version":"1.0.0",' +
      '"produced_at":"2026-05-20T12:00:00+00:00",' +
      '"producer_id":"py-collector",' +
      '"trace_context":{"traceparent":"00-' +
      'abcdef0123456789abcdef0123456789-0123456789abcdef-01"},' +
      '"idempotency_key":"cf88ba0e",' +
      '"provenance":{"source":"system","method":"dns-probe",' +
      '"contributor_identity":null},' +
      '"payload":{"domain_id":"d1","signal_class":"dns"}}';
    const envelope = EventEnvelope.fromBytes(Buffer.from(pythonWire, 'utf8'));
    expect(envelope.eventId).toBe('3f8b9c2a-1d4e-4f6a-9b8c-2d1e3f4a5b6c');
    expect(envelope.producedAt).toBe('2026-05-20T12:00:00+00:00');
    expect(envelope.provenance).toEqual(provenance('system', 'dns-probe'));
    expect(envelope.payload).toEqual({ domain_id: 'd1', signal_class: 'dns' });
    // ...and it survives a re-serialisation byte-identically.
    expect(envelope.toBytes().toString('utf8')).toBe(pythonWire);
  });
});

describe('provenance', () => {
  it('round-trips through the wire form with and without a contributor', () => {
    const system = provenance('system', 'probe');
    expect(provenanceFromWire(provenanceToWire(system))).toEqual(system);

    const human = provenance('human', 'review', 'rev@example.org');
    expect(provenanceFromWire(provenanceToWire(human))).toEqual(human);
  });

  it('defaults contributorIdentity to null', () => {
    expect(provenance('system', 'probe').contributorIdentity).toBeNull();
  });
});
