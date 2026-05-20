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

"""Tests for the structured JSON logger (Stage 0 PRD §7f field schema)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from observability.decorators import instrument_scheduled_job
from observability.logging import get_logger

# The exact field set Stage 0 PRD §7f fixes for every structured log line.
_SCHEMA_FIELDS = {
    "timestamp",
    "level",
    "component",
    "trace_id",
    "span_id",
    "message",
}

# A reader callable: invoking it drains stderr and returns the parsed lines.
LineReader = Callable[[], list[dict[str, object]]]


@pytest.fixture
def read_lines(capsys: pytest.CaptureFixture[str]) -> LineReader:
    """Return a callable that drains stderr and parses the JSON log lines."""

    def reader() -> list[dict[str, object]]:
        captured = capsys.readouterr()
        return [
            json.loads(raw)
            for raw in captured.err.splitlines()
            if raw.strip()
        ]

    return reader


def test_logger_emits_the_full_field_schema(read_lines: LineReader) -> None:
    """An emitted line carries exactly the six §7f schema fields by default."""
    logger = get_logger("scoring-engine")
    logger.info("score computed")

    lines = read_lines()
    assert len(lines) == 1
    entry = lines[0]
    assert _SCHEMA_FIELDS.issubset(entry.keys())
    # With no event-specific fields, only the schema fields appear.
    assert set(entry.keys()) == _SCHEMA_FIELDS


def test_logger_records_component_level_and_message(
    read_lines: LineReader,
) -> None:
    """The component, level and message fields carry the supplied values."""
    logger = get_logger("signal-collector-framework")
    logger.warning("collector lagging")

    entry = read_lines()[0]
    assert entry["component"] == "signal-collector-framework"
    assert entry["level"] == "WARNING"
    assert entry["message"] == "collector lagging"


def test_timestamp_is_iso8601_utc(read_lines: LineReader) -> None:
    """The timestamp is ISO-8601 with a UTC offset and millisecond precision."""
    get_logger("event-bus-sdk").info("published")

    timestamp = read_lines()[0]["timestamp"]
    assert isinstance(timestamp, str)
    # Millisecond precision renders three fractional digits, and UTC renders
    # as a +00:00 offset.
    assert timestamp.endswith("+00:00")
    assert "." in timestamp


def test_event_specific_fields_are_merged_top_level(
    read_lines: LineReader,
) -> None:
    """Arbitrary keyword arguments appear as top-level fields in the JSON."""
    logger = get_logger("scoring-engine")
    logger.info(
        "domain rescored",
        domain_id="d-123",
        composite_score=0.82,
        verdict="Green",
    )

    entry = read_lines()[0]
    assert entry["domain_id"] == "d-123"
    assert entry["composite_score"] == 0.82
    assert entry["verdict"] == "Green"
    # The schema fields are still present alongside the event-specific ones.
    assert _SCHEMA_FIELDS.issubset(entry.keys())


@pytest.mark.parametrize(
    "reserved_field", ["timestamp", "level", "component", "trace_id", "span_id"]
)
def test_reserved_field_collision_raises(reserved_field: str) -> None:
    """An event-specific field colliding with a reserved name raises.

    ``message`` cannot be tested this way — it is a positional parameter of the
    log methods, so passing it as a keyword is a Python-level argument clash
    rather than a schema collision. The other five reserved names arrive purely
    as ``**fields`` keywords and are caught by the schema guard.
    """
    logger = get_logger("scoring-engine")
    with pytest.raises(ValueError, match="reserved"):
        logger.info("bad", **{reserved_field: "shadowing the schema"})


def test_trace_and_span_ids_are_null_without_an_active_span(
    read_lines: LineReader,
) -> None:
    """Outside any span, trace_id and span_id are JSON null."""
    get_logger("event-bus-sdk").info("no span here")

    entry = read_lines()[0]
    assert entry["trace_id"] is None
    assert entry["span_id"] is None


def test_trace_and_span_ids_populate_inside_a_span(
    span_exporter: InMemorySpanExporter,
    read_lines: LineReader,
) -> None:
    """Inside an instrumented span, the line carries that span's identifiers."""
    logger = get_logger("scoring-engine")

    @instrument_scheduled_job("nightly-rescore")
    def job() -> None:
        logger.info("inside the span")

    job()

    entry = read_lines()[0]
    trace_id = entry["trace_id"]
    span_id = entry["span_id"]
    assert isinstance(trace_id, str)
    assert isinstance(span_id, str)
    assert len(trace_id) == 32
    assert len(span_id) == 16

    # The ids in the log line match the span the SDK actually recorded.
    spans = span_exporter.get_finished_spans()
    assert len(spans) == 1
    context = spans[0].get_span_context()
    assert context is not None
    assert trace_id == format(context.trace_id, "032x")
    assert span_id == format(context.span_id, "016x")


def test_error_with_exc_info_attaches_a_traceback(
    read_lines: LineReader,
) -> None:
    """error(..., exc_info=True) attaches a rendered traceback."""
    logger = get_logger("scoring-engine")
    try:
        raise RuntimeError("scoring failed")
    except RuntimeError:
        logger.error("scoring error", exc_info=True)

    entry = read_lines()[0]
    assert "exception" in entry
    assert "RuntimeError" in str(entry["exception"])


def test_log_level_filters_below_threshold(read_lines: LineReader) -> None:
    """A logger created at WARNING does not emit INFO lines."""
    logger = get_logger("quiet-component", level=logging.WARNING)
    logger.info("should be filtered")
    logger.warning("should appear")

    lines = read_lines()
    assert len(lines) == 1
    assert lines[0]["message"] == "should appear"
