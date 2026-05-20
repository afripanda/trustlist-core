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
 * Unit tests for `event-schema/` discovery and loading (PRD §7b).
 *
 * These exercise the bridge between the committed JSON Schema files — shared
 * with the Python SDK — and the registry, without needing a registry: only the
 * filesystem-discovery half is tested here, the registration half is covered
 * by the integration suite.
 */

import { subjectFor } from '../../src/schema-registry';
import { listSchemaFiles, loadSchema } from '../../src/schema-files';

describe('subjectFor', () => {
  it('appends the Confluent -value suffix, matching the Python SDK', () => {
    expect(subjectFor('signal.tier-one.example-collector')).toBe(
      'signal.tier-one.example-collector-value',
    );
  });
});

describe('event-schema discovery', () => {
  it('discovers the shared example-collector schema file', async () => {
    const files = await listSchemaFiles();
    const eventTypes = files.map((file) => file.eventType);
    expect(eventTypes).toContain('signal.tier-one.example-collector');
  });

  it('derives the event type from each filename and loads valid JSON Schema', async () => {
    const files = await listSchemaFiles();
    expect(files.length).toBeGreaterThan(0);
    for (const file of files) {
      expect(file.path).toMatch(/\.schema\.json$/);
      const schema = await loadSchema(file.path);
      // A JSON Schema document — every event-schema/ file declares a $schema.
      expect(schema).toHaveProperty('$schema');
      expect(schema).toHaveProperty('type');
    }
  });
});
