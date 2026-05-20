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

"""The canonical-store snapshot for the reproducibility test (PRD §8.5).

PRD §8 acceptance criterion 5 requires that processing a fixture signal yields
*exactly* the expected rows, and that a re-run against a freshly-migrated
database yields *byte-identical* results. "Byte-identical" cannot mean every
column literally — two rows of a UUID-keyed, ``now()``-stamped table are never
byte-identical across runs. The criterion's own wording resolves this: re-runs
must be "identical ... modulo unavoidably-varying fields like timestamps".

This module makes that precise. A :class:`CanonicalStoreSnapshot` captures the
``domain``, ``provenance`` and ``evidence`` rows a fixture run produced and
splits every column into one of two buckets:

- **stable** — the column's value is fully determined by the fixture input.
  ``evidence.signal_class``, ``provenance.source``, ``domain.normalised_url``
  and the like. These must be *byte-identical* across runs; the reproducibility
  assertion compares them with ``==`` on a canonical JSON serialisation.
- **volatile** — the column's value is *not* determined by the fixture: the
  server-generated surrogate UUIDs (``gen_random_uuid()``) and the
  wall-clock ``created_at`` / ``recorded_at`` stamps (``now()``). These are the
  "unavoidably-varying fields" the criterion exempts. The snapshot records
  *which* columns they are and *that they are present and well-formed*, but
  their literal values are not part of the byte-identical comparison.

The split is declared once, per table, in :data:`_VOLATILE_COLUMNS`. Every
other selected column is stable by construction — a column the test forgets to
classify defaults to stable, so a newly-added non-deterministic column would
*fail* the reproducibility assertion rather than be silently tolerated. That
fail-loud default is deliberate.

Cross-row foreign keys (``evidence.domain_id`` → ``domain.domain_id``,
``evidence.provenance_id`` → ``provenance.provenance_id``) are themselves
volatile UUIDs, but their *referential structure* is stable: the evidence row
must point at the one domain row and the one provenance row in the snapshot.
The snapshot replaces each such UUID with a stable symbolic token
(``<domain:0>``, ``<provenance:0>``) so the referential shape is captured in
the stable part while the raw UUID stays out of it. This is what lets the
byte-identical comparison see "the evidence row references the snapshot's
domain and provenance rows" without seeing the run-specific UUID.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Engine, text

# The fixed synthetic ``domain_id`` of the smoke/reproducibility fixture. The
# snapshot is scoped to this domain so a run reads back exactly its own rows and
# nothing else in the canonical store. Imported lazily by the test module; kept
# here as the documented contract value.
#
# Volatile columns, declared per table. Anything *not* listed is treated as
# stable and therefore part of the byte-identical comparison. A column is
# volatile when its value is server-generated and run-specific:
#
# - the surrogate primary keys are ``gen_random_uuid()`` defaults;
# - ``created_at`` / ``recorded_at`` are ``now()`` defaults — wall-clock stamps;
# - the cross-row foreign keys hold those run-specific UUIDs.
#
# ``provenance.observed_at`` and ``evidence.observed_at`` are *not* volatile:
# they carry the fixture's fixed ``observed_at`` from the payload, so they are
# fully determined by the input and must be byte-identical across runs.
_VOLATILE_COLUMNS: dict[str, frozenset[str]] = {
    "domain": frozenset({"domain_id", "created_at", "updated_at"}),
    "provenance": frozenset({"provenance_id", "created_at"}),
    "evidence": frozenset(
        {"evidence_id", "provenance_id", "domain_id", "recorded_at"}
    ),
}

# The columns selected per table, in a fixed order. Explicit and ordered so the
# snapshot is deterministic regardless of the database's column ordering.
_SELECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "domain": (
        "domain_id",
        "normalised_url",
        "current_status",
        "current_score",
        "score_version",
        "last_scored_at",
        "created_at",
        "updated_at",
    ),
    "provenance": (
        "provenance_id",
        "source",
        "method",
        "observed_at",
        "contributor_id",
        "contributor_identity",
        "created_at",
    ),
    "evidence": (
        "evidence_id",
        "domain_id",
        "signal_class",
        "source",
        "method",
        "source_url",
        "observed_at",
        "recorded_at",
        "contributor_identity",
        "observed_value",
        "provenance_id",
    ),
}

# The foreign-key columns whose run-specific UUID is rewritten to a stable
# symbolic reference token. Keyed by ``(table, column)`` → the referenced
# table; the snapshot looks the UUID up among that table's captured rows and
# substitutes ``<reftable:index>``.
_REFERENCE_COLUMNS: dict[tuple[str, str], str] = {
    ("evidence", "domain_id"): "domain",
    ("evidence", "provenance_id"): "provenance",
}


def _json_default(value: Any) -> str:
    """Render a non-JSON-native value as a deterministic string.

    ``datetime`` columns reach the snapshot as aware :class:`~datetime.datetime`
    instances; their ISO-8601 form is deterministic for a fixed instant. Any
    other exotic type (a ``Decimal``, say) falls back to ``str`` — also
    deterministic. This keeps the canonical JSON serialisation total.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _canonical_json(obj: Any) -> str:
    """Serialise ``obj`` to canonical JSON — sorted keys, no incidental spacing.

    Two snapshots are "byte-identical" exactly when this serialisation of their
    stable parts is equal. ``sort_keys`` removes dict-ordering as a variable;
    the compact separators remove whitespace as one.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), default=_json_default
    )


@dataclass(frozen=True, slots=True)
class CanonicalStoreSnapshot:
    """A deterministic capture of one fixture run's canonical-store rows.

    :ivar stable: the part of the snapshot fully determined by the fixture
        input — every selected column except the volatile ones, with
        cross-row foreign keys rewritten to symbolic reference tokens. This is
        what the reproducibility assertion compares byte-for-byte.
    :ivar volatile: the unavoidably-varying part — the surrogate UUIDs and the
        ``now()`` timestamps. Recorded so the test can assert these columns are
        *present and well-formed* on every run, and assert they are the *only*
        thing that differs between two runs.
    """

    stable: dict[str, list[dict[str, Any]]]
    volatile: dict[str, list[dict[str, Any]]]

    def stable_bytes(self) -> str:
        """Return the canonical-JSON serialisation of the stable part.

        Equality of this string between two runs is the operational definition
        of "byte-identical results" in PRD §8 criterion 5.
        """
        return _canonical_json(self.stable)

    def volatile_columns_present(self) -> bool:
        """Return ``True`` when every volatile column is present and non-null.

        The reproducibility test asserts not only that the *stable* part
        matches but that the volatile fields — the surrogate keys and the
        ``now()`` stamps — were actually produced. A run that left a primary
        key or a ``created_at`` null is a regression even though the stable
        part would still match.
        """
        for table_rows in self.volatile.values():
            for row in table_rows:
                if any(value is None for value in row.values()):
                    return False
        return True


def _row_to_dict(columns: tuple[str, ...], row: Any) -> dict[str, Any]:
    """Map a SQLAlchemy result row onto an ordered ``column -> value`` dict."""
    return {column: getattr(row, column) for column in columns}


def _resolve_reference(
    table: str,
    column: str,
    value: Any,
    captured: dict[str, list[dict[str, Any]]],
) -> str:
    """Rewrite a foreign-key UUID to a stable ``<reftable:index>`` token.

    The raw UUID is run-specific, but *which* captured row it points at is not.
    Looking the UUID up among the referenced table's captured primary keys and
    substituting its index turns a volatile value into a stable structural
    fact: "this evidence row references domain row 0".

    :raises AssertionError: when the foreign key points at no captured row —
        a dangling reference, which would mean the snapshot is incomplete.
    """
    referenced_table = _REFERENCE_COLUMNS[(table, column)]
    pk_column = f"{referenced_table.rstrip('s')}_id"
    if referenced_table == "domain":
        pk_column = "domain_id"
    elif referenced_table == "provenance":
        pk_column = "provenance_id"
    for index, ref_row in enumerate(captured[referenced_table]):
        if str(ref_row[pk_column]) == str(value):
            return f"<{referenced_table}:{index}>"
    raise AssertionError(
        f"{table}.{column} = {value!r} references no captured "
        f"{referenced_table} row — the snapshot is incomplete."
    )


def capture_snapshot(engine: Engine, domain_id: str) -> CanonicalStoreSnapshot:
    """Capture the canonical-store rows for ``domain_id`` as a snapshot.

    Reads the ``domain`` row, every ``evidence`` row for that domain and every
    ``provenance`` row those evidence rows reference, then splits each selected
    column into the stable and volatile buckets per :data:`_VOLATILE_COLUMNS`.

    Rows are read in a deterministic order (``domain`` by its single key,
    ``evidence`` and ``provenance`` by ``observed_at`` then the surrogate key)
    so the snapshot does not depend on the database's physical row order.

    :param engine: a SQLAlchemy engine bound to the migrated canonical store.
    :param domain_id: the fixture's fixed synthetic ``domain_id``.
    :returns: the :class:`CanonicalStoreSnapshot` for that domain's rows.
    """
    with engine.connect() as conn:
        domain_rows = conn.execute(
            text(
                "SELECT * FROM domain WHERE domain_id = :d "
                "ORDER BY domain_id"
            ),
            {"d": domain_id},
        ).all()
        evidence_rows = conn.execute(
            text(
                "SELECT * FROM evidence WHERE domain_id = :d "
                "ORDER BY observed_at, signal_class, source_url, evidence_id"
            ),
            {"d": domain_id},
        ).all()
        provenance_rows = conn.execute(
            text(
                "SELECT p.* FROM provenance p "
                "JOIN evidence e ON e.provenance_id = p.provenance_id "
                "WHERE e.domain_id = :d "
                "ORDER BY p.observed_at, p.method, p.provenance_id"
            ),
            {"d": domain_id},
        ).all()

    raw: dict[str, list[dict[str, Any]]] = {
        "domain": [_row_to_dict(_SELECTED_COLUMNS["domain"], r) for r in domain_rows],
        "provenance": [
            _row_to_dict(_SELECTED_COLUMNS["provenance"], r) for r in provenance_rows
        ],
        "evidence": [
            _row_to_dict(_SELECTED_COLUMNS["evidence"], r) for r in evidence_rows
        ],
    }

    stable: dict[str, list[dict[str, Any]]] = {}
    volatile: dict[str, list[dict[str, Any]]] = {}
    for table, rows in raw.items():
        stable_rows: list[dict[str, Any]] = []
        volatile_rows: list[dict[str, Any]] = []
        volatile_cols = _VOLATILE_COLUMNS.get(table, frozenset())
        for row in rows:
            stable_row: dict[str, Any] = {}
            volatile_row: dict[str, Any] = {}
            for column, value in row.items():
                if column in volatile_cols:
                    if (table, column) in _REFERENCE_COLUMNS:
                        # A volatile FK: its raw UUID is volatile, but its
                        # symbolic reference token is stable structure.
                        stable_row[column] = _resolve_reference(
                            table, column, value, raw
                        )
                    volatile_row[column] = value
                else:
                    stable_row[column] = value
            stable_rows.append(stable_row)
            volatile_rows.append(volatile_row)
        stable[table] = stable_rows
        volatile[table] = volatile_rows

    return CanonicalStoreSnapshot(stable=stable, volatile=volatile)


def normalise_for_expected(stable: dict[str, list[dict[str, Any]]]) -> Any:
    """Round-trip the stable part through canonical JSON to a plain structure.

    The checked-in expected snapshot (``expected_snapshot.json``) is plain
    JSON; a freshly-captured snapshot holds Python ``datetime`` objects. This
    round-trip renders the captured stable part into the same plain
    JSON-native structure the expected file holds, so the two compare equal
    with ``==``.
    """
    return json.loads(_canonical_json(stable))
