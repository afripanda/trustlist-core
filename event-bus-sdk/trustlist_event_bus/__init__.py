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

"""TrustList event-bus SDK — Python producer and consumer (Stage 0 PRD §7b).

This package is the Python half of the event-bus SDK (the TypeScript half is
Stage 0 issue 14). It wraps the RedPanda event bus (ADR-0011, Kafka-wire
compatible) with the responsibilities §7b puts on an SDK:

- **Typed event-envelope construction** — :class:`EventEnvelope` and
  :func:`new_envelope` carry every §7b field: ``event_id``, ``event_type``,
  ``event_version``, ``produced_at``, ``producer_id``, ``trace_context``,
  ``idempotency_key``, ``provenance`` and ``payload``.
- **Schema-registry integration** — :class:`SchemaRegistry` registers and
  validates payload JSON Schemas against RedPanda's built-in
  Confluent-API-compatible registry; the schema files live under
  ``event-schema/``.
- **Idempotency-key derivation** — :func:`derive_idempotency_key` builds a
  stable key from payload-specific fields.
- **Distributed-tracing propagation** — the producer injects the active W3C
  trace context (via :mod:`observability`) into the envelope; the consumer
  surfaces it to the handler.
- **Back-pressure as a typed error** — :class:`BackPressureError`; the
  producer never blocks silently.
- **At-least-once with consumer-side dedup** — :class:`EventConsumer` commits
  offsets only after the handler succeeds and deduplicates on the
  ``idempotency_key``.

The SDK is versioned independently of ``trustlist-core`` and follows semver;
see ``event-bus-sdk/README.md``.
"""

from trustlist_event_bus.admin import ensure_topic
from trustlist_event_bus.config import EventBusConfig
from trustlist_event_bus.consumer import (
    DedupStore,
    EventConsumer,
    EventHandler,
    InMemoryDedupStore,
)
from trustlist_event_bus.envelope import (
    ENVELOPE_VERSION,
    EventEnvelope,
    Provenance,
    new_envelope,
)
from trustlist_event_bus.errors import (
    BackPressureError,
    EventBusError,
    ProduceError,
    SchemaRegistryError,
    SchemaValidationError,
)
from trustlist_event_bus.idempotency import derive_idempotency_key
from trustlist_event_bus.producer import EventProducer
from trustlist_event_bus.schema_files import (
    iter_schema_files,
    load_schema,
    register_all,
)
from trustlist_event_bus.schema_registry import SchemaRegistry, subject_for

# The SDK's own semver version, independent of trustlist-core (PRD §7b).
__version__ = "0.1.0"

__all__ = [
    "ENVELOPE_VERSION",
    "BackPressureError",
    "DedupStore",
    "EventBusConfig",
    "EventBusError",
    "EventConsumer",
    "EventEnvelope",
    "EventHandler",
    "EventProducer",
    "InMemoryDedupStore",
    "ProduceError",
    "Provenance",
    "SchemaRegistry",
    "SchemaRegistryError",
    "SchemaValidationError",
    "__version__",
    "derive_idempotency_key",
    "ensure_topic",
    "iter_schema_files",
    "load_schema",
    "new_envelope",
    "register_all",
    "subject_for",
]
