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

"""Unit tests for idempotency-key derivation (PRD §7b)."""

from __future__ import annotations

import pytest

from trustlist_event_bus.idempotency import derive_idempotency_key


def test_same_payload_fields_derive_the_same_key() -> None:
    """Two emissions of the same logical event collide on one key."""
    payload = {"domain_id": "d1", "signal_class": "dns", "observed_at": "t1"}
    key_a = derive_idempotency_key(
        event_type="signal.tier-one.example-collector",
        payload=payload,
        key_fields=("domain_id", "signal_class", "observed_at"),
    )
    key_b = derive_idempotency_key(
        event_type="signal.tier-one.example-collector",
        payload=dict(payload),
        key_fields=("domain_id", "signal_class", "observed_at"),
    )
    assert key_a == key_b


def test_key_is_independent_of_key_field_order() -> None:
    """The key depends on the field values, not the order they are listed."""
    payload = {"domain_id": "d1", "signal_class": "dns"}
    key_a = derive_idempotency_key(
        event_type="t",
        payload=payload,
        key_fields=("domain_id", "signal_class"),
    )
    key_b = derive_idempotency_key(
        event_type="t",
        payload=payload,
        key_fields=("signal_class", "domain_id"),
    )
    assert key_a == key_b


def test_key_is_independent_of_unselected_payload_fields() -> None:
    """Fields not named in key_fields do not change the derived key."""
    base = {"domain_id": "d1", "signal_class": "dns"}
    with_extra = {**base, "observed_value": {"resolves": True}}
    key_base = derive_idempotency_key(
        event_type="t", payload=base, key_fields=("domain_id", "signal_class")
    )
    key_extra = derive_idempotency_key(
        event_type="t",
        payload=with_extra,
        key_fields=("domain_id", "signal_class"),
    )
    assert key_base == key_extra


def test_different_values_derive_different_keys() -> None:
    """A change in any keyed field changes the derived key."""
    key_one = derive_idempotency_key(
        event_type="t", payload={"domain_id": "d1"}, key_fields=("domain_id",)
    )
    key_two = derive_idempotency_key(
        event_type="t", payload={"domain_id": "d2"}, key_fields=("domain_id",)
    )
    assert key_one != key_two


def test_event_type_namespaces_the_key() -> None:
    """The same field values under different event types do not collide."""
    payload = {"domain_id": "d1"}
    key_a = derive_idempotency_key(
        event_type="signal.tier-one.example-collector",
        payload=payload,
        key_fields=("domain_id",),
    )
    key_b = derive_idempotency_key(
        event_type="score.update", payload=payload, key_fields=("domain_id",)
    )
    assert key_a != key_b


def test_key_is_a_64_char_hex_sha256() -> None:
    """The derived key is a lowercase 64-character hex SHA-256 digest."""
    key = derive_idempotency_key(
        event_type="t", payload={"domain_id": "d1"}, key_fields=("domain_id",)
    )
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_empty_key_fields_is_rejected() -> None:
    """An event with no idempotency-defining fields cannot be keyed."""
    with pytest.raises(ValueError, match="at least one payload field"):
        derive_idempotency_key(
            event_type="t", payload={"domain_id": "d1"}, key_fields=()
        )


def test_missing_key_field_is_rejected() -> None:
    """Naming a field absent from the payload is a hard error, not a silent miss."""
    with pytest.raises(ValueError, match="not present in the payload"):
        derive_idempotency_key(
            event_type="t",
            payload={"domain_id": "d1"},
            key_fields=("domain_id", "signal_class"),
        )
