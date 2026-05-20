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

"""The Stage 0 end-to-end smoke test (PRD §8 acceptance criterion 2).

This package holds the headline Stage 0 milestone: a runnable, demoable
pipeline that proves a synthetic tier-one signal flows end-to-end through the
foundation — event-bus producer to canonical store.

Contents:

- :mod:`tests.smoke.evidence_writer` — the evidence-writer service *stub*: a
  small event-bus consumer that reads a tier-one signal event off the bus and
  writes the originating ``provenance`` row and the referencing ``evidence``
  row into the canonical store, in one transaction, idempotently. The real
  evidence-writer service is a later-stage deliverable; this stub is the
  Stage-0-scoped consumer the §8.2 round-trip exercises.
- :mod:`tests.smoke.test_smoke_roundtrip` — the integration test harness that
  produces a synthetic signal, runs the stub against a real RedPanda and a
  real migrated Postgres, asserts the canonical-store rows match the synthetic
  payload, asserts idempotency on a re-run, and asserts W3C trace context
  propagates from the producer through the bus to the consumer.

The smoke test underwrites the reproducibility test (issue 24) and the
annotation-propagation scaffolded test (issue 25), both of which extend this
same harness.
"""
