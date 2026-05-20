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

"""Topic administration for the event-bus SDK.

A small wrapper over :class:`confluent_kafka.admin.AdminClient` for creating
topics. Stage 0 issue 11 owns the *deployment* of the §7b topic set against
RedPanda Cloud; this helper gives the SDK a programmatic way to ensure a topic
exists, which the integration tests use to provision a deterministic,
known-partition topic before a round-trip rather than relying on broker-side
auto-creation.
"""

from __future__ import annotations

from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from trustlist_event_bus.config import EventBusConfig
from trustlist_event_bus.errors import EventBusError


def ensure_topic(
    topic: str,
    *,
    config: EventBusConfig | None = None,
    num_partitions: int = 1,
    replication_factor: int = 1,
) -> None:
    """Create ``topic`` if it does not already exist.

    Creating an existing topic is treated as success — the operation is
    idempotent, which is what makes it safe to call from a test set-up step.

    :param topic: the topic name to ensure.
    :param config: resolved connection settings; :meth:`EventBusConfig.from_env`
        when omitted.
    :param num_partitions: partition count for a newly-created topic. §7b
        partitions by ``domain_id``; a single partition is enough for the
        synthetic round-trip, real topics size this for throughput.
    :param replication_factor: replication factor for a newly-created topic.
        ``1`` suits a single-broker dev cluster; production uses the cluster
        default.
    :raises EventBusError: when the broker rejects the create for a reason
        other than "topic already exists".
    """
    resolved = config or EventBusConfig.from_env()
    admin = AdminClient({"bootstrap.servers": resolved.brokers})
    new_topic = NewTopic(
        topic,
        num_partitions=num_partitions,
        replication_factor=replication_factor,
    )
    futures = admin.create_topics([new_topic])
    try:
        futures[topic].result()
    except Exception as exc:  # noqa: BLE001 - normalised to EventBusError below
        # confluent-kafka raises KafkaException with TOPIC_ALREADY_EXISTS when
        # the topic is already present; that is success for an "ensure".
        if "already exists" in str(exc).lower():
            return
        raise EventBusError(f"failed to create topic {topic!r}: {exc}") from exc
