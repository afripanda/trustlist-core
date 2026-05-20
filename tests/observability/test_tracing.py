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

"""Tests for exporter selection in the tracing-initialisation module.

These tests assert the safe-by-default behaviour: with no OTLP endpoint
configured the library never reaches the network, and an explicit
``TRUSTLIST_OTEL_EXPORTER`` overrides the inference.
"""

from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export import ConsoleSpanExporter

from observability.tracing import (
    ExporterKind,
    _build_exporter,
    _resolve_exporter_kind,
)


def test_unset_endpoint_resolves_to_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no OTLP endpoint and no override, the console exporter is chosen."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("TRUSTLIST_OTEL_EXPORTER", raising=False)
    assert _resolve_exporter_kind() is ExporterKind.CONSOLE


def test_otlp_endpoint_resolves_to_otlp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting an OTLP endpoint (the Honeycomb path) selects the OTLP exporter."""
    monkeypatch.delenv("TRUSTLIST_OTEL_EXPORTER", raising=False)
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "https://api.honeycomb.io"
    )
    assert _resolve_exporter_kind() is ExporterKind.OTLP


def test_explicit_override_wins_over_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TRUSTLIST_OTEL_EXPORTER overrides the endpoint-based inference."""
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "https://api.honeycomb.io"
    )
    monkeypatch.setenv("TRUSTLIST_OTEL_EXPORTER", "none")
    assert _resolve_exporter_kind() is ExporterKind.NONE


def test_unknown_override_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unrecognised exporter name fails loudly rather than silently."""
    monkeypatch.setenv("TRUSTLIST_OTEL_EXPORTER", "carrier-pigeon")
    with pytest.raises(ValueError, match="not a recognised exporter"):
        _resolve_exporter_kind()


def test_build_console_exporter() -> None:
    """The console kind builds a ConsoleSpanExporter."""
    exporter = _build_exporter(ExporterKind.CONSOLE)
    assert isinstance(exporter, ConsoleSpanExporter)


def test_build_none_exporter_is_none() -> None:
    """The none kind builds no exporter at all."""
    assert _build_exporter(ExporterKind.NONE) is None
