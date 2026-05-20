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

"""Environment-driven configuration for the event-bus SDK.

Every connection detail is read from the environment — never hard-coded — so
the same SDK code runs unchanged against a local RedPanda container (high
host ports, see ``docker-compose.dev.yml`` and the issue-13 isolated
container), the CI RedPanda service (standard ports) and RedPanda Cloud BYOC
in production (ADR-0011). No credential is ever embedded in code (PRD §7g).

The two environment variables that matter:

- ``TRUSTLIST_EVENT_BUS_BROKERS`` — the Kafka bootstrap servers, a
  comma-separated ``host:port`` list.
- ``TRUSTLIST_SCHEMA_REGISTRY_URL`` — the base URL of the Confluent-API
  schema registry (RedPanda's built-in registry).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Environment variable names. Public so callers and tests can reference them
# without re-typing the string literals.
BROKERS_ENV = "TRUSTLIST_EVENT_BUS_BROKERS"
SCHEMA_REGISTRY_ENV = "TRUSTLIST_SCHEMA_REGISTRY_URL"


@dataclass(frozen=True, slots=True)
class EventBusConfig:
    """Resolved connection settings for the event bus and schema registry.

    :ivar brokers: the Kafka bootstrap-server list, ``host:port`` comma-joined.
    :ivar schema_registry_url: the base URL of the schema registry.
    """

    brokers: str
    schema_registry_url: str

    @classmethod
    def from_env(cls) -> EventBusConfig:
        """Build the configuration from environment variables.

        :raises ValueError: when either required variable is unset or empty.
            Failing loudly at start-up is deliberate — a silently-defaulted
            broker address would let a producer appear healthy while writing
            nowhere.
        """
        brokers = os.environ.get(BROKERS_ENV, "").strip()
        registry = os.environ.get(SCHEMA_REGISTRY_ENV, "").strip()
        if not brokers:
            raise ValueError(
                f"{BROKERS_ENV} is unset; the event-bus SDK reads every "
                "connection detail from the environment and never hard-codes "
                "a broker address."
            )
        if not registry:
            raise ValueError(
                f"{SCHEMA_REGISTRY_ENV} is unset; the event-bus SDK reads the "
                "schema-registry URL from the environment."
            )
        return cls(brokers=brokers, schema_registry_url=registry.rstrip("/"))
