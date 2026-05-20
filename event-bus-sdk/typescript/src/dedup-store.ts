// Copyright 2026 The TrustList Foundation
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * The consumer-side deduplication store (Stage 0 PRD §7b).
 *
 * The event bus is at-least-once: the broker may redeliver an event, and a
 * producer may emit the same logical observation more than once. §7b requires
 * the consumer to deduplicate on the envelope's `idempotency_key`. This module
 * defines the pluggable store interface and a bounded in-memory default,
 * mirroring the Python SDK's `DedupStore` / `InMemoryDedupStore`.
 */

/**
 * A store of already-processed `idempotency_key` values.
 *
 * The consumer asks {@link DedupStore.seen} whether a key has been processed
 * and calls {@link DedupStore.record} once a handler succeeds. Implementations
 * decide retention — the default keeps a bounded in-memory window; a durable
 * implementation would persist to Postgres (PRD §7a `output_cache`). The
 * methods are async so a durable store can do I/O without changing the
 * interface.
 */
export interface DedupStore {
  /** Return `true` when `idempotencyKey` was already processed. */
  seen(idempotencyKey: string): Promise<boolean>;
  /** Record that `idempotencyKey` has been processed. */
  record(idempotencyKey: string): Promise<void>;
}

/**
 * A bounded, in-memory {@link DedupStore} with least-recently-used eviction.
 *
 * Holds at most `capacity` keys; the oldest is evicted when full. This is the
 * right default for a single consumer process: it catches the common
 * redelivery (a rebalance, or a redeliver after a transient error) without
 * unbounded memory growth. A consumer that needs deduplication to survive a
 * restart should supply a durable store instead.
 */
export class InMemoryDedupStore implements DedupStore {
  private readonly capacity: number;
  // A Map preserves insertion order, which gives an O(1) LRU: re-inserting a
  // key moves it to the end, and the oldest is the first key the iterator
  // yields.
  private readonly keys = new Map<string, true>();

  /**
   * Create an empty store holding up to `capacity` keys.
   *
   * @param capacity the maximum number of keys retained; defaults to 100,000.
   */
  public constructor(capacity = 100_000) {
    if (capacity < 1) {
      throw new Error('capacity must be at least 1');
    }
    this.capacity = capacity;
  }

  /** Return `true` when the key is in the window, refreshing its age. */
  public seen(idempotencyKey: string): Promise<boolean> {
    if (this.keys.has(idempotencyKey)) {
      this.keys.delete(idempotencyKey);
      this.keys.set(idempotencyKey, true);
      return Promise.resolve(true);
    }
    return Promise.resolve(false);
  }

  /** Record the key, evicting the oldest entry when at capacity. */
  public record(idempotencyKey: string): Promise<void> {
    this.keys.delete(idempotencyKey);
    this.keys.set(idempotencyKey, true);
    while (this.keys.size > this.capacity) {
      const oldest = this.keys.keys().next().value;
      if (oldest === undefined) {
        break;
      }
      this.keys.delete(oldest);
    }
    return Promise.resolve();
  }
}
