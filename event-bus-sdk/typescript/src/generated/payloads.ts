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

// AUTO-GENERATED — do not edit by hand.
// Produced by event-bus-sdk/typescript/scripts/generate-payload-types.ts
// from the shared event-schema/ JSON Schemas. Run `npm run generate-types`
// to regenerate; CI fails when this file drifts from the schemas.

// event_type: signal.tier-one.example-collector
// Generated from event-schema/signal.tier-one.example-collector.schema.json.
/**
 * The payload body of a tier-one signal event emitted by the example collector. Carried inside the §7b event envelope on the signal.tier-one.example-collector topic. This is the synthetic signal exercised by the Stage 0 acceptance round-trip (PRD §8.2); first-party signal collectors land their real payload schemas in Stage 1.
 */
export interface SignalTierOneExampleCollectorPayload {
  /**
   * The domain the observation concerns; also the topic partition key (PRD §7b).
   */
  domain_id: string;
  /**
   * The signal class — for example 'dns' or 'content' (§7a evidence.signal_class).
   */
  signal_class: string;
  /**
   * The per-source-URL granularity key for content-derived signals (§7a evidence_current natural key). Empty string when not source-URL scoped.
   */
  source_url?: string;
  /**
   * When the observation was made (§7a evidence.observed_at).
   */
  observed_at: string;
  /**
   * The observed value, a free-form JSON object (§7a evidence.observed_value is jsonb).
   */
  observed_value: {};
}

/**
 * The discriminated union of every known event payload type.
 */
export type EventPayload = SignalTierOneExampleCollectorPayload;
