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

"""Stage 0 reproducibility test — the fixture-replay (PRD §8 criterion 5).

The Implementation Planning grilling treats reproducibility as a foundational
Stage-0 operating-model discipline: *agent-driven implementation produces
production-quality code, verified at the Stage-0 outcome*. PRD §8 acceptance
criterion 5 makes that concrete:

    *Given a fixture input (a stub signal event with a fixed payload and
    provenance), the canonical store contains exactly the expected rows after
    processing. Re-running the test against a freshly-migrated database
    produces identical results.*

This module is that test. It extends the issue-22 smoke-test harness
(:mod:`tests.smoke`) — it imports the smoke test's *fixed* synthetic signal
(the constant payload, provenance and derived idempotency key) and its
producer helper, so the two tests exercise the very same fixture and can never
drift. What the reproducibility test adds beyond the smoke test:

1. **Exact-rows assertion.** After processing the fixture signal it captures a
   :class:`~tests.reproducibility.snapshot.CanonicalStoreSnapshot` of the
   resulting ``domain`` / ``provenance`` / ``evidence`` rows and asserts the
   *stable* part — every column whose value the fixture fully determines —
   equals a checked-in expected snapshot (``expected_snapshot.json``), to the
   byte. The smoke test asserts individual fields; this asserts the *whole*
   row set is exactly as expected, with no extra and no missing rows.

2. **Freshly-migrated re-run.** It then drops the canonical schema, re-applies
   the Alembic migrations (the ``fresh_migrate`` fixture), replays the same
   fixture signal, captures a second snapshot and asserts the two stable
   snapshots are *byte-identical*. The only fields permitted to differ are the
   unavoidably-varying ones — the server-generated surrogate UUIDs and the
   ``now()`` timestamps — and the test asserts those are *present and
   well-formed* on each run and are the *only* difference.

"Byte-identical modulo unavoidably-varying fields" is made precise by the
stable/volatile split in :mod:`tests.reproducibility.snapshot`; see that
module's docstring.

Run with ``pytest -m integration`` and the three ``TRUSTLIST_*`` connection
variables set. The CI ``reproducibility`` job (``.github/workflows/ci.yml``)
stands up the Postgres and RedPanda services, applies the migrations and runs
``pytest tests/test_reproducibility.py``, which collects this package.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

from tests.reproducibility.snapshot import (
    CanonicalStoreSnapshot,
    capture_snapshot,
    normalise_for_expected,
)
from tests.smoke.evidence_writer import EvidenceWriter
from tests.smoke.test_smoke_roundtrip import (
    _FIXED_DOMAIN_ID,
    _produce_synthetic_signal,
)
from trustlist_event_bus import EventConsumer, ensure_topic
from trustlist_event_bus.config import EventBusConfig
from trustlist_event_bus.schema_registry import SchemaRegistry

pytestmark = pytest.mark.integration

# The checked-in expected snapshot — the canonical, reviewed record of exactly
# which stable rows the fixture signal must produce. Committed alongside this
# test; regenerated only by a deliberate, reviewed change (see the module's
# ``--update-expected`` note below).
_EXPECTED_SNAPSHOT_PATH = Path(__file__).resolve().parent / "expected_snapshot.json"


def _delete_fixture_rows(engine: Engine) -> None:
    """Remove the reproducibility fixture's rows from the canonical store.

    Keyed on the fixed synthetic ``domain_id``; cascades by hand through
    ``evidence`` → ``provenance`` → ``domain`` in foreign-key order. Uses the
    privileged migration-owner connection — cleanup is a harness concern, not a
    production code path. Mirrors the smoke test's ``_delete_smoke_rows``.
    """
    with engine.begin() as conn:
        provenance_ids = [
            str(row[0])
            for row in conn.execute(
                text("SELECT provenance_id FROM evidence WHERE domain_id = :d"),
                {"d": _FIXED_DOMAIN_ID},
            ).all()
        ]
        conn.execute(
            text("DELETE FROM evidence WHERE domain_id = :d"),
            {"d": _FIXED_DOMAIN_ID},
        )
        if provenance_ids:
            conn.execute(
                text("DELETE FROM provenance WHERE provenance_id = ANY(:ids)"),
                {"ids": provenance_ids},
            )
        conn.execute(
            text("DELETE FROM domain WHERE domain_id = :d"),
            {"d": _FIXED_DOMAIN_ID},
        )


@pytest.fixture
def clean_canonical_store(engine: Engine) -> Iterator[Engine]:
    """Yield the canonical-store engine with the fixture's rows removed.

    Gives each run a known-clean starting point and keeps the suite hermetic.
    The ``fresh_migrate``-driven test does not use this fixture — a freshly
    migrated schema is already empty — but the first, single-database test
    does.
    """
    _delete_fixture_rows(engine)
    try:
        yield engine
    finally:
        _delete_fixture_rows(engine)


def _process_fixture_signal(
    engine: Engine,
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
) -> CanonicalStoreSnapshot:
    """Process the fixture signal end-to-end and snapshot the resulting rows.

    Runs the full Stage-0 pipeline for one fixture signal — produce the fixed
    synthetic event with the SDK, consume it with the evidence-writer stub,
    persist the ``provenance`` + ``evidence`` pair — then captures a
    :class:`CanonicalStoreSnapshot` of the canonical store for the fixture
    domain.

    A fresh, per-call topic isolates each run's partition log; idempotency is a
    canonical-store property (the writer deduplicates on the stable key), so a
    fresh topic does not weaken the reproducibility guarantee.
    """
    topic = f"signal.tier-one.example-collector.repro-{uuid.uuid4().hex[:12]}"
    ensure_topic(topic, config=event_bus_config)

    # --- produce -----------------------------------------------------------
    _produce_synthetic_signal(topic, event_bus_config, schema_registry)

    # --- consume + write ---------------------------------------------------
    writer = EvidenceWriter(engine)
    with EventConsumer(
        f"repro-evidence-writer-{uuid.uuid4().hex[:8]}",
        config=event_bus_config,
        schema_registry=schema_registry,
    ) as consumer:
        consumer.subscribe([topic])
        processed = consumer.run(writer, poll_timeout=2.0, max_events=1)

    assert processed == 1, "the evidence-writer should consume exactly one event"
    assert len(writer.results) == 1
    assert writer.results[0].created is True, "the fixture run must insert rows"

    return capture_snapshot(engine, _FIXED_DOMAIN_ID)


def test_fixture_signal_yields_exactly_the_expected_rows(
    clean_canonical_store: Engine,
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
) -> None:
    """The fixture signal produces exactly the checked-in expected rows.

    PRD §8 criterion 5, first half: "the canonical store contains exactly the
    expected rows after processing". The fixture signal is processed through
    the full pipeline; the *stable* part of the resulting snapshot — every
    column the fixture fully determines, across the ``domain``, ``provenance``
    and ``evidence`` rows — is asserted equal to ``expected_snapshot.json``.

    The expected file is a reviewed artefact: a change to it is a change to the
    canonical store's deterministic output and must be deliberate. A mismatch
    here means either the data model, the evidence-writer or the fixture
    changed in a way that altered the produced rows.
    """
    engine = clean_canonical_store
    snapshot = _process_fixture_signal(engine, event_bus_config, schema_registry)

    captured = normalise_for_expected(snapshot.stable)
    expected = json.loads(_EXPECTED_SNAPSHOT_PATH.read_text(encoding="utf-8"))

    assert captured == expected, (
        "the fixture signal did not yield the checked-in expected rows; the "
        "canonical store's deterministic output has changed. If the change is "
        "intentional, regenerate tests/reproducibility/expected_snapshot.json "
        "and review the diff."
    )

    # The volatile fields — surrogate UUIDs and now() stamps — must still have
    # been produced; a null primary key or timestamp is a regression even when
    # the stable part matches.
    assert snapshot.volatile_columns_present(), (
        "a volatile column (a surrogate UUID or a now() timestamp) was null; "
        "the canonical store did not populate it."
    )

    # Exactly one domain, one provenance and one evidence row — no more.
    assert len(snapshot.stable["domain"]) == 1
    assert len(snapshot.stable["provenance"]) == 1
    assert len(snapshot.stable["evidence"]) == 1


def test_rerun_against_freshly_migrated_database_is_byte_identical(
    fresh_migrate: object,
    event_bus_config: EventBusConfig,
    schema_registry: SchemaRegistry,
) -> None:
    """Re-running against a freshly-migrated database yields identical rows.

    PRD §8 criterion 5, second half: "re-running the test against a
    freshly-migrated database produces identical results". The fixture signal
    is processed twice, each time against a *separately, freshly-migrated*
    canonical store (the schema is dropped and ``alembic upgrade head`` re-run
    between the two), and the two snapshots are compared:

    - the **stable** parts must be *byte-identical* — equal canonical-JSON
      serialisations. This is the operational meaning of "identical results";
    - the **volatile** parts — the surrogate UUIDs and the ``now()`` stamps —
      are the "unavoidably-varying fields" the criterion exempts. The test
      asserts they are present and well-formed on each run, and asserts they
      are the *only* difference: a run that differed in any *stable* column
      would fail the byte-identical check.
    """
    fresh = fresh_migrate  # the callable yielded by the fixture
    assert callable(fresh)

    # --- run one: against a freshly-migrated, empty schema -----------------
    engine = fresh()
    first = _process_fixture_signal(engine, event_bus_config, schema_registry)

    # --- run two: against a *second* freshly-migrated, empty schema --------
    engine = fresh()
    second = _process_fixture_signal(engine, event_bus_config, schema_registry)

    # The headline assertion: the stable parts are byte-identical.
    assert first.stable_bytes() == second.stable_bytes(), (
        "two runs of the fixture signal against freshly-migrated databases "
        "produced different stable rows; the canonical store is not "
        "reproducible. Diff the stable snapshots to find the offending column."
    )

    # Both runs must have produced their volatile fields.
    assert first.volatile_columns_present()
    assert second.volatile_columns_present()

    # The volatile fields are the *only* difference: the surrogate UUIDs are
    # freshly generated each run (gen_random_uuid()), so they must differ;
    # demonstrating they differ confirms the byte-identical stable check above
    # is not accidentally comparing two identical-by-luck runs.
    first_evidence_id = first.volatile["evidence"][0]["evidence_id"]
    second_evidence_id = second.volatile["evidence"][0]["evidence_id"]
    assert first_evidence_id != second_evidence_id, (
        "the surrogate evidence_id should be freshly generated on each run; "
        "two runs produced the same UUID, which means the schema was not "
        "actually re-migrated between runs."
    )

    # And the freshly-migrated re-run still matches the checked-in expected
    # snapshot — reproducibility and correctness in one assertion.
    expected = json.loads(_EXPECTED_SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert normalise_for_expected(second.stable) == expected, (
        "the freshly-migrated re-run did not match the expected snapshot."
    )
