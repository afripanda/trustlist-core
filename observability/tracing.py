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

"""OpenTelemetry SDK initialisation for TrustList components.

This module wraps the OpenTelemetry tracing SDK so that application code calls a
single :func:`init_tracing` entry point rather than binding to the SDK directly.

Exporter selection (per ADR-0012, Honeycomb over OTLP):

- When ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set, traces are exported over OTLP to
  that endpoint. ``OTEL_EXPORTER_OTLP_HEADERS`` carries the Honeycomb API key as
  a comma-separated ``key=value`` list (for example
  ``x-honeycomb-team=<api-key>``). The endpoint and headers are read straight
  from the environment so that no secret is ever embedded in code (PRD §7g).
- When the endpoint is unset, the library falls back to a **console exporter**
  so that tests and local runs need neither a network nor an API key. Setting
  ``TRUSTLIST_OTEL_EXPORTER=none`` selects a no-op exporter instead, which is
  the quietest option for unit-test runs that assert against their own
  in-memory exporter.

The chosen behaviour keeps the library safe-by-default: importing and
initialising it in a test or a laptop checkout never reaches out to the
network.
"""

from __future__ import annotations

import os
from enum import StrEnum

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

# Environment variable that selects the exporter explicitly. When unset, the
# library infers the exporter from OTEL_EXPORTER_OTLP_ENDPOINT.
_EXPORTER_ENV = "TRUSTLIST_OTEL_EXPORTER"

# Standard OpenTelemetry environment variables for the OTLP exporter. Honeycomb
# is OTLP-based, so these are the only knobs an operator needs to point the
# library at Honeycomb.
_OTLP_ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"

# Name of the service emitting traces; surfaces as the resource attribute
# ``service.name`` in Honeycomb.
_SERVICE_NAME_ENV = "OTEL_SERVICE_NAME"


class ExporterKind(StrEnum):
    """The exporter back-ends the library knows how to construct."""

    OTLP = "otlp"
    CONSOLE = "console"
    NONE = "none"


def _resolve_exporter_kind() -> ExporterKind:
    """Decide which exporter to use from the environment.

    An explicit ``TRUSTLIST_OTEL_EXPORTER`` wins. Otherwise the presence of an
    OTLP endpoint selects OTLP, and its absence selects the console exporter so
    local runs and tests need no network.
    """
    explicit = os.environ.get(_EXPORTER_ENV, "").strip().lower()
    if explicit:
        try:
            return ExporterKind(explicit)
        except ValueError as exc:  # pragma: no cover - defensive guard
            valid = ", ".join(kind.value for kind in ExporterKind)
            raise ValueError(
                f"{_EXPORTER_ENV}={explicit!r} is not a recognised exporter; "
                f"expected one of: {valid}."
            ) from exc

    if os.environ.get(_OTLP_ENDPOINT_ENV, "").strip():
        return ExporterKind.OTLP
    return ExporterKind.CONSOLE


def _build_exporter(kind: ExporterKind) -> SpanExporter | None:
    """Construct the span exporter for ``kind``.

    Returns ``None`` for :attr:`ExporterKind.NONE`, signalling that no span
    processor should be registered at all.
    """
    if kind is ExporterKind.NONE:
        return None
    if kind is ExporterKind.CONSOLE:
        return ConsoleSpanExporter()
    # OTLP. Imported lazily so that environments without the OTLP exporter
    # extra installed can still use the console / no-op paths.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    # The OTLP exporter reads OTEL_EXPORTER_OTLP_ENDPOINT and
    # OTEL_EXPORTER_OTLP_HEADERS from the environment itself, so no secret is
    # passed through code here.
    return OTLPSpanExporter()


def init_tracing(
    service_name: str | None = None,
    *,
    exporter: SpanExporter | None = None,
) -> TracerProvider:
    """Initialise OpenTelemetry tracing and register it as the global provider.

    :param service_name: the value of the ``service.name`` resource attribute.
        When ``None``, ``OTEL_SERVICE_NAME`` is consulted, defaulting to
        ``"trustlist"``.
    :param exporter: an explicit span exporter. When supplied, it overrides the
        environment-driven selection — this is the hook tests use to inject an
        in-memory exporter.
    :returns: the configured :class:`~opentelemetry.sdk.trace.TracerProvider`.

    The returned provider is also installed as the process-global provider via
    :func:`opentelemetry.trace.set_tracer_provider`, so a component need call
    :func:`init_tracing` only once at start-up.
    """
    resolved_name = (
        service_name
        or os.environ.get(_SERVICE_NAME_ENV, "").strip()
        or "trustlist"
    )
    resource = Resource.create({"service.name": resolved_name})
    provider = TracerProvider(resource=resource)

    if exporter is not None:
        # An explicitly supplied exporter (typically an in-memory test
        # exporter) is registered synchronously so spans are visible the
        # instant they end, with no batch flush to wait on.
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        kind = _resolve_exporter_kind()
        built = _build_exporter(kind)
        if built is not None:
            provider.add_span_processor(BatchSpanProcessor(built))

    trace.set_tracer_provider(provider)
    return provider


def shutdown_tracing() -> None:
    """Flush and shut down the global tracer provider.

    Safe to call when tracing was never initialised, or was initialised with a
    provider that does not support shutdown — both are no-ops.
    """
    provider = trace.get_tracer_provider()
    shutdown = getattr(provider, "shutdown", None)
    if callable(shutdown):
        shutdown()
