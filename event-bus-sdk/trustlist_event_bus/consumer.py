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

"""The event-bus consumer (Stage 0 PRD §7b).

:class:`EventConsumer` wraps the librdkafka-based :class:`confluent_kafka.Consumer`
with the consumer-side §7b responsibilities:

1. **Typed event reconstruction** — each Kafka message is decoded back into an
   :class:`~trustlist_event_bus.envelope.EventEnvelope`.
2. **Schema validation on consume** — the payload is validated against the
   registered JSON Schema for its ``event_type`` (§7b: "schema validation on
   both produce and consume").
3. **Distributed-tracing context propagation** — the envelope's
   ``trace_context`` field is passed to the handler, ready for
   :func:`observability.extract_trace_context` / the
   :func:`observability.instrument_consume` decorator, so the handler's span
   joins the producer's trace.
4. **Idempotency / deduplication** — the consumer tracks recently-seen
   ``idempotency_key`` values and skips a redelivery (§7b: "deduplication at
   the consumer driven by the idempotency key").
5. **Offset commit after success** — the offset is committed only *after* the
   handler returns successfully (§7b: at-least-once delivery; "consumers
   commit offsets after successful processing"). A handler exception leaves
   the offset uncommitted, so the event is redelivered.

The deduplication store is pluggable via the ``DedupStore`` protocol. The
default :class:`InMemoryDedupStore` is sufficient for a single consumer
process; PRD §7a names ``output_cache`` and the consumer-side dedup discipline,
and a durable store (Postgres-backed) can be slotted in without touching the
consumer loop.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from types import TracebackType
from typing import Protocol

from confluent_kafka import Consumer, KafkaError, KafkaException, Message

from trustlist_event_bus.config import EventBusConfig
from trustlist_event_bus.envelope import EventEnvelope
from trustlist_event_bus.errors import EventBusError
from trustlist_event_bus.schema_registry import SchemaRegistry

# A consumer handler is any callable taking the decoded envelope. It returns
# nothing; raising signals a processing failure and leaves the offset
# uncommitted so the event is redelivered.
EventHandler = Callable[[EventEnvelope], None]


class DedupStore(Protocol):
    """A store of already-processed ``idempotency_key`` values.

    The consumer asks :meth:`seen` whether a key has been processed and calls
    :meth:`record` once a handler succeeds. Implementations decide retention —
    the default keeps a bounded in-memory window; a durable implementation
    would persist to Postgres.
    """

    def seen(self, idempotency_key: str) -> bool:
        """Return ``True`` when ``idempotency_key`` was already processed."""
        ...

    def record(self, idempotency_key: str) -> None:
        """Record that ``idempotency_key`` has been processed."""
        ...


class InMemoryDedupStore:
    """A bounded, in-memory :class:`DedupStore` (LRU eviction).

    Holds at most ``capacity`` keys; the oldest is evicted when full. This is
    the right default for a single consumer process: it catches the common
    redelivery (a rebalance, or a redeliver after a transient error) without
    unbounded memory growth. A consumer that needs deduplication to survive a
    restart should supply a durable store instead.

    :param capacity: the maximum number of keys retained.
    """

    def __init__(self, capacity: int = 100_000) -> None:
        """Create an empty store holding up to ``capacity`` keys."""
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._capacity = capacity
        self._keys: OrderedDict[str, None] = OrderedDict()

    def seen(self, idempotency_key: str) -> bool:
        """Return ``True`` when the key is in the window, refreshing its age."""
        if idempotency_key in self._keys:
            self._keys.move_to_end(idempotency_key)
            return True
        return False

    def record(self, idempotency_key: str) -> None:
        """Record the key, evicting the oldest entry when at capacity."""
        self._keys[idempotency_key] = None
        self._keys.move_to_end(idempotency_key)
        while len(self._keys) > self._capacity:
            self._keys.popitem(last=False)


class EventConsumer:
    """A typed, schema-validating, deduplicating event-bus consumer.

    :param group_id: the Kafka consumer-group id — co-operating consumer
        instances share one group and the partitions are split across them.
    :param config: resolved connection settings. Defaults to
        :meth:`EventBusConfig.from_env`.
    :param schema_registry: an explicit :class:`SchemaRegistry`; constructed
        from ``config`` when omitted.
    :param dedup_store: the deduplication store; an
        :class:`InMemoryDedupStore` is used when omitted.
    :param extra_config: additional librdkafka consumer settings merged over
        the SDK defaults.
    """

    def __init__(
        self,
        group_id: str,
        *,
        config: EventBusConfig | None = None,
        schema_registry: SchemaRegistry | None = None,
        dedup_store: DedupStore | None = None,
        extra_config: dict[str, object] | None = None,
    ) -> None:
        """Build a consumer bound to the resolved event-bus configuration."""
        self._group_id = group_id
        resolved = config or EventBusConfig.from_env()
        self._registry = schema_registry or SchemaRegistry(
            resolved.schema_registry_url
        )
        self._dedup = dedup_store or InMemoryDedupStore()
        kafka_config: dict[str, object] = {
            "bootstrap.servers": resolved.brokers,
            "group.id": group_id,
            # The SDK commits offsets explicitly *after* the handler succeeds
            # (PRD §7b at-least-once). Auto-commit would commit on a timer
            # regardless of handler success and is therefore disabled.
            "enable.auto.commit": False,
            # A fresh group starts from the beginning of the topic so no
            # already-published event is missed.
            "auto.offset.reset": "earliest",
        }
        if extra_config:
            kafka_config.update(extra_config)
        self._consumer = Consumer(kafka_config)
        self._closed = False

    @property
    def group_id(self) -> str:
        """The Kafka consumer-group id this consumer belongs to."""
        return self._group_id

    def subscribe(self, topics: list[str]) -> None:
        """Subscribe to ``topics``; partitions are assigned on the next poll."""
        self._consumer.subscribe(topics)

    def poll_once(
        self,
        handler: EventHandler,
        *,
        timeout: float = 1.0,
    ) -> EventEnvelope | None:
        """Poll for one event, process it and commit on success.

        The full §7b consume contract for a single event:

        1. poll for a message (``timeout`` seconds);
        2. decode it into an :class:`EventEnvelope`;
        3. validate the payload against its registered schema;
        4. skip it when its ``idempotency_key`` was already processed
           (deduplication) — the offset is still committed, since a duplicate
           is "successfully handled";
        5. otherwise run ``handler``;
        6. on handler success, record the key and commit the offset;
        7. on handler failure, leave the offset uncommitted so the event is
           redelivered, and re-raise.

        :param handler: the callable invoked with the decoded envelope.
        :param timeout: seconds to wait for a message.
        :returns: the processed (or deduplicated) :class:`EventEnvelope`, or
            ``None`` when the poll timed out with no message.
        :raises EventBusError: when decoding, schema validation or the broker
            poll fails. A schema-invalid event is *not* committed — it is left
            for operator triage rather than silently advanced past.
        """
        message = self._consumer.poll(timeout)
        if message is None:
            return None

        error = message.error()
        if error is not None:
            if error.code() == KafkaError._PARTITION_EOF:
                # End of partition is informational, not a failure.
                return None
            raise EventBusError(f"event-bus poll error: {error}")

        envelope = self._decode(message)

        # Schema validation on consume (PRD §7b). A schema-invalid event is
        # not committed; it is surfaced for triage.
        self._registry.validate(envelope.event_type, envelope.payload)

        if self._dedup.seen(envelope.idempotency_key):
            # A redelivery of an already-processed event. It has been handled;
            # commit the offset so the consumer moves past it.
            self._commit(message)
            return envelope

        # Run the handler. On failure the offset stays uncommitted, so the
        # event is redelivered — the at-least-once contract (PRD §7b).
        handler(envelope)

        self._dedup.record(envelope.idempotency_key)
        self._commit(message)
        return envelope

    def run(
        self,
        handler: EventHandler,
        *,
        poll_timeout: float = 1.0,
        max_events: int | None = None,
    ) -> int:
        """Consume in a loop until stopped.

        :param handler: the callable invoked with each decoded envelope.
        :param poll_timeout: seconds each underlying poll waits.
        :param max_events: stop after this many events have been processed
            (or deduplicated). ``None`` runs until :meth:`close` is called
            from another thread or the process is interrupted — the
            production mode. A bounded count is what the integration tests
            use for a deterministic round-trip.
        :returns: the number of events processed.
        """
        processed = 0
        while not self._closed:
            if max_events is not None and processed >= max_events:
                break
            envelope = self.poll_once(handler, timeout=poll_timeout)
            if envelope is not None:
                processed += 1
        return processed

    def _decode(self, message: Message) -> EventEnvelope:
        """Decode a Kafka message body into an :class:`EventEnvelope`."""
        raw = message.value()
        if raw is None:
            raise EventBusError(
                "event-bus message has an empty body; cannot decode an "
                "envelope."
            )
        try:
            return EventEnvelope.from_bytes(raw)
        except (ValueError, KeyError) as exc:
            raise EventBusError(
                f"event-bus message is not a valid envelope: {exc}"
            ) from exc

    def _commit(self, message: Message) -> None:
        """Commit the offset for ``message`` synchronously.

        ``asynchronous=False`` makes the commit durable before the next poll,
        which keeps the at-least-once guarantee tight: a crash immediately
        after a successful handler cannot lose the commit.
        """
        try:
            self._consumer.commit(message=message, asynchronous=False)
        except KafkaException as exc:  # pragma: no cover - broker failure path
            raise EventBusError(f"failed to commit offset: {exc}") from exc

    def close(self) -> None:
        """Stop the :meth:`run` loop and leave the consumer group cleanly."""
        self._closed = True
        if self._consumer is not None:
            self._consumer.close()

    def __enter__(self) -> EventConsumer:
        """Enter a context manager — returns the consumer itself."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Exit the context manager, closing the consumer."""
        self.close()
