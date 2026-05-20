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
 * Generate exhaustive TypeScript payload types from the `event-schema/` JSON
 * Schemas (Stage 0 issue 14 acceptance criteria).
 *
 * Issue 14: "payload types per topic are generated from schema-registry JSON
 * Schemas" via `json-schema-to-typescript`, run on CI, with the generated
 * types checked into the repo. This script reads every `<event_type>.schema.json`
 * file under the shared `event-schema/` directory, compiles each into a
 * TypeScript interface, and writes the result to `src/generated/payloads.ts`.
 *
 * The generated file is committed; CI runs this script and fails if the
 * committed output drifts from what the current schemas produce (see the
 * `generated-types-check` CI job). That keeps the payload types and the
 * schemas in lock-step without a runtime code-generation dependency.
 */

import { readFile, readdir, writeFile, mkdir } from 'node:fs/promises';
import { join, resolve } from 'node:path';

import { compile, type JSONSchema } from 'json-schema-to-typescript';

const SCHEMA_SUFFIX = '.schema.json';

const LICENCE_HEADER = `// Copyright 2026 The TrustList Foundation
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
`;

/**
 * Convert an `event_type` such as `signal.tier-one.example-collector` into a
 * PascalCase interface name such as `SignalTierOneExampleCollectorPayload`.
 */
function interfaceName(eventType: string): string {
  const pascal = eventType
    .split(/[.\-_]/)
    .filter((segment) => segment.length > 0)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join('');
  return `${pascal}Payload`;
}

async function main(): Promise<void> {
  const repoRoot = resolve(__dirname, '..', '..', '..');
  const schemaDir = join(repoRoot, 'event-schema');
  const outputDir = join(__dirname, '..', 'src', 'generated');
  const outputFile = join(outputDir, 'payloads.ts');

  const files = (await readdir(schemaDir))
    .filter((name) => name.endsWith(SCHEMA_SUFFIX))
    .sort();

  const blocks: string[] = [];
  const exportedNames: string[] = [];

  for (const file of files) {
    const eventType = file.slice(0, -SCHEMA_SUFFIX.length);
    const schema = JSON.parse(
      await readFile(join(schemaDir, file), 'utf8'),
    ) as JSONSchema;
    const name = interfaceName(eventType);
    // json-schema-to-typescript names the root interface after the schema's
    // `title`; overwrite it with the event-type-derived name so the generated
    // identifier is deterministic and keyed off the event type, not the
    // human-readable title. The original title is preserved in the JSDoc by
    // `description`.
    const named: JSONSchema = { ...schema, title: name };
    const compiled = await compile(named, name, {
      bannerComment: '',
      additionalProperties: false,
      style: { singleQuote: true },
    });
    blocks.push(
      `// event_type: ${eventType}\n` +
        `// Generated from event-schema/${file}.\n` +
        compiled.trim(),
    );
    exportedNames.push(name);
  }

  const unionName = 'EventPayload';
  const union =
    exportedNames.length > 0
      ? `/**\n` +
        ` * The discriminated union of every known event payload type.\n` +
        ` */\nexport type ${unionName} = ${exportedNames.join(' | ')};\n`
      : `/**\n * No payload schemas were found under event-schema/.\n */\n` +
        `export type ${unionName} = never;\n`;

  const banner =
    `${LICENCE_HEADER}\n` +
    `// AUTO-GENERATED — do not edit by hand.\n` +
    `// Produced by event-bus-sdk/typescript/scripts/generate-payload-types.ts\n` +
    `// from the shared event-schema/ JSON Schemas. Run \`npm run generate-types\`\n` +
    `// to regenerate; CI fails when this file drifts from the schemas.\n`;

  const body = `${banner}\n${blocks.join('\n\n')}\n\n${union}`;

  await mkdir(outputDir, { recursive: true });
  await writeFile(outputFile, body, 'utf8');
  process.stdout.write(
    `Generated ${String(exportedNames.length)} payload type(s) -> ` +
      `${outputFile}\n`,
  );
}

main().catch((error: unknown) => {
  process.stderr.write(
    `payload-type generation failed: ${
      error instanceof Error ? error.message : String(error)
    }\n`,
  );
  process.exitCode = 1;
});
