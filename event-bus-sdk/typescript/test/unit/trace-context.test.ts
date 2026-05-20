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
 * Unit tests for W3C trace-context propagation (PRD §7b, ADR-0012).
 *
 * The SDK reads the active trace context through a pluggable provider; these
 * tests exercise the default empty provider, a custom provider, and the
 * `traceparent` well-formedness check a consumer uses to decide whether to
 * continue a trace or start a fresh one.
 */

import {
  emptyTraceContextProvider,
  hasValidTraceParent,
  injectTraceContext,
  setTraceContextProvider,
} from '../../src/trace-context';

describe('trace-context provider', () => {
  afterEach(() => {
    setTraceContextProvider(null);
  });

  it('yields an empty carrier with no provider registered', () => {
    expect(injectTraceContext()).toEqual({});
  });

  it('captures a carrier from a registered provider', () => {
    const traceparent = `00-${'a'.repeat(32)}-${'b'.repeat(16)}-01`;
    setTraceContextProvider({
      inject: () => ({ traceparent }),
    });
    expect(injectTraceContext()).toEqual({ traceparent });
  });

  it('returns a fresh carrier copy, not the provider internal object', () => {
    const internal = { traceparent: `00-${'c'.repeat(32)}-${'d'.repeat(16)}-01` };
    setTraceContextProvider({ inject: () => internal });
    const captured = injectTraceContext();
    expect(captured).toEqual(internal);
    expect(captured).not.toBe(internal);
  });

  it('resets to the empty provider when given null', () => {
    setTraceContextProvider({ inject: () => ({ traceparent: 'x' }) });
    setTraceContextProvider(null);
    expect(injectTraceContext()).toEqual(emptyTraceContextProvider.inject());
  });
});

describe('hasValidTraceParent', () => {
  it('accepts a well-formed W3C traceparent', () => {
    const traceparent = `00-${'a'.repeat(32)}-${'b'.repeat(16)}-01`;
    expect(hasValidTraceParent({ traceparent })).toBe(true);
  });

  it('rejects an empty carrier', () => {
    expect(hasValidTraceParent({})).toBe(false);
  });

  it('rejects a structurally malformed traceparent', () => {
    expect(hasValidTraceParent({ traceparent: 'not-a-traceparent' })).toBe(
      false,
    );
  });

  it('rejects the W3C all-zero (invalid) trace id', () => {
    const traceparent = `00-${'0'.repeat(32)}-${'b'.repeat(16)}-01`;
    expect(hasValidTraceParent({ traceparent })).toBe(false);
  });

  it('rejects the W3C all-zero (invalid) parent id', () => {
    const traceparent = `00-${'a'.repeat(32)}-${'0'.repeat(16)}-01`;
    expect(hasValidTraceParent({ traceparent })).toBe(false);
  });
});
