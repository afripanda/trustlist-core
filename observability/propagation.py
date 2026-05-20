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

"""W3C trace-context propagation helpers.

These helpers move OpenTelemetry trace context to and from a plain ``dict``
carrier using the W3C Trace Context standard (``traceparent`` and
``tracestate`` keys). They are deliberately standalone and
independently-testable: the event-bus SDK (Stage 0 issue 13, not yet built)
will call :func:`inject_trace_context` when producing an event — writing the
carrier into the envelope's ``trace_context`` field — and
:func:`extract_trace_context` when consuming one, so that a signal's flow from
collector through the bus to the scoring engine forms a single distributed
trace (ADR-0012, PRD §7b event envelope).

Nothing here depends on the tracing SDK being initialised; the propagator
operates purely on the carrier and the supplied or current context.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry.propagators.textmap import default_getter, default_setter
from opentelemetry.trace.propagation.tracecontext import (
    TraceContextTextMapPropagator,
)

if TYPE_CHECKING:
    from opentelemetry.context import Context

# A single propagator instance is reused; it is stateless and thread-safe.
_PROPAGATOR = TraceContextTextMapPropagator()


def inject_trace_context(
    carrier: dict[str, str] | None = None,
    *,
    context: Context | None = None,
) -> dict[str, str]:
    """Write the active trace context into ``carrier`` as W3C headers.

    :param carrier: the dict to write into. When ``None`` a fresh dict is
        created. When supplied, it is mutated in place *and* returned, so the
        call site may use either style.
    :param context: the OpenTelemetry context to serialise. When ``None`` the
        currently-active context is used.
    :returns: the carrier, now carrying a ``traceparent`` key (and
        ``tracestate`` when non-empty).

    When there is no active span, the W3C propagator writes nothing; the
    returned carrier is simply unchanged. The event-bus SDK stores the result
    in the envelope's ``trace_context`` field.
    """
    target: dict[str, str] = carrier if carrier is not None else {}
    _PROPAGATOR.inject(target, context=context, setter=default_setter)
    return target


def extract_trace_context(carrier: dict[str, str]) -> Context:
    """Rebuild an OpenTelemetry context from a W3C-headers ``carrier``.

    :param carrier: a dict that may carry ``traceparent`` / ``tracestate``
        keys — typically the event envelope's ``trace_context`` field.
    :returns: a :class:`~opentelemetry.context.Context` carrying the remote
        span context. Pass it as the ``context`` argument when starting a
        consumer span so the new span is parented to the producer's trace.

    When the carrier holds no valid ``traceparent``, an empty (root) context is
    returned, which simply starts a fresh trace — the safe default.
    """
    return _PROPAGATOR.extract(carrier, getter=default_getter)
