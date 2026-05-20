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

"""Reproducibility test — placeholder.

The real reproducibility test (Stage 0 issue 24) re-runs a fixture signal
through the canonical store and asserts byte-identical results. Until the data
model and event-bus SDK land, this placeholder keeps the dedicated CI step
wired and green.
"""


def test_reproducibility_placeholder() -> None:
    """Placeholder — replaced by the real fixture replay in Stage 0 issue 24."""
    assert 1 + 1 == 2
