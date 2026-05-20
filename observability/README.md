# observability

The OpenTelemetry instrumentation library, structured-log conventions and
alert definitions adopted by every TrustList component. Exports to Honeycomb
per ADR-0012.

- `alerts/` — alert definitions as code (Postgres connection-pool exhaustion,
  reproducibility-test failure at Stage 0; per-component alerts added later).

**Status:** placeholder. The instrumentation library is implemented in Stage 0
issue 20 and the minimum-viable alert set in issue 21.
