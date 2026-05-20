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

/** Unit tests for the in-memory deduplication store (PRD §7b). */

import { InMemoryDedupStore } from '../../src/dedup-store';

describe('InMemoryDedupStore', () => {
  it('reports a recorded key as seen', async () => {
    const store = new InMemoryDedupStore();
    expect(await store.seen('k1')).toBe(false);
    await store.record('k1');
    expect(await store.seen('k1')).toBe(true);
  });

  it('treats distinct keys independently', async () => {
    const store = new InMemoryDedupStore();
    await store.record('k1');
    expect(await store.seen('k2')).toBe(false);
  });

  it('evicts the oldest key once capacity is exceeded (LRU)', async () => {
    const store = new InMemoryDedupStore(2);
    await store.record('k1');
    await store.record('k2');
    await store.record('k3'); // evicts k1, the oldest.
    expect(await store.seen('k1')).toBe(false);
    expect(await store.seen('k2')).toBe(true);
    expect(await store.seen('k3')).toBe(true);
  });

  it('refreshes a key age on seen, sparing it from eviction', async () => {
    const store = new InMemoryDedupStore(2);
    await store.record('k1');
    await store.record('k2');
    await store.seen('k1'); // k1 is now the most-recently-used.
    await store.record('k3'); // evicts k2, not k1.
    expect(await store.seen('k1')).toBe(true);
    expect(await store.seen('k2')).toBe(false);
  });

  it('rejects a capacity below one', () => {
    expect(() => new InMemoryDedupStore(0)).toThrow(/at least 1/);
  });
});
