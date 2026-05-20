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

"""The event-bus producer (Stage 0 PRD §7b).

:class:`EventProducer` wraps the librdkafka-based :class:`confluent_kafka.Producer`
with the four SDK responsibilities §7b puts on the producer:

1. **Typed event construction** — :meth:`EventProducer.produce` builds a full
   :class:`~trustlist_event_bus.envelope.EventEnvelope` (generating
   ``event_id`` and ``produced_at``).
2. **Schema validation on produce** — the payload is validated against the
   registered JSON Schema for its ``event_type`` before anything is sent.
3. **Distributed-tracing context propagation** — the active W3C trace context
   is injected into the envelope's ``trace_context`` field via
   :func:`observability.inject_trace_context`, so the consumer's span joins
   this producer's trace.
4. **Back-pressure as a typed error** — when librdkafka's local queue is full,
   the SDK raises :class:`~trustlist_event_bus.errors.BackPressureError`
   rather than blocking. A producer must *never* block silently (§7b).

Partitioning. §7b: topics are partitioned by ``domain_id`` where applicable to
preserve per-domain ordering. The producer uses the supplied ``partition_key``
as the Kafka message key; librdkafka's default partitioner then maps equal
keys to the same partition. Callers pass the ``domain_id`` (or ``user_id`` for
``auth.audit``) as the key.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from confluent_kafka import KafkaException, Producer

from observability import inject_trace_context
from trustlist_event_bus.config import EventBusConfig
from trustlist_event_bus.envelope import EventEnvelope, Provenance, new_envelope
from trustlist_event_bus.errors import BackPressureError, ProduceError
from trustlist_event_bus.schema_registry import SchemaRegistry

# librdkafka's error code for a full local producer queue. Surfacing it as
# BackPressureError is the §7b back-pressure contract.
_QUEUE_FULL = "_QUEUE_FULL"


class EventProducer:
    """A typed, schema-validating, trace-propagating event-bus producer.

    :param producer_id: identifies this component instance; stamped into every
        envelope's ``producer_id`` field.
    :param config: resolved connection settings. Defaults to
        :meth:`EventBusConfig.from_env` so production code needs no arguments.
    :param schema_registry: an explicit :class:`SchemaRegistry`; constructed
        from ``config`` when omitted. Tests inject one to share a registry.
    :param extra_config: additional librdkafka producer settings merged over
        the SDK defaults — an escape hatch, rarely needed.
    """

    def __init__(
        self,
        producer_id: str,
        *,
        config: EventBusConfig | None = None,
        schema_registry: SchemaRegistry | None = None,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        """Build a producer bound to the resolved event-bus configuration."""
        self._producer_id = producer_id
        resolved = config or EventBusConfig.from_env()
        self._registry = schema_registry or SchemaRegistry(
            resolved.schema_registry_url
        )
        kafka_config: dict[str, Any] = {
            "bootstrap.servers": resolved.brokers,
            # At-least-once delivery with no silent loss: full in-sync-replica
            # acknowledgement and idempotent producer semantics (PRD §7b).
            "acks": "all",
            "enable.idempotence": True,
        }
        if extra_config:
            kafka_config.update(extra_config)
        self._producer = Producer(kafka_config)

    @property
    def producer_id(self) -> str:
        """The component-instance id stamped into every envelope."""
        return self._producer_id

    def produce(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        event_type: str,
        idempotency_key: str,
        provenance: Provenance,
        partition_key: str | None = None,
        event_version: str | None = None,
        flush: bool = False,
    ) -> EventEnvelope:
        """Construct, validate and publish an event.

        :param topic: the Kafka topic — one of the §7b topic set.
        :param payload: the event-type-specific body.
        :param event_type: the topic plus payload-type qualifier; also the
            schema-registry key.
        :param idempotency_key: the deduplication key; derive it with
            :func:`trustlist_event_bus.idempotency.derive_idempotency_key`.
        :param provenance: the observation's origin (§7a).
        :param partition_key: the Kafka message key — pass ``domain_id`` (or
            ``user_id`` for ``auth.audit``) to preserve per-key ordering.
        :param event_version: the payload schema version; defaults to the
            envelope version when omitted.
        :param flush: when ``True``, block until the broker acknowledges
            before returning — useful for tests and low-rate emitters.
        :returns: the :class:`EventEnvelope` that was published.
        :raises SchemaValidationError: when the payload fails schema
            validation — the event is *not* sent.
        :raises BackPressureError: when librdkafka's local queue is full. The
            caller must decide how to degrade; the SDK never blocks silently.
        :raises ProduceError: when the produce call fails for any other
            reason (unknown topic, broker unreachable, ...).
        """
        # Schema validation first — a malformed payload never reaches the bus.
        self._registry.validate(event_type, payload)

        # Inject the active W3C trace context so the consumer span joins this
        # trace. With no active span the carrier is simply empty.
        trace_context = inject_trace_context()

        envelope = new_envelope(
            event_type=event_type,
            payload=payload,
            producer_id=self._producer_id,
            idempotency_key=idempotency_key,
            provenance=provenance,
            trace_context=trace_context,
            event_version=event_version or "1.0.0",
        )

        try:
            self._producer.produce(
                topic=topic,
                key=partition_key.encode("utf-8") if partition_key else None,
                value=envelope.to_bytes(),
            )
        except BufferError as exc:
            # librdkafka raises BufferError when the local produce queue is
            # full — this is the back-pressure signal. Surface it typed.
            raise BackPressureError(
                f"event-bus producer queue is full while producing to "
                f"{topic!r}; the bus is applying back-pressure. The caller "
                "must degrade gracefully — the SDK does not block."
            ) from exc
        except KafkaException as exc:
            if _QUEUE_FULL in str(exc):
                raise BackPressureError(
                    f"event-bus producer queue is full while producing to "
                    f"{topic!r}; the bus is applying back-pressure."
                ) from exc
            raise ProduceError(
                f"failed to produce to {topic!r}: {exc}"
            ) from exc

        # poll(0) services delivery callbacks without blocking; flush() blocks
        # until every outstanding message is acknowledged.
        if flush:
            self._producer.flush()
        else:
            self._producer.poll(0)
        return envelope

    def flush(self, timeout: float = 10.0) -> int:
        """Block until outstanding events are delivered.

        :param timeout: seconds to wait.
        :returns: the number of messages still in the queue when the timeout
            elapsed — ``0`` means everything was delivered.
        """
        return self._producer.flush(timeout)

    def close(self) -> None:
        """Flush any buffered events; call before discarding the producer."""
        self._producer.flush()

    def __enter__(self) -> EventProducer:
        """Enter a context manager — returns the producer itself."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit the context manager, flushing buffered events."""
        self.close()
