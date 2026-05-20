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

"""Shared fixtures for the observability test suite.

The :func:`span_exporter` fixture wires the tracing SDK to an in-memory span
exporter so the decorator and propagation tests can assert against the exact
spans produced — with no network and no Honeycomb credentials.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from observability.tracing import init_tracing


def _reset_global_provider() -> None:
    """Clear OpenTelemetry's process-global tracer provider.

    :func:`trace.set_tracer_provider` is guarded by a one-shot latch, so each
    test must clear both the latch and the cached provider to install a fresh
    one. These are OpenTelemetry SDK internals; the reset is confined to this
    test fixture and is the standard pattern for per-test provider isolation.
    """
    once_cls = type(trace._TRACER_PROVIDER_SET_ONCE)
    trace._TRACER_PROVIDER_SET_ONCE = once_cls()
    trace._TRACER_PROVIDER = None


@pytest.fixture
def span_exporter() -> Iterator[InMemorySpanExporter]:
    """Yield an in-memory exporter wired as the global tracer provider.

    The global provider is reset before and after each test so the tests stay
    independent of one another.
    """
    _reset_global_provider()

    exporter = InMemorySpanExporter()
    init_tracing("test-component", exporter=exporter)
    yield exporter

    exporter.clear()
    _reset_global_provider()
