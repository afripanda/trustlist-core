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

"""Alembic migration environment for the TrustList canonical store.

The canonical models and migrations land in Stage 0 issue 12; this file is the
wiring only. The database URL is read from the TRUSTLIST_DB_URL environment
variable so that no credentials live in the repository (Stage 0 PRD §7g).
"""

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

_db_url = os.environ.get("TRUSTLIST_DB_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url)

# Populated in Stage 0 issue 12 once the ORM models exist.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations without a live database connection (emits SQL)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
