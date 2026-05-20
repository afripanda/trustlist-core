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

"""Annotation-propagation integration test — *scaffolded* at Stage 0 (issue 25).

This file is the Stage-0 scaffold for the cross-cutting integration test that
the Architecture Alignment Report flags as a Material refactor recommendation
(item 6, "Annotation propagation integration test"):

    *Subsequent-findings annotation propagation (MATERIAL — cross-cutting).*
    Audit-compute Q8 established that destination-knowledge changes after an
    audit was issued become annotations on past audits rather than re-runs. The
    brand-portal Q5 certification verification flow surfaces these annotations.
    **Verification needed.** Confirm that the annotation propagation chain works
    end-to-end across the architecture: a Trust Council decision updating a
    domain's score should reliably surface as an annotation on every
    certification artefact referencing that domain. This is an integration
    test, not a design change.

**Why a scaffold and not a real test yet.** Stage 0 PRD §8 ("CI discipline")
spells the expectation out exactly:

    *Annotation-propagation integration test exists in CI (the test cases
    relevant to Stage 1+ are scaffolded but assert nothing of substance until
    the signal collectors land).*

None of the machinery the substantive assertions need exists at Stage 0:

- the **signal collectors** that produce annotation-bearing evidence are a
  Stage 1 deliverable (PRD §5, Non-scope);
- the **scoring engine** that writes ``score`` / ``score_history`` and the
  ``rationale_summary`` an annotation would surface in is a Stage 2 deliverable
  (PRD §5; the ``score`` and ``score_history`` tables exist in the Stage 0
  schema but, per PRD §7a, "First write happens at Stage 2");
- the **Trust Council console** whose ratified decisions are the *trigger* of
  the propagation, and its ``trust_council_decision`` table, are gated on
  Foundation incorporation (PRD §5, Non-scope; §7a deferred-tables list);
- the **certification artefact** that the annotation must surface on is a
  brand-side product surface — Stage 5 (PRD §5).

So this file does what issue 25's acceptance criteria ask: it carries the
test-name documenting the property under verification, a reusable fixture
harness, and ``skip`` markers whose reason points at the Stage that lands the
real assertions and at the Alignment Report finding. Later stages remove the
``skip`` and fill in assertions against the then-present score-rationale fields
— the test *names* and the *harness* do not move.

**What runs green now.** ``test_annotation_propagation_harness_is_wired`` is a
real, non-skipped Stage-0 test: it proves the harness itself is sound — the
canonical store is migrated and the ``score`` / ``score_history`` tables that
the future annotation assertions read are present and queryable. That is the
"CI runs the test and reports the skip; this proves the wiring is present"
acceptance criterion (issue 25): the wiring assertion passes, and alongside it
CI reports the deferred cases as skips.

Run with ``pytest -m integration`` and the ``TRUSTLIST_*`` connection variables
set (see :mod:`tests.smoke.conftest` and ``tests/conftest.py``). The harness
reuses the smoke-test fixtures (``engine`` from ``tests/conftest.py``) so the
Stage 1+ author inherits a real, migrated Postgres with no extra wiring.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.integration

# --- deferral anchors --------------------------------------------------------
#
# The reasons below are referenced by every ``pytest.mark.skip`` in this file.
# Centralising them keeps the Stage-pointer and the Alignment-Report reference
# consistent, and gives the Stage 1+ author a single place to read what each
# deferred case is waiting on.

_ALIGNMENT_REPORT_FINDING = (
    "TrustList_Architecture_Alignment_Report.md — 'Subsequent-findings "
    "annotation propagation (MATERIAL — cross-cutting)'; refactor "
    "recommendations summary item 6"
)

_SKIP_NEEDS_COLLECTORS = (
    "Deferred: needs Stage 1 signal collectors to produce annotation-bearing "
    f"evidence rows. Scaffolded per Stage 0 PRD §8 and {_ALIGNMENT_REPORT_FINDING}. "
    "Stage 1 removes this skip and asserts against the collector-written "
    "evidence."
)

_SKIP_NEEDS_SCORING_ENGINE = (
    "Deferred: needs the Stage 2 scoring engine to write `score` / "
    "`score_history` rows (the `score` table exists in the Stage 0 schema but "
    "PRD §7a states the first write happens at Stage 2). Scaffolded per Stage 0 "
    f"PRD §8 and {_ALIGNMENT_REPORT_FINDING}. Stage 2 removes this skip and "
    "asserts the annotation surfaces in `score.rationale_summary`."
)

_SKIP_NEEDS_TRUST_COUNCIL = (
    "Deferred: needs the Trust Council console and the `trust_council_decision` "
    "table — both gated on Foundation incorporation (Stage 0 PRD §5 Non-scope; "
    "§7a deferred-tables list). The ratified Trust Council decision is the "
    "*trigger* of annotation propagation. Scaffolded per Stage 0 PRD §8 and "
    f"{_ALIGNMENT_REPORT_FINDING}."
)

_SKIP_NEEDS_CERTIFICATION = (
    "Deferred: needs the Stage 5 brand-side certification artefact (the "
    "surface the annotation must appear on) and the Stage 4a audit-compute "
    "pipeline that issues the audits an annotation attaches to. Scaffolded per "
    f"Stage 0 PRD §8 and {_ALIGNMENT_REPORT_FINDING}."
)


# --- harness -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AnnotationScenario:
    """The canonical-store anchors a propagation scenario operates over.

    A propagation scenario, once the Stage 1+ machinery exists, is: a domain is
    scored; a certification artefact is issued referencing that domain; a Trust
    Council decision later changes the domain's score; the change must surface
    as an annotation on the certification. This dataclass names the durable
    anchor of that scenario — the domain — so the Stage 1+ author has a fixed,
    deterministic handle to thread through ``evidence`` → ``score`` →
    ``score_history`` → (future) ``trust_council_decision`` → (future)
    certification artefact.

    :ivar domain_id: the synthetic domain the scenario is scored against. Fixed
        per scenario so re-runs are deterministic, mirroring the smoke test's
        ``_FIXED_DOMAIN_ID`` discipline.
    :ivar normalised_url: the ADR-0002-normalised URL for that domain.
    """

    domain_id: str
    normalised_url: str


# A *fixed* synthetic domain id for the annotation-propagation scenario.
# Holding it constant keeps the scaffolded harness deterministic across runs —
# the same discipline the smoke test applies to ``_FIXED_DOMAIN_ID``. The
# trailing ``025`` nods to this being the issue-25 fixture, distinct from the
# issue-22 smoke fixture so the two suites never collide on a domain row.
_FIXED_DOMAIN_ID = "00000000-0000-4000-8000-000000000025"
_FIXED_NORMALISED_URL = f"{_FIXED_DOMAIN_ID}.annotation.trustlist.test"


def _delete_scenario_rows(engine: Engine, domain_id: str) -> None:
    """Remove the annotation scenario's own canonical-store rows.

    Run before and after each test so the suite stays hermetic and re-runnable
    from a known-clean state — the same teardown discipline the smoke test
    applies in ``_delete_smoke_rows``. Deletion cascades by hand in foreign-key
    order: ``score_history`` and ``score`` reference ``domain``; ``evidence``
    references ``provenance`` and ``domain``.

    This uses the privileged migration-owner connection, not the append-only
    ``trustlist_app`` role — cleanup is a test-harness concern, not a production
    code path (the production code paths never DELETE; PRD §7a).
    """
    with engine.begin() as conn:
        provenance_ids = [
            str(row[0])
            for row in conn.execute(
                text("SELECT provenance_id FROM evidence WHERE domain_id = :d"),
                {"d": domain_id},
            ).all()
        ]
        conn.execute(
            text("DELETE FROM score_history WHERE domain_id = :d"),
            {"d": domain_id},
        )
        conn.execute(
            text("DELETE FROM score WHERE domain_id = :d"),
            {"d": domain_id},
        )
        conn.execute(
            text("DELETE FROM evidence WHERE domain_id = :d"),
            {"d": domain_id},
        )
        if provenance_ids:
            conn.execute(
                text("DELETE FROM provenance WHERE provenance_id = ANY(:ids)"),
                {"ids": provenance_ids},
            )
        conn.execute(
            text("DELETE FROM domain WHERE domain_id = :d"),
            {"d": domain_id},
        )


@pytest.fixture
def annotation_scenario(engine: Engine) -> Iterator[AnnotationScenario]:
    """Yield a clean :class:`AnnotationScenario` against the canonical store.

    The Stage-0 scaffold of the propagation fixture. It seeds exactly one
    ``domain`` row — the durable anchor every later step of the scenario hangs
    off — and tears the whole scenario down on both sides so the suite is
    hermetic and re-runnable (PRD §8 criterion 5 reproducibility discipline).

    **For the Stage 1+ author.** This fixture is the reusable harness issue 25
    asks for: "Stage 1 just needs to remove the ``skip`` and add assertions
    against the (then-present) score-rationale fields." When the scoring engine
    and Trust Council machinery land, extend *this* fixture — not the test
    bodies — to additionally seed the ``evidence`` rows (Stage 1 collectors),
    the ``score`` / ``score_history`` rows (Stage 2 engine) and the
    ``trust_council_decision`` row (post-incorporation). The test functions
    below then assert over the richer scenario without changing their names or
    their place in CI.
    """
    _delete_scenario_rows(engine, _FIXED_DOMAIN_ID)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO domain (domain_id, normalised_url, current_status) "
                "VALUES (:id, :url, 'under_review') "
                "ON CONFLICT (domain_id) DO NOTHING"
            ),
            {"id": _FIXED_DOMAIN_ID, "url": _FIXED_NORMALISED_URL},
        )
    try:
        yield AnnotationScenario(
            domain_id=_FIXED_DOMAIN_ID,
            normalised_url=_FIXED_NORMALISED_URL,
        )
    finally:
        _delete_scenario_rows(engine, _FIXED_DOMAIN_ID)


# --- the wiring test (runs green at Stage 0) ---------------------------------


def test_annotation_propagation_harness_is_wired(
    annotation_scenario: AnnotationScenario,
    engine: Engine,
) -> None:
    """The annotation-propagation harness is wired into CI and the schema is ready.

    This is the one *substantive* Stage-0 assertion in this file, and it is the
    "CI runs the test and reports the skip; this proves the wiring is present"
    acceptance criterion of issue 25 made concrete. It asserts only what Stage 0
    supports:

    1. the ``annotation_scenario`` fixture really seeds its domain anchor
       against a live, migrated canonical store (the fixture ran, so the
       harness is sound);
    2. the ``score`` and ``score_history`` tables — the tables a future
       annotation will surface in via ``rationale_summary`` — exist in the
       migrated schema and are queryable. (At Stage 0 they are empty: PRD §7a
       says the first ``score`` write happens at Stage 2.)

    It deliberately asserts *nothing* about annotation behaviour: that property
    has no implementation at Stage 0. The deferred cases below carry the named
    behaviour; this test carries the proof that the rest of the file is wired
    into the ``integration-tests`` CI job and will run the moment the skips come
    off.
    """
    with engine.connect() as conn:
        # The fixture seeded its anchor — the harness reached a real store.
        domain_count = conn.execute(
            text("SELECT count(*) FROM domain WHERE domain_id = :d"),
            {"d": annotation_scenario.domain_id},
        ).scalar()
        assert domain_count == 1, (
            "the annotation_scenario fixture must seed exactly one domain "
            "anchor row against the migrated canonical store"
        )

        # The score-side tables the future annotation assertions read exist and
        # are queryable. count(*) over an empty table proves the table is
        # present without asserting any Stage 1+ behaviour.
        score_count = conn.execute(
            text("SELECT count(*) FROM score WHERE domain_id = :d"),
            {"d": annotation_scenario.domain_id},
        ).scalar()
        score_history_count = conn.execute(
            text("SELECT count(*) FROM score_history WHERE domain_id = :d"),
            {"d": annotation_scenario.domain_id},
        ).scalar()

    # Stage 0: no scoring engine has written, so both are empty. The assertion
    # is that the *tables* answer the query — the Stage 2 author flips these to
    # the real annotation checks.
    assert score_count == 0, (
        "Stage 0 has no scoring engine; `score` must be empty for the "
        "scenario domain (PRD §7a: first score write is Stage 2)"
    )
    assert score_history_count == 0, (
        "Stage 0 has no scoring engine; `score_history` must be empty for "
        "the scenario domain (PRD §7a: first write is Stage 2)"
    )


# --- the deferred cases (scaffolded; skipped until Stage 1+) -----------------
#
# Each function below names one link in the annotation-propagation chain the
# Alignment Report asks to be verified end-to-end. They are skipped, not
# deleted, so CI *reports* them every run — the standing reminder that keeps the
# Alignment Report's "we forgot to wire that test" failure mode from recurring
# (issue 25 Notes). Each docstring states precisely what the test WILL assert
# once its blocking Stage lands; the body is a placeholder.


@pytest.mark.skip(reason=_SKIP_NEEDS_COLLECTORS)
def test_annotation_bearing_evidence_is_recorded(
    annotation_scenario: AnnotationScenario,
) -> None:
    """Annotation-bearing evidence from a Stage 1 collector persists with its provenance.

    WILL ASSERT (Stage 1): a Stage 1 signal collector that observes a
    subsequent finding about an already-scored domain writes an ``evidence``
    row whose ``observed_value`` carries the annotation, referencing one
    ``provenance`` row (the §7a evidence/provenance invariant). This is the
    first link of the chain — there is no annotation to propagate until a
    collector produces one.

    Deferred at Stage 0: no signal collectors exist (PRD §5 Non-scope — Stage
    1). The ``annotation_scenario`` fixture already provides the migrated store
    and the domain anchor this case will write the evidence against.
    """
    pytest.fail("scaffold: implement when Stage 1 signal collectors land")


@pytest.mark.skip(reason=_SKIP_NEEDS_SCORING_ENGINE)
def test_score_rationale_carries_evidence_annotation_at_scoring_time(
    annotation_scenario: AnnotationScenario,
) -> None:
    """Evidence-row annotation metadata propagates to score rationale at re-scoring.

    The function name and this docstring carry the exact property issue 25's
    acceptance criteria name: that "annotation metadata on an evidence row
    propagates to derived score rationale at re-scoring time."

    WILL ASSERT (Stage 2): given an ``evidence`` row carrying an annotation
    (seeded via the Stage 1 collector path), when the Stage 2 scoring engine
    re-scores the domain, the resulting ``score`` row's ``rationale_summary``
    (and the matching ``score_history`` row) reflects that annotation — the
    annotation has propagated from raw evidence into the derived,
    customer-visible score rationale.

    Deferred at Stage 0: the scoring engine does not exist and the ``score`` /
    ``score_history`` tables are empty (PRD §5 Non-scope — Stage 2; PRD §7a
    "First write happens at Stage 2"). The harness — fixture, domain anchor,
    teardown of the score-side tables — is in place; Stage 2 removes this skip
    and asserts against ``score.rationale_summary``.
    """
    pytest.fail("scaffold: implement when the Stage 2 scoring engine lands")


@pytest.mark.skip(reason=_SKIP_NEEDS_TRUST_COUNCIL)
def test_trust_council_decision_triggers_score_update(
    annotation_scenario: AnnotationScenario,
) -> None:
    """A ratified Trust Council decision re-scores the referenced domain.

    WILL ASSERT (post-Foundation-incorporation): a ratified Trust Council
    decision — published to the ``trust-council.decision-ratified`` topic
    (PRD §7b) and recorded in the ``trust_council_decision`` table — that
    changes a domain's score causes a new ``score`` / ``score_history`` row,
    and that row's ``rationale_summary`` records the decision as the cause. The
    Trust Council decision is the *trigger* the Alignment Report names: the
    chain starts here whenever destination knowledge changes after an audit was
    issued.

    Deferred at Stage 0: the Trust Council console and the
    ``trust_council_decision`` table are gated on Foundation incorporation
    (PRD §5 Non-scope; §7a deferred-tables list).
    """
    pytest.fail("scaffold: implement when the Trust Council console lands")


@pytest.mark.skip(reason=_SKIP_NEEDS_CERTIFICATION)
def test_decision_surfaces_as_annotation_on_referencing_certification(
    annotation_scenario: AnnotationScenario,
) -> None:
    """A Trust Council decision surfaces as an annotation on every referencing certification.

    WILL ASSERT (Stage 4a / Stage 5) — the headline end-to-end property of the
    Alignment Report's Material refactor item 6: a Trust Council decision
    updating a domain's score reliably surfaces as an annotation on *every*
    certification artefact that references that domain. Concretely: seed two
    certification artefacts referencing the scenario domain; ratify a Trust
    Council decision that changes the domain's score; assert both certifications
    carry the resulting subsequent-findings annotation (audit-compute Q8 turns
    post-issue knowledge changes into annotations on past audits rather than
    re-runs; brand-portal Q5's verification flow surfaces them).

    Deferred at Stage 0: the certification artefact is a Stage 5 brand-side
    surface and the audit-compute pipeline that issues audits is Stage 4a
    (PRD §5 Non-scope). This is the last link of the chain; it composes every
    earlier deferred case above.
    """
    pytest.fail(
        "scaffold: implement when the Stage 4a audit-compute pipeline and the "
        "Stage 5 certification artefact land"
    )


# A module-level uuid import guard: the scaffold references `uuid` in its
# docstrings as the discipline a Stage 1+ author follows for any *additional*
# synthetic rows (one fresh uuid per non-anchor row). Keeping the import here
# documents that expectation and is exercised by the helper below so the import
# is not dead at Stage 0.


def _fresh_row_id() -> str:
    """Return a fresh UUID for a non-anchor scenario row.

    The scenario's *domain* anchor is fixed (``_FIXED_DOMAIN_ID``) for
    determinism, but any additional rows a Stage 1+ author seeds — extra
    evidence, extra certifications — should each get a fresh id so they never
    collide across a re-run. This helper hands the Stage 1+ author that
    convention; it is intentionally unused by the Stage-0 wiring test, whose
    only row is the fixed anchor.
    """
    return str(uuid.uuid4())
