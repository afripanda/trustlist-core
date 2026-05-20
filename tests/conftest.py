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

"""Shared pytest fixtures for the data-model integration tests.

These fixtures connect to a *real* Postgres — there are no mocks, per the Stage
0 PRD §7a discipline. The database URL is taken from ``TRUSTLIST_DB_URL``; the
schema is expected to be migrated to head before the suite runs (``cd
data-model && alembic upgrade head``). The integration CI job and the local
docker-compose workflow both satisfy that precondition.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection

# The append-only application role created by migration 0001. Tests SET ROLE to
# it to exercise the withheld UPDATE/DELETE grants.
APP_ROLE = "trustlist_app"

_DEFAULT_DB_URL = "postgresql+psycopg://trustlist:trustlist-dev@localhost:5432/trustlist"


def _database_url() -> str:
    """Return the integration-test database URL."""
    return os.environ.get("TRUSTLIST_DB_URL", _DEFAULT_DB_URL)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """A session-scoped SQLAlchemy engine bound to the integration database."""
    eng = create_engine(_database_url(), future=True)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    """A connection wrapped in a transaction that is rolled back after the test.

    Every integration test runs inside its own transaction; the rollback at
    teardown keeps the suite hermetic without truncating tables between tests.
    A few tests that exercise the application role must ``commit()`` mid-test so
    the row is visible to a second connection — for those the teardown rollback
    is a defensive no-op, hence the ``in_transaction()`` guard.
    """
    conn = engine.connect()
    trans = conn.begin()
    try:
        yield conn
    finally:
        if trans.is_active:
            trans.rollback()
        conn.close()


@pytest.fixture
def app_role_connection(engine: Engine) -> Iterator[Connection]:
    """A connection whose effective role is the append-only application role.

    ``SET LOCAL ROLE`` switches the privilege context to ``trustlist_app`` for
    the duration of the transaction, so privilege checks behave exactly as they
    would for the deployed application. The transaction is rolled back at
    teardown, which also discards the role switch.
    """
    conn = engine.connect()
    trans = conn.begin()
    conn.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
    try:
        yield conn
    finally:
        if trans.is_active:
            trans.rollback()
        conn.close()
