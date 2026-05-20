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

"""Unit tests for the consumer-side deduplication store (PRD §7b)."""

from __future__ import annotations

import pytest

from trustlist_event_bus.consumer import InMemoryDedupStore


def test_unseen_key_is_not_seen() -> None:
    """A key never recorded reports as unseen."""
    store = InMemoryDedupStore()
    assert store.seen("key-1") is False


def test_recorded_key_is_seen() -> None:
    """A recorded key reports as seen — the deduplication signal."""
    store = InMemoryDedupStore()
    store.record("key-1")
    assert store.seen("key-1") is True


def test_distinct_keys_are_tracked_independently() -> None:
    """Recording one key does not mark another as seen."""
    store = InMemoryDedupStore()
    store.record("key-1")
    assert store.seen("key-1") is True
    assert store.seen("key-2") is False


def test_oldest_key_is_evicted_at_capacity() -> None:
    """At capacity the least-recently-used key is evicted."""
    store = InMemoryDedupStore(capacity=2)
    store.record("a")
    store.record("b")
    store.record("c")  # evicts "a"
    assert store.seen("a") is False
    assert store.seen("b") is True
    assert store.seen("c") is True


def test_seen_refreshes_recency_so_a_hot_key_is_not_evicted() -> None:
    """Touching a key via seen() keeps it from being the eviction victim."""
    store = InMemoryDedupStore(capacity=2)
    store.record("a")
    store.record("b")
    store.seen("a")  # "a" is now most-recent
    store.record("c")  # evicts "b", the least-recently-used
    assert store.seen("a") is True
    assert store.seen("b") is False
    assert store.seen("c") is True


def test_capacity_below_one_is_rejected() -> None:
    """A non-positive capacity is a configuration error."""
    with pytest.raises(ValueError, match="at least 1"):
        InMemoryDedupStore(capacity=0)
