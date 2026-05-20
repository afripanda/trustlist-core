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

"""Schema-registry integration for the event-bus SDK (Stage 0 PRD §7b).

RedPanda ships a built-in, Confluent-API-compatible schema registry (ADR-0011).
This module wraps it for the SDK's purposes:

- registering a payload's JSON Schema under a subject keyed by ``event_type``;
- fetching the latest registered schema for an ``event_type``;
- validating a payload against that schema, on both the produce and the
  consume path (PRD §7b: "schema validation on both produce and consume").

Design choice — the *wire* format stays plain JSON. The SDK does not use the
Confluent magic-byte / schema-id framing: the envelope (see ``envelope.py``)
is self-describing JSON, and coupling the wire bytes to a registry round-trip
would make an event un-decodable without the registry. Instead the registry is
used as the *contract store* — schemas are registered there and CI validates
``event-schema/`` against it (PRD §7b) — while validation at runtime is done
locally with :mod:`jsonschema` against the schema the registry holds. The
schema is fetched once per ``event_type`` and cached for the validator's life.

The subject-naming strategy is the Confluent ``TopicNameStrategy`` analogue
keyed on ``event_type`` rather than topic: the subject is
``"<event_type>-value"``. ``event_type`` is the natural schema key per §7b
("schema registry ... holds payload schemas keyed by ``event_type``").
"""

from __future__ import annotations

import json
from typing import Any

from confluent_kafka.schema_registry import Schema, SchemaRegistryClient
from confluent_kafka.schema_registry.error import SchemaRegistryError as _ConfluentSRError
from jsonschema import Draft202012Validator
from jsonschema import ValidationError as _JsonSchemaValidationError
from jsonschema.protocols import Validator

from trustlist_event_bus.errors import SchemaRegistryError, SchemaValidationError

# The schema type string the Confluent / RedPanda registry uses for JSON
# Schema (as opposed to "AVRO" or "PROTOBUF").
_JSON_SCHEMA_TYPE = "JSON"


def subject_for(event_type: str) -> str:
    """Return the registry subject name for an ``event_type``.

    Mirrors Confluent's ``TopicNameStrategy`` ``-value`` suffix convention so
    the subjects are legible to any standard schema-registry tooling.
    """
    return f"{event_type}-value"


class SchemaRegistry:
    """A thin wrapper over the RedPanda / Confluent schema registry.

    One instance is shared by a producer or a consumer. It owns the HTTP
    client to the registry and an in-process cache of compiled JSON Schema
    validators, keyed by ``event_type``.

    :param url: the registry base URL — pass
        :attr:`~trustlist_event_bus.config.EventBusConfig.schema_registry_url`.
    """

    def __init__(self, url: str) -> None:
        """Open a registry client against ``url``."""
        self._client = SchemaRegistryClient({"url": url})
        self._validator_cache: dict[str, Validator] = {}

    def register(self, event_type: str, schema: dict[str, Any]) -> int:
        """Register ``schema`` as the JSON Schema for ``event_type``.

        Registration is idempotent at the registry: re-registering an
        identical schema returns the existing schema id. This is what the CI
        ``event-schema`` validation step calls to keep the registry in step
        with the JSON Schema files committed under ``event-schema/``.

        :param event_type: the event type the schema describes.
        :param schema: the JSON Schema document, as a dict.
        :returns: the registry-assigned schema id.
        :raises SchemaRegistryError: when the registry cannot be reached or
            rejects the schema.
        """
        registry_schema = Schema(
            schema_str=json.dumps(schema),
            schema_type=_JSON_SCHEMA_TYPE,
        )
        try:
            return self._client.register_schema(
                subject_for(event_type), registry_schema
            )
        except _ConfluentSRError as exc:  # pragma: no cover - network path
            raise SchemaRegistryError(
                f"failed to register schema for event_type {event_type!r}: {exc}"
            ) from exc

    def _validator(self, event_type: str) -> Validator:
        """Return a compiled validator for ``event_type``, fetching once.

        The latest schema registered for the ``event_type`` subject is fetched
        from the registry, compiled into a :mod:`jsonschema` validator and
        cached for the registry instance's lifetime.

        :raises SchemaRegistryError: when no schema is registered for the
            event type, or the registry is unreachable.
        """
        cached = self._validator_cache.get(event_type)
        if cached is not None:
            return cached
        try:
            registered = self._client.get_latest_version(subject_for(event_type))
        except _ConfluentSRError as exc:
            raise SchemaRegistryError(
                f"no schema registered for event_type {event_type!r} "
                f"(subject {subject_for(event_type)!r}): {exc}"
            ) from exc
        schema_str = registered.schema.schema_str
        if schema_str is None:  # pragma: no cover - registry always returns one
            raise SchemaRegistryError(
                f"registry returned an empty schema body for event_type "
                f"{event_type!r}."
            )
        schema_doc = json.loads(schema_str)
        validator = Draft202012Validator(schema_doc)
        self._validator_cache[event_type] = validator
        return validator

    def validate(self, event_type: str, payload: dict[str, Any]) -> None:
        """Validate ``payload`` against the registered schema for its type.

        Called by the producer before publishing and by the consumer before
        the handler runs (PRD §7b).

        :raises SchemaValidationError: when the payload does not conform.
        :raises SchemaRegistryError: when the schema cannot be retrieved.
        """
        validator = self._validator(event_type)
        try:
            validator.validate(payload)
        except _JsonSchemaValidationError as exc:
            raise SchemaValidationError(
                f"payload for event_type {event_type!r} failed schema "
                f"validation: {exc.message}"
            ) from exc
