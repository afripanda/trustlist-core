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

"""Fixtures for the Stage 0 reproducibility test (PRD §8 acceptance criterion 5).

The reproducibility test extends the issue-22 smoke-test harness (issue 24
"extends the smoke-test harness"). It therefore needs the same two real backing
services the smoke test uses — a real RedPanda event bus and a real Postgres
canonical store (PRD §7a / §7b: no mocks).

The event-bus fixtures (``event_bus_config``, ``schema_registry``) are defined
*here*, in the same small resolve/register form the smoke test's own
``conftest.py`` uses. They cannot be inherited from ``tests/smoke/conftest.py``
— that is a sibling package, not an ancestor — and pytest no longer allows a
non-top-level conftest to re-export fixtures via ``pytest_plugins``. Repeating
the few lines keeps the reproducibility package self-contained, exactly as the
smoke conftest itself notes the event-bus SDK's conftest does for the same
reason. The canonical-store ``engine`` fixture *is* inherited — it lives in the
repository-level :mod:`tests.conftest`, which sits above this package.

The one fixture unique to the reproducibility test is :func:`fresh_migrate` —
the capability PRD §8 criterion 5 needs that the smoke test does not: re-running
the fixture against a *freshly-migrated* database. It drops the canonical schema
and re-applies the Alembic migrations, giving each reproducibility run a schema
with no carried-over state at all.

Every connection detail comes from the environment, never hard-coded
(PRD §7b / §7g): ``TRUSTLIST_DB_URL`` for Postgres, ``TRUSTLIST_EVENT_BUS_BROKERS``
and ``TRUSTLIST_SCHEMA_REGISTRY_URL`` for the bus. When the event-bus variables
are unset the reproducibility test is skipped, so a plain unit-test run needs no
broker.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

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
    the unit-test run independent of any broker. The CI ``reproducibility`` job
    and the local isolated-container workflow both export the variables.
    """
    if not os.environ.get(BROKERS_ENV) or not os.environ.get(SCHEMA_REGISTRY_ENV):
        pytest.skip(
            f"the Stage 0 reproducibility test needs {BROKERS_ENV} and "
            f"{SCHEMA_REGISTRY_ENV}; stand up a RedPanda and export them."
        )
    return EventBusConfig.from_env()


@pytest.fixture(scope="session")
def schema_registry(event_bus_config: EventBusConfig) -> SchemaRegistry:
    """A registry client with the ``event-schema/`` files registered.

    The synthetic ``signal.tier-one.example-collector`` payload schema lives
    under ``event-schema/``; registering it once per session lets the producer
    validate on produce and the consumer validate on consume against the same
    contract (PRD §7b). The reproducibility test reuses the smoke test's
    fixture signal, which is an ``example-collector`` event.
    """
    registry = SchemaRegistry(event_bus_config.schema_registry_url)
    register_all(registry)
    return registry


# The data-model directory holds ``alembic.ini``; ``alembic upgrade head`` must
# run with it as the working directory (it carries ``script_location`` and
# ``prepend_sys_path``). Resolved from this file so the test is location-stable.
_DATA_MODEL_DIR = Path(__file__).resolve().parents[2] / "data-model"


def _run_alembic_upgrade_head() -> None:
    """Apply the canonical-store migrations to head against ``TRUSTLIST_DB_URL``.

    Runs ``alembic upgrade head`` as a subprocess from the ``data-model``
    directory — exactly the command the CI ``reproducibility`` job and the
    local workflow run, so the test exercises the real migration path rather
    than a re-implementation of it. The database URL reaches Alembic through
    the inherited ``TRUSTLIST_DB_URL`` environment variable (``env.py``).
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, trusted input
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_DATA_MODEL_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed while preparing a fresh schema for "
            f"the reproducibility test:\n{result.stdout}\n{result.stderr}"
        )


def _drop_canonical_schema(engine: Engine) -> None:
    """Drop the canonical schema so the next migration runs against a clean DB.

    ``DROP SCHEMA public CASCADE`` removes every canonical table, enum type,
    materialised view and index in one statement. The ``trustlist_app``
    application role is a *cluster-level* object, not a schema object, so it
    survives the drop — and the migration's role-creation step is guarded with
    ``IF NOT EXISTS``, so the subsequent ``alembic upgrade head`` re-grants
    against the freshly-created tables without error.

    The schema is recreated immediately so the migration has somewhere to
    build. Crucially the recreated ``public`` schema is re-granted ``USAGE`` to
    ``PUBLIC`` — a plain ``CREATE SCHEMA public`` on Postgres 16 grants schema
    access to the owner only, which would leave the ``trustlist_app`` role
    unable to *see* the migrated tables. Restoring the standard grant makes the
    post-drop database byte-for-byte equivalent to a never-touched fresh
    database, which is precisely what "freshly-migrated" must mean.
    """
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        # Restore the default schema-level grants a pristine database carries,
        # so the application role can reach the freshly-migrated tables.
        conn.execute(text("GRANT USAGE, CREATE ON SCHEMA public TO PUBLIC"))


@pytest.fixture
def fresh_migrate(engine: Engine) -> Iterator[Callable[[], Engine]]:
    """Yield a callable that drops and re-applies the canonical-store schema.

    PRD §8 criterion 5 requires the reproducibility test to re-run "against a
    freshly-migrated database". Calling the yielded function does exactly that:
    it drops the canonical schema and runs ``alembic upgrade head`` again, then
    returns the same engine — now bound to a schema with no state at all from
    the first run.

    The fixture leaves the database freshly-migrated and empty at teardown too,
    so the suite stays hermetic for whatever runs next.

    :returns: a zero-argument callable returning the migrated
        :class:`~sqlalchemy.Engine`.
    """
    if not os.environ.get("TRUSTLIST_DB_URL"):
        pytest.skip(
            "the reproducibility test needs TRUSTLIST_DB_URL pointing at a "
            "real Postgres; migrate one and export it."
        )

    def _fresh() -> Engine:
        _drop_canonical_schema(engine)
        _run_alembic_upgrade_head()
        return engine

    try:
        yield _fresh
    finally:
        # Leave a clean, migrated schema behind for the next test.
        _drop_canonical_schema(engine)
        _run_alembic_upgrade_head()
