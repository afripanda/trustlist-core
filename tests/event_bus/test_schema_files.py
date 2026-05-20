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

"""Unit tests for the ``event-schema/`` JSON Schema files (PRD §7b).

§7b: payload schemas live under ``event-schema/`` and are CI-validated. These
tests need no registry — they assert the committed files are well-formed JSON
Schema documents and that the SDK can discover them.
"""

from __future__ import annotations

from jsonschema import Draft202012Validator

from trustlist_event_bus.schema_files import iter_schema_files, load_schema

# The §7b acceptance round-trip uses this event type (PRD §8.2).
_EXAMPLE_COLLECTOR = "signal.tier-one.example-collector"


def test_event_schema_directory_is_discovered() -> None:
    """The SDK discovers at least the example-collector schema file."""
    discovered = {event_type for event_type, _ in iter_schema_files()}
    assert _EXAMPLE_COLLECTOR in discovered


def test_every_committed_schema_is_a_valid_json_schema() -> None:
    """Each event-schema/ file is itself a valid JSON Schema document.

    This is the local half of the §7b discipline; the registry round-trip is
    exercised by the integration suite.
    """
    files = list(iter_schema_files())
    assert files, "expected at least one schema file under event-schema/"
    for event_type, path in files:
        schema = load_schema(path)
        # check_schema raises if the document is not valid JSON Schema.
        Draft202012Validator.check_schema(schema)
        assert schema.get("type") == "object", (
            f"{event_type}: payload schemas describe a JSON object"
        )


def test_example_collector_schema_accepts_a_well_formed_payload() -> None:
    """The example-collector schema accepts a representative payload."""
    schema = next(
        load_schema(path)
        for event_type, path in iter_schema_files()
        if event_type == _EXAMPLE_COLLECTOR
    )
    validator = Draft202012Validator(schema)
    validator.validate(
        {
            "domain_id": "11111111-1111-1111-1111-111111111111",
            "signal_class": "dns",
            "observed_at": "2026-05-20T12:00:00+00:00",
            "observed_value": {"resolves": True},
        }
    )


def test_example_collector_schema_rejects_a_malformed_payload() -> None:
    """The example-collector schema rejects a payload missing a required field."""
    schema = next(
        load_schema(path)
        for event_type, path in iter_schema_files()
        if event_type == _EXAMPLE_COLLECTOR
    )
    validator = Draft202012Validator(schema)
    errors = list(validator.iter_errors({"signal_class": "dns"}))
    assert errors, "a payload missing domain_id must fail validation"
