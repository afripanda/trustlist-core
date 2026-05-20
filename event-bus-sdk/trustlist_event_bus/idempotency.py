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

"""Idempotency-key derivation (Stage 0 PRD §7b).

The event bus is at-least-once: a producer may emit the same logical event
more than once, and the broker may redeliver. PRD §7b requires the
``idempotency_key`` to be *derived from payload-specific fields* so that two
emissions of the same logical observation collide on the same key, and a
consumer can deduplicate on it.

:func:`derive_idempotency_key` builds that key as a SHA-256 over a canonical
JSON rendering of the chosen fields. Canonicalisation — sorted keys, no
incidental whitespace — is what makes the key *stable*: two dicts that are
equal as data hash identically regardless of construction order.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any


def _canonical_json(value: Any) -> str:
    """Render ``value`` as canonical JSON — sorted keys, minimal separators.

    Sorting keys recursively and stripping incidental whitespace means the
    rendering depends only on the *data*, never on dict insertion order or
    formatting, so the derived key is reproducible.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def derive_idempotency_key(
    *,
    event_type: str,
    payload: dict[str, Any],
    key_fields: Sequence[str],
) -> str:
    """Derive a stable idempotency key from payload-specific fields.

    The key is the hex SHA-256 of a canonical JSON document containing the
    ``event_type`` and the selected payload fields. Including ``event_type``
    namespaces the key, so the same field values under two different event
    types do not collide.

    :param event_type: the event type; namespaces the derived key.
    :param payload: the event payload.
    :param key_fields: the payload keys whose values identify the logical
        event — for a tier-one signal, typically ``("domain_id",
        "signal_class", "observed_at")``. Order does not matter; the values
        are gathered into a sorted-key document.
    :returns: a 64-character lowercase hex SHA-256 digest.
    :raises ValueError: when ``key_fields`` is empty (an unkeyed event could
        never be deduplicated) or names a field absent from ``payload`` (a
        silent miss would produce a key that does not identify the event).
    """
    if not key_fields:
        raise ValueError(
            "key_fields must name at least one payload field; an event with "
            "no idempotency-defining fields cannot be deduplicated (PRD §7b)."
        )
    missing = [name for name in key_fields if name not in payload]
    if missing:
        raise ValueError(
            f"key_fields names payload field(s) not present in the payload: "
            f"{', '.join(sorted(missing))}."
        )
    document = {
        "event_type": event_type,
        "key_fields": {name: payload[name] for name in key_fields},
    }
    digest = hashlib.sha256(_canonical_json(document).encode("utf-8"))
    return digest.hexdigest()
