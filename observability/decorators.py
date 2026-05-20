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

"""Instrumentation decorators for the five §7f surfaces.

Stage 0 PRD §7f names five surfaces that every TrustList component instruments:
incoming HTTP requests, DB queries, event-bus produce, event-bus consume and
scheduled jobs. This module provides one decorator per surface. Each:

- starts an OpenTelemetry span around the wrapped callable, with a span name
  and ``SpanKind`` appropriate to the surface;
- stamps a small set of conventional attributes onto the span;
- records exceptions and sets the span status to ``ERROR`` when the wrapped
  callable raises, then re-raises;
- always ends the span, success or failure.

The event-bus consume decorator additionally accepts a carrier so the consumer
span is parented to the producer's trace, making a signal's collector → bus →
consumer flow a single distributed trace.

Each decorator preserves the wrapped callable's signature via
:func:`functools.wraps`, and works on both synchronous and asynchronous
callables.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar, cast

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from observability.propagation import extract_trace_context

# The instrumentation scope name spans created by the decorators are attributed
# to. The tracer itself is fetched lazily (see :func:`_tracer`) so it always
# reflects the currently-installed tracer provider.
_INSTRUMENTATION_SCOPE = "trustlist.observability"

F = TypeVar("F", bound=Callable[..., Any])


def _tracer() -> trace.Tracer:
    """Return the instrumentation tracer from the current global provider.

    Resolved on every call rather than cached at import time so that a span is
    always created against whichever tracer provider is installed when the
    decorated callable runs.
    """
    return trace.get_tracer(_INSTRUMENTATION_SCOPE)


def _run_in_span(
    func: Callable[..., Any],
    span_name: str,
    span_kind: SpanKind,
    attributes: Mapping[str, Any],
    carrier: Mapping[str, str] | None,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Execute ``func`` inside a span, handling sync and async callables.

    When ``func`` is a coroutine function, an awaitable is returned that keeps
    the span open until the coroutine completes; otherwise the call is run
    synchronously within the span.
    """
    parent = (
        extract_trace_context(dict(carrier)) if carrier is not None else None
    )

    tracer = _tracer()

    if asyncio.iscoroutinefunction(func):

        async def _async_runner() -> Any:
            with tracer.start_as_current_span(
                span_name,
                kind=span_kind,
                context=parent,
                attributes=dict(attributes),
            ) as span:
                try:
                    return await cast(
                        Callable[..., Awaitable[Any]], func
                    )(*args, **kwargs)
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR, str(exc)))
                    raise

        return _async_runner()

    with tracer.start_as_current_span(
        span_name,
        kind=span_kind,
        context=parent,
        attributes=dict(attributes),
    ) as span:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def _make_decorator(
    span_kind: SpanKind,
    name_prefix: str,
    static_attributes: Mapping[str, Any],
) -> Callable[..., Callable[[F], F]]:
    """Build a decorator factory for one instrumentation surface.

    The returned factory accepts an optional ``name`` (defaulting to the
    wrapped callable's qualified name) and optional extra ``attributes``.
    """

    def factory(
        name: str | None = None,
        *,
        attributes: Mapping[str, Any] | None = None,
    ) -> Callable[[F], F]:
        def decorator(func: F) -> F:
            span_name = f"{name_prefix} {name or func.__qualname__}"
            merged: dict[str, Any] = dict(static_attributes)
            if attributes:
                merged.update(attributes)

            @functools.wraps(func)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return _run_in_span(
                    func, span_name, span_kind, merged, None, args, kwargs
                )

            return cast(F, wrapper)

        return decorator

    return factory


# ---------------------------------------------------------------------------
# Surface 1 — incoming HTTP requests.
# ---------------------------------------------------------------------------
instrument_http_request = _make_decorator(
    SpanKind.SERVER,
    "HTTP",
    {"trustlist.surface": "http_request"},
)
"""Instrument a handler for an incoming HTTP request (``SpanKind.SERVER``)."""


# ---------------------------------------------------------------------------
# Surface 2 — database queries.
# ---------------------------------------------------------------------------
instrument_db_query = _make_decorator(
    SpanKind.CLIENT,
    "DB",
    {"trustlist.surface": "db_query"},
)
"""Instrument a database query (``SpanKind.CLIENT``)."""


# ---------------------------------------------------------------------------
# Surface 3 — event-bus produce.
# ---------------------------------------------------------------------------
instrument_produce = _make_decorator(
    SpanKind.PRODUCER,
    "PRODUCE",
    {"trustlist.surface": "event_produce"},
)
"""Instrument an event-bus produce call (``SpanKind.PRODUCER``)."""


# ---------------------------------------------------------------------------
# Surface 5 — scheduled jobs.
# ---------------------------------------------------------------------------
instrument_scheduled_job = _make_decorator(
    SpanKind.INTERNAL,
    "JOB",
    {"trustlist.surface": "scheduled_job"},
)
"""Instrument a scheduled / cron job (``SpanKind.INTERNAL``)."""


# ---------------------------------------------------------------------------
# Surface 4 — event-bus consume.
#
# Consume needs its own factory because it must read the W3C trace context out
# of the event envelope so the consumer span joins the producer's trace.
# ---------------------------------------------------------------------------
def instrument_consume(
    name: str | None = None,
    *,
    carrier_arg: str = "carrier",
    attributes: Mapping[str, Any] | None = None,
) -> Callable[[F], F]:
    """Instrument an event-bus consume handler (``SpanKind.CONSUMER``).

    The decorated handler is expected to receive the W3C trace-context carrier
    — in production, the event envelope's ``trace_context`` field — so the
    consumer span can be parented to the producing component's trace.

    :param name: span name suffix; defaults to the handler's qualified name.
    :param carrier_arg: the name of the keyword argument carrying the
        ``dict`` of W3C headers. The argument is consulted but left in place,
        so the wrapped handler still receives it.
    :param attributes: optional extra span attributes.

    When the named carrier argument is absent, or is not a mapping, the span
    simply starts a fresh root trace rather than failing — the handler still
    runs.
    """

    def decorator(func: F) -> F:
        span_name = f"CONSUME {name or func.__qualname__}"
        merged: dict[str, Any] = {"trustlist.surface": "event_consume"}
        if attributes:
            merged.update(attributes)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            candidate = kwargs.get(carrier_arg)
            carrier = (
                cast(Mapping[str, str], candidate)
                if isinstance(candidate, Mapping)
                else None
            )
            return _run_in_span(
                func,
                span_name,
                SpanKind.CONSUMER,
                merged,
                carrier,
                args,
                kwargs,
            )

        return cast(F, wrapper)

    return decorator
