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

"""The Stage 0 reproducibility test (PRD §8 acceptance criterion 5).

This package holds the reproducibility test — the Stage-0 operating-model
discipline the Implementation Planning grilling identifies (PRD §8 criterion 5;
issue 24). It extends the issue-22 smoke-test harness (:mod:`tests.smoke`) to
prove a stronger property than the smoke test's idempotency check:

    *Given a fixture signal event with a fixed payload and provenance,
    processing it through the canonical store yields exactly one expected set
    of rows; and re-running the same fixture against a freshly-migrated
    database yields byte-identical rows.*

Contents:

- :mod:`tests.reproducibility.snapshot` — the canonical-store *snapshot*: the
  deterministic, JSON-serialisable capture of the ``domain`` / ``provenance`` /
  ``evidence`` rows a fixture run produces, split into a *stable* part (every
  column whose value is fully determined by the fixture) and a *volatile* part
  (the unavoidably-varying fields — surrogate UUIDs and wall-clock timestamps).
- :mod:`tests.reproducibility.test_reproducibility` — the integration test
  harness that processes the fixture signal through the full producer → bus →
  evidence-writer → canonical-store path, asserts the stable snapshot equals a
  checked-in expected snapshot, then re-runs against a freshly-migrated
  database and asserts the two stable snapshots are byte-identical (and that
  the volatile fields are the *only* differences).

The reproducibility test reuses the smoke test's fixture signal — the fixed
synthetic payload, provenance and idempotency key are imported from
:mod:`tests.smoke.test_smoke_roundtrip`, so the two tests can never drift.
"""
