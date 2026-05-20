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

"""Structured JSON logging for TrustList components.

Every TrustList component emits logs through :func:`get_logger`, producing a
single JSON object per line with the field schema fixed by Stage 0 PRD §7f:

- ``timestamp`` — ISO-8601 UTC, millisecond precision.
- ``level`` — the log level name (``INFO``, ``ERROR`` and so on).
- ``component`` — the emitting component, supplied to :func:`get_logger`.
- ``trace_id`` — the active 32-hex-character trace id, or ``None`` when there
  is no active span.
- ``span_id`` — the active 16-hex-character span id, or ``None`` likewise.
- ``message`` — the human-readable log message.
- arbitrary event-specific fields — any keyword arguments passed to the log
  call are merged in at the top level.

Wiring the logger to the OpenTelemetry tracing context means a log line emitted
inside an instrumented span is automatically correlatable with that span in
Honeycomb (ADR-0012), with no extra work at the call site.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

# The reserved §7f field names. Event-specific fields may not collide with
# these; an attempt to do so raises rather than silently shadowing the schema.
_RESERVED_FIELDS = frozenset(
    {"timestamp", "level", "component", "trace_id", "span_id", "message"}
)

# Attribute name under which the component label is stashed on the stdlib
# LogRecord so the formatter can recover it.
_COMPONENT_ATTR = "trustlist_component"

# Attribute name under which event-specific fields are stashed on the record.
_FIELDS_ATTR = "trustlist_fields"


def _format_trace_id(trace_id: int) -> str | None:
    """Render an integer trace id as 32 lowercase hex chars, or ``None``."""
    if trace_id == 0:
        return None
    return format(trace_id, "032x")


def _format_span_id(span_id: int) -> str | None:
    """Render an integer span id as 16 lowercase hex chars, or ``None``."""
    if span_id == 0:
        return None
    return format(span_id, "016x")


class _StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """A stream handler that always writes to the *current* ``sys.stderr``.

    The stdlib :class:`logging.StreamHandler` binds to whatever stream object
    it was given at construction time. That is awkward for a process-lifetime
    logger because anything that legitimately swaps ``sys.stderr`` — a test
    harness's output capture, for one — would leave the handler writing to a
    stale, possibly closed stream. Resolving the stream on every emit keeps the
    logger robust against such swaps.
    """

    def __init__(self) -> None:
        """Initialise the handler with no fixed stream."""
        super().__init__()

    @property
    def stream(self) -> Any:
        """Return the live ``sys.stderr`` at emit time."""
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        """Ignore stream assignment; the stream is always ``sys.stderr``."""


class JsonFormatter(logging.Formatter):
    """A :class:`logging.Formatter` that emits the §7f JSON field schema."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a single-line JSON object."""
        span_context = trace.get_current_span().get_span_context()

        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=UTC
            ).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "component": getattr(record, _COMPONENT_ATTR, record.name),
            "trace_id": _format_trace_id(span_context.trace_id),
            "span_id": _format_span_id(span_context.span_id),
            "message": record.getMessage(),
        }

        fields: dict[str, Any] = getattr(record, _FIELDS_ATTR, {})
        entry.update(fields)

        if record.exc_info:
            # Attach a rendered traceback under a non-reserved field so error
            # logs carry their stack without breaking the flat schema.
            entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(entry, default=str, sort_keys=True)


class StructuredLogger:
    """A thin, schema-enforcing wrapper over a stdlib :class:`logging.Logger`.

    Instances are obtained via :func:`get_logger`. Each log method accepts a
    message plus arbitrary keyword arguments, which become event-specific
    fields in the emitted JSON. Passing a keyword that collides with a reserved
    §7f field name raises :class:`ValueError` — the schema is fixed.
    """

    def __init__(self, component: str, logger: logging.Logger) -> None:
        """Bind a structured logger to ``component`` over ``logger``."""
        self._component = component
        self._logger = logger

    @property
    def component(self) -> str:
        """The component label stamped onto every line this logger emits."""
        return self._component

    def _log(self, level: int, message: str, fields: dict[str, Any]) -> None:
        """Emit one record at ``level`` with validated event-specific fields."""
        collisions = _RESERVED_FIELDS.intersection(fields)
        if collisions:
            offending = ", ".join(sorted(collisions))
            raise ValueError(
                f"event-specific log fields may not use the reserved §7f "
                f"field names: {offending}."
            )
        exc_info = fields.pop("exc_info", None)
        self._logger.log(
            level,
            message,
            exc_info=exc_info,
            extra={
                _COMPONENT_ATTR: self._component,
                _FIELDS_ATTR: fields,
            },
        )

    def debug(self, message: str, **fields: Any) -> None:
        """Emit a ``DEBUG``-level structured log line."""
        self._log(logging.DEBUG, message, fields)

    def info(self, message: str, **fields: Any) -> None:
        """Emit an ``INFO``-level structured log line."""
        self._log(logging.INFO, message, fields)

    def warning(self, message: str, **fields: Any) -> None:
        """Emit a ``WARNING``-level structured log line."""
        self._log(logging.WARNING, message, fields)

    def error(self, message: str, **fields: Any) -> None:
        """Emit an ``ERROR``-level structured log line.

        Pass ``exc_info=True`` to attach the active exception's traceback under
        an ``exception`` field.
        """
        self._log(logging.ERROR, message, fields)

    def critical(self, message: str, **fields: Any) -> None:
        """Emit a ``CRITICAL``-level structured log line."""
        self._log(logging.CRITICAL, message, fields)


def get_logger(
    component: str,
    *,
    level: int = logging.INFO,
) -> StructuredLogger:
    """Return a :class:`StructuredLogger` for ``component``.

    :param component: the component name stamped into every line's
        ``component`` field — for example ``"scoring-engine"`` or
        ``"signal-collector-framework"``.
    :param level: the minimum level to emit; defaults to ``INFO``.

    The underlying stdlib logger is configured exactly once per component name
    with a single :class:`JsonFormatter` handler writing to ``stderr``.
    Propagation to the root logger is disabled so the JSON output is never
    duplicated by an ancestor handler.
    """
    underlying = logging.getLogger(f"trustlist.{component}")
    underlying.setLevel(level)
    underlying.propagate = False

    already_wired = any(
        isinstance(handler, _StderrHandler) for handler in underlying.handlers
    )
    if not already_wired:
        handler = _StderrHandler()
        handler.setFormatter(JsonFormatter())
        underlying.addHandler(handler)

    return StructuredLogger(component, underlying)
