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

"""Shared fixtures for the event-bus SDK integration tests.

The integration tests run against a **real RedPanda** — no mocks, per the
Stage 0 PRD §7a / §7b discipline. Every connection detail comes from the
environment (never hard-coded), so the same tests run against:

- the issue-13 local isolated container (``trustlist-issue13-redpanda``, Kafka
  on host port 19292, schema registry on 18191);
- the CI RedPanda service container (standard ports 9092 / 8081).

Set ``TRUSTLIST_EVENT_BUS_BROKERS`` and ``TRUSTLIST_SCHEMA_REGISTRY_URL``
before running ``pytest -m integration``. When either is unset the integration
tests are skipped, so a plain unit-test run needs no broker.
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

    A bare ``pytest`` invocation has neither environment variable; the skip
    keeps the unit-test run independent of any broker. The integration CI job
    and the local container workflow both export the variables.
    """
    if not os.environ.get(BROKERS_ENV) or not os.environ.get(SCHEMA_REGISTRY_ENV):
        pytest.skip(
            f"event-bus integration tests need {BROKERS_ENV} and "
            f"{SCHEMA_REGISTRY_ENV}; stand up a RedPanda and export them."
        )
    return EventBusConfig.from_env()


@pytest.fixture(scope="session")
def schema_registry(event_bus_config: EventBusConfig) -> SchemaRegistry:
    """A registry client with the ``event-schema/`` files registered.

    The committed payload schemas are registered once per session so producer
    and consumer share an already-provisioned registry — the §7b discipline of
    schemas living under ``event-schema/`` and being registered.
    """
    registry = SchemaRegistry(event_bus_config.schema_registry_url)
    register_all(registry)
    return registry


@pytest.fixture
def unique_topic() -> Iterator[str]:
    """Yield a per-test topic name so tests never share a partition log."""
    yield f"signal.tier-one.example-collector.it-{uuid.uuid4().hex[:12]}"
