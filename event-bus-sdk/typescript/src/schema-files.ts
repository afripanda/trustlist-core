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
 * Loading and registering the `event-schema/` JSON Schema files.
 *
 * Stage 0 PRD §7b: "All payload schemas are stored in `trustlist-core` under
 * `event-schema/` and CI-validated against the registry on every PR." The
 * `event-schema/` directory is shared by both SDKs — the same JSON Schema
 * files the Python SDK registers. This module is the TypeScript bridge between
 * those committed files and the running schema registry, mirroring the Python
 * SDK's `schema_files.py`. It:
 *
 * - discovers every `<event_type>.schema.json` file under `event-schema/`;
 * - derives the `event_type` from each filename;
 * - registers each schema with a {@link SchemaRegistry}.
 */

import { readFile, readdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import type { JsonObject } from './idempotency';
import type { SchemaRegistry } from './schema-registry';

/** Files carry this suffix; the part before it is the `event_type`. */
const SCHEMA_SUFFIX = '.schema.json';

/**
 * Return the repository's `event-schema/` directory.
 *
 * This module is published from `event-bus-sdk/typescript/`; the shared schema
 * directory is `event-schema/` two levels up from the package root. At runtime
 * the compiled file lives in `dist/`, so the package root is one level above
 * the directory holding this file.
 */
function repoEventSchemaDir(): string {
  // __dirname is .../event-bus-sdk/typescript/dist (built) or
  // .../event-bus-sdk/typescript/src (ts-node/jest). Either way the package
  // root is one directory up, and event-schema/ is two further up.
  return resolve(__dirname, '..', '..', '..', 'event-schema');
}

/** An `(eventType, path)` pair for one discovered schema file. */
export interface SchemaFile {
  /** The `event_type` derived from the filename. */
  readonly eventType: string;
  /** The absolute path to the schema file. */
  readonly path: string;
}

/**
 * List `(eventType, path)` for every schema file under `directory`.
 *
 * @param directory the directory to scan; defaults to the repository's
 *   `event-schema/` directory.
 * @returns the discovered schema files, sorted by filename.
 */
export async function listSchemaFiles(
  directory?: string,
): Promise<SchemaFile[]> {
  const base = directory ?? repoEventSchemaDir();
  const entries = await readdir(base);
  return entries
    .filter((name) => name.endsWith(SCHEMA_SUFFIX))
    .sort()
    .map((name) => ({
      eventType: name.slice(0, -SCHEMA_SUFFIX.length),
      path: join(base, name),
    }));
}

/**
 * Load and parse a JSON Schema file.
 *
 * @param path the schema file path.
 * @returns the parsed JSON Schema document.
 */
export async function loadSchema(path: string): Promise<JsonObject> {
  const text = await readFile(path, 'utf8');
  return JSON.parse(text) as JsonObject;
}

/**
 * Register every `event-schema/` file with `registry`.
 *
 * The CI `event-schema` step calls this so the registry always reflects the
 * committed schemas; the integration tests call it to provision the registry
 * before a round-trip.
 *
 * @param registry the schema registry to populate.
 * @param directory the directory to scan; defaults to `event-schema/`.
 * @returns a mapping of `event_type` to the registry-assigned schema id.
 */
export async function registerAll(
  registry: SchemaRegistry,
  directory?: string,
): Promise<Record<string, number>> {
  const registered: Record<string, number> = {};
  for (const { eventType, path } of await listSchemaFiles(directory)) {
    const schema = await loadSchema(path);
    registered[eventType] = await registry.register(eventType, schema);
  }
  return registered;
}
