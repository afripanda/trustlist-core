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

"""Typed errors for the TrustList event-bus SDK.

Stage 0 PRD §7b makes one error category load-bearing: **back-pressure must
surface as a typed error**. A producer must never block silently when the bus
cannot accept more traffic — the calling collector has to be able to see the
condition and degrade gracefully (drop, buffer, or slow down). The exception
hierarchy here gives every failure mode a distinct, catchable type.
"""

from __future__ import annotations


class EventBusError(Exception):
    """Base class for every error raised by the event-bus SDK.

    Catching this catches anything the SDK itself raises, while the concrete
    subclasses below let a caller react to a specific failure mode.
    """


class BackPressureError(EventBusError):
    """The bus declined the event because its local queue is saturated.

    Raised by the producer when the underlying client's send buffer is full —
    typically because a downstream broker or a slow consumer group has stalled
    the flow. Per PRD §7b a producer must *never* block silently on this
    condition: it surfaces here as a typed error so the collector can choose to
    drop the observation, buffer it elsewhere, or back off and retry.
    """


class ProduceError(EventBusError):
    """The broker rejected, or failed to acknowledge, a produced event.

    Distinct from :class:`BackPressureError`: this is a delivery failure (the
    broker NAK'd the message, the topic does not exist, the connection
    dropped), not a local-buffer saturation that retrying later would clear.
    """


class SchemaValidationError(EventBusError):
    """An event payload did not validate against its registered JSON Schema.

    Raised on both the produce and the consume path (PRD §7b: "schema
    validation on both produce and consume"). On produce it stops a malformed
    event from ever reaching the bus; on consume it quarantines an event whose
    payload does not match the schema the consumer expects.
    """


class SchemaRegistryError(EventBusError):
    """The schema registry could not be reached, or returned an error.

    Covers transport failures talking to RedPanda's built-in
    (Confluent-API-compatible) schema registry, and non-success HTTP responses
    from it — for example a lookup for an unregistered subject.
    """
