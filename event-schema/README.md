# event-schema

JSON Schema definitions for event-bus payloads, keyed by `event_type` and
registered with the RedPanda schema registry (ADR-0011). Schemas are
CI-validated against the registry on every pull request (Stage 0 PRD §7b).

## Convention

- One file per `event_type`, named `<event_type>.schema.json`.
- Each file is a JSON Schema (Draft 2020-12) document describing the
  **payload** body — the `payload` field of the §7b event envelope, not the
  envelope itself. The envelope is defined in code
  (`event-bus-sdk/trustlist_event_bus/envelope.py`).
- The event-bus SDK discovers and registers these files via
  `trustlist_event_bus.register_all`; the producer and consumer validate
  payloads against the registered schema on both produce and consume.

## Schemas

| File | `event_type` | Notes |
| --- | --- | --- |
| `signal.tier-one.example-collector.schema.json` | `signal.tier-one.example-collector` | The synthetic tier-one signal exercised by the Stage 0 acceptance round-trip (PRD §8.2) and the issue-13 event-bus SDK integration tests. |

First-party signal collectors land their real payload schemas here in Stage 1;
each later collector and consumer extends this directory as topics are added.
