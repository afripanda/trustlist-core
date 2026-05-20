# Copyright 2026 The TrustList Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""TrustList observability instrumentation library.

This package is the shared observability foundation every TrustList component
adopts, per Stage 0 PRD §7f. It wraps the OpenTelemetry SDK so that application
code never binds to the SDK directly, and provides:

- :func:`init_tracing` — initialise tracing and configure the OTLP exporter
  (Honeycomb per ADR-0012), falling back to a console exporter when no endpoint
  is configured so that tests and local runs need neither a network nor an API
  key.
- Trace-context propagation helpers (:func:`inject_trace_context`,
  :func:`extract_trace_context`) — standalone W3C ``traceparent`` /
  ``tracestate`` carriers, used by the event-bus SDK to ferry trace context
  through the event envelope's ``trace_context`` field.
- :func:`get_logger` — a structured JSON logger emitting the §7f field schema.
- Decorators for the five instrumentation surfaces named in §7f: incoming HTTP
  requests, DB queries, event-bus produce, event-bus consume and scheduled
  jobs.
"""

from observability.decorators import (
    instrument_consume,
    instrument_db_query,
    instrument_http_request,
    instrument_produce,
    instrument_scheduled_job,
)
from observability.logging import StructuredLogger, get_logger
from observability.propagation import extract_trace_context, inject_trace_context
from observability.tracing import init_tracing, shutdown_tracing

__all__ = [
    "StructuredLogger",
    "extract_trace_context",
    "get_logger",
    "init_tracing",
    "inject_trace_context",
    "instrument_consume",
    "instrument_db_query",
    "instrument_http_request",
    "instrument_produce",
    "instrument_scheduled_job",
    "shutdown_tracing",
]
