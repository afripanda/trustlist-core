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

"""Loading and registering the ``event-schema/`` JSON Schema files.

Stage 0 PRD §7b: "All payload schemas are stored in ``trustlist-core`` under
``event-schema/`` and CI-validated against the registry on every PR." This
module is the bridge between those committed files and the running schema
registry. It:

- discovers every ``<event_type>.schema.json`` file under ``event-schema/``;
- derives the ``event_type`` from each filename;
- registers each schema with a :class:`~trustlist_event_bus.schema_registry.SchemaRegistry`.

The CI ``event-schema`` step calls :func:`register_all` so the registry always
reflects the committed schemas; the integration tests call it to provision the
registry before a round-trip.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from trustlist_event_bus.schema_registry import SchemaRegistry

# Files carry this suffix; the part before it is the event_type.
_SCHEMA_SUFFIX = ".schema.json"


def _repo_event_schema_dir() -> Path:
    """Return the repository's ``event-schema/`` directory.

    This file lives at ``event-bus-sdk/trustlist_event_bus/schema_files.py``;
    the schema directory is ``event-schema/`` two levels up from the package.
    """
    return Path(__file__).resolve().parents[2] / "event-schema"


def iter_schema_files(directory: Path | None = None) -> Iterator[tuple[str, Path]]:
    """Yield ``(event_type, path)`` for every schema file under ``directory``.

    :param directory: the directory to scan; defaults to the repository's
        ``event-schema/`` directory.
    """
    base = directory or _repo_event_schema_dir()
    for path in sorted(base.glob(f"*{_SCHEMA_SUFFIX}")):
        event_type = path.name[: -len(_SCHEMA_SUFFIX)]
        yield event_type, path


def load_schema(path: Path) -> dict[str, Any]:
    """Load and parse a JSON Schema file."""
    parsed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return parsed


def register_all(
    registry: SchemaRegistry,
    *,
    directory: Path | None = None,
) -> dict[str, int]:
    """Register every ``event-schema/`` file with ``registry``.

    :param registry: the schema registry to populate.
    :param directory: the directory to scan; defaults to ``event-schema/``.
    :returns: a mapping of ``event_type`` to the registry-assigned schema id.
    """
    registered: dict[str, int] = {}
    for event_type, path in iter_schema_files(directory):
        schema = load_schema(path)
        registered[event_type] = registry.register(event_type, schema)
    return registered
