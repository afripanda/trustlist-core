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

"""Fixtures for the Stage 0 end-to-end smoke test (PRD §8.2).

The smoke test exercises *two* real backing services at once — a real RedPanda
event bus and a real, migrated Postgres canonical store (PRD §7a / §7b: no
mocks). It therefore composes both fixture families:

- the canonical-store ``engine`` fixture from the repository-level
  ``tests/conftest.py`` (reachable here because that conftest sits above this
  package);
- the event-bus fixtures (``event_bus_config``, ``schema_registry``), defined
  *here* — the event-bus SDK's own ``tests/event_bus/conftest.py`` is a sibling
  package, so its fixtures are not visible to ``tests/smoke/`` and the small
  resolve/register pattern is repeated.

Every connection detail comes from the environment, never hard-coded
(PRD §7b / §7g): ``TRUSTLIST_DB_URL`` for Postgres, ``TRUSTLIST_EVENT_BUS_BROKERS``
and ``TRUSTLIST_SCHEMA_REGISTRY_URL`` for the bus. When the event-bus variables
are unset the smoke test is skipped, so a plain unit-test run needs no broker.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from trustlist_event_bus.config import (
    BROKERS_ENV,
    SCHEMA_REGISTRY_ENV,
    EventBusConfig,
)
from trustlist_event_bus.schema_files import register_all
from trustlist_event_bus.schema_registry import SchemaRegistry


@pytest.fixture(scope="session")
def event_bus_config() -> EventBusConfig:
    """Resolve the event-bus configuration, skipping when it is not set.

    A bare ``pytest`` invocation has neither event-bus variable; the skip keeps
    the unit-test run independent of any broker. The integration CI job and the
    local isolated-container workflow both export the variables.
    """
    if not os.environ.get(BROKERS_ENV) or not os.environ.get(SCHEMA_REGISTRY_ENV):
        pytest.skip(
            f"the Stage 0 smoke test needs {BROKERS_ENV} and "
            f"{SCHEMA_REGISTRY_ENV}; stand up a RedPanda and export them."
        )
    return EventBusConfig.from_env()


@pytest.fixture(scope="session")
def schema_registry(event_bus_config: EventBusConfig) -> SchemaRegistry:
    """A registry client with the ``event-schema/`` files registered.

    The synthetic ``signal.tier-one.example-collector`` payload schema lives
    under ``event-schema/``; registering it once per session lets the producer
    validate on produce and the consumer validate on consume against the same
    contract (PRD §7b).
    """
    registry = SchemaRegistry(event_bus_config.schema_registry_url)
    register_all(registry)
    return registry


@pytest.fixture
def smoke_topic() -> Iterator[str]:
    """Yield a per-test ``signal.tier-one.example-collector`` topic name.

    Each smoke-test invocation gets its own topic so re-runs and parallel runs
    never share a partition log. The base name is the §7b
    ``signal.tier-one.example-collector`` topic; a unique suffix isolates the
    run. Idempotency (PRD §8 criterion 5) is asserted at the canonical-store
    layer — the writer deduplicates regardless of which topic carried the
    event — so a fresh topic per run does not weaken the idempotency check.
    """
    yield f"signal.tier-one.example-collector.smoke-{uuid.uuid4().hex[:12]}"
