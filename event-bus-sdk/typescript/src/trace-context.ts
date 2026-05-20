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
 * W3C trace-context propagation for the event-bus SDK (PRD §7b, ADR-0012).
 *
 * The §7b envelope carries a `trace_context` field so a signal's flow from
 * collector through the bus to a consumer forms a single distributed trace.
 * The carrier is a plain object of W3C Trace Context headers — a `traceparent`
 * key and, when present, a `tracestate` key — exactly the carrier the Python
 * SDK writes via `observability.inject_trace_context`. Because the carrier is
 * just W3C headers, a trace started in a Python collector is continued by a
 * TypeScript consumer and vice versa.
 *
 * Pluggable provider. The Python `trustlist-core` ships an OpenTelemetry-based
 * `observability` library; the producer there injects the active OTel span.
 * This TypeScript SDK lives in the *applications* layer and must not force an
 * OpenTelemetry dependency on every consumer. It therefore reads the active
 * trace context through a {@link TraceContextProvider} interface: an
 * application already running OpenTelemetry registers a provider that bridges
 * to it (a one-liner over `@opentelemetry/api`'s `propagation.inject`); an
 * application that has not wired tracing simply gets an empty carrier and the
 * consumer starts a fresh root trace — the safe default, matching the Python
 * SDK's "no active span -> empty carrier" behaviour.
 */

import type { TraceContext } from './envelope';

/**
 * A source of the active W3C trace context.
 *
 * Register an implementation with {@link setTraceContextProvider} to have the
 * producer stamp the active trace into every envelope. With no provider
 * registered the SDK uses {@link emptyTraceContextProvider}.
 */
export interface TraceContextProvider {
  /**
   * Return the active trace context as a W3C-headers carrier.
   *
   * @returns a carrier object — `{}` when there is no active trace.
   */
  inject(): TraceContext;
}

/**
 * The default provider: it never has an active trace and always yields an
 * empty carrier. The consumer then starts a fresh root trace.
 */
export const emptyTraceContextProvider: TraceContextProvider = {
  inject(): TraceContext {
    return {};
  },
};

let activeProvider: TraceContextProvider = emptyTraceContextProvider;

/**
 * Register the process-wide trace-context provider.
 *
 * An application that runs OpenTelemetry registers a bridging provider once at
 * start-up; every producer built afterwards stamps the active trace.
 *
 * @param provider the provider to install, or `null` to reset to the empty
 *   default.
 */
export function setTraceContextProvider(
  provider: TraceContextProvider | null,
): void {
  activeProvider = provider ?? emptyTraceContextProvider;
}

/**
 * Capture the active trace context as a W3C-headers carrier.
 *
 * Mirrors the Python SDK's `inject_trace_context`: with an active trace it
 * returns a carrier holding a `traceparent` (and `tracestate` when non-empty);
 * with none it returns `{}`.
 *
 * @returns the trace-context carrier to store in the envelope.
 */
export function injectTraceContext(): TraceContext {
  return { ...activeProvider.inject() };
}

/**
 * Validate that a carrier holds a syntactically well-formed W3C `traceparent`.
 *
 * A `traceparent` is `version "-" trace-id "-" parent-id "-" trace-flags`,
 * three hyphens splitting four hex fields. A consumer that needs to decide
 * whether to continue a trace or start a fresh one can use this; an
 * ill-formed carrier should be treated as "no parent".
 *
 * @param carrier the envelope's `trace_context` field.
 * @returns `true` when the carrier carries a well-formed `traceparent`.
 */
export function hasValidTraceParent(carrier: TraceContext): boolean {
  const traceparent = carrier.traceparent;
  if (typeof traceparent !== 'string') {
    return false;
  }
  // version-traceid-parentid-flags, all lowercase hex; trace-id and parent-id
  // must not be all-zero (the W3C "invalid" sentinel).
  const match =
    /^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$/.exec(
      traceparent,
    );
  if (match === null) {
    return false;
  }
  const traceId = match[2];
  const parentId = match[3];
  return (
    traceId !== '0'.repeat(32) && parentId !== '0'.repeat(16)
  );
}
