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

"""Unit tests for environment-driven SDK configuration (PRD §7b / §7g).

Connection details are read from the environment and never hard-coded; these
tests assert that, and that missing settings fail loudly rather than silently
defaulting to a broker the producer writes nowhere into.
"""

from __future__ import annotations

import pytest

from trustlist_event_bus.config import (
    BROKERS_ENV,
    SCHEMA_REGISTRY_ENV,
    EventBusConfig,
)


def test_from_env_reads_both_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """from_env builds the config from the two environment variables."""
    monkeypatch.setenv(BROKERS_ENV, "localhost:19292")
    monkeypatch.setenv(SCHEMA_REGISTRY_ENV, "http://localhost:18191")
    config = EventBusConfig.from_env()
    assert config.brokers == "localhost:19292"
    assert config.schema_registry_url == "http://localhost:18191"


def test_from_env_strips_trailing_slash_from_registry_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trailing slash on the registry URL is normalised away."""
    monkeypatch.setenv(BROKERS_ENV, "localhost:19292")
    monkeypatch.setenv(SCHEMA_REGISTRY_ENV, "http://localhost:18191/")
    assert EventBusConfig.from_env().schema_registry_url == "http://localhost:18191"


def test_from_env_rejects_a_missing_broker_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing broker list fails loudly — never a silent default."""
    monkeypatch.delenv(BROKERS_ENV, raising=False)
    monkeypatch.setenv(SCHEMA_REGISTRY_ENV, "http://localhost:18191")
    with pytest.raises(ValueError, match=BROKERS_ENV):
        EventBusConfig.from_env()


def test_from_env_rejects_a_missing_registry_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing schema-registry URL fails loudly."""
    monkeypatch.setenv(BROKERS_ENV, "localhost:19292")
    monkeypatch.delenv(SCHEMA_REGISTRY_ENV, raising=False)
    with pytest.raises(ValueError, match=SCHEMA_REGISTRY_ENV):
        EventBusConfig.from_env()


def test_config_is_immutable() -> None:
    """The config dataclass is frozen."""
    config = EventBusConfig(
        brokers="localhost:19292", schema_registry_url="http://localhost:18191"
    )
    with pytest.raises((AttributeError, TypeError)):
        config.brokers = "tampered"  # type: ignore[misc]
