# observability

The OpenTelemetry instrumentation library, structured-log conventions and
alert definitions adopted by every TrustList component. Exports to Honeycomb
over OTLP per ADR-0012. Implements Stage 0 PRD §7f.

## What this library provides

- **Tracing initialisation** (`tracing.py`) — `init_tracing()` configures the
  OpenTelemetry SDK and selects an exporter from the environment. When
  `OTEL_EXPORTER_OTLP_ENDPOINT` is set, traces are exported over OTLP (the
  Honeycomb path; `OTEL_EXPORTER_OTLP_HEADERS` carries the API key). When it is
  unset, the library falls back to a **console exporter** so tests and local
  runs need neither a network nor an API key; `TRUSTLIST_OTEL_EXPORTER=none`
  selects a no-op exporter instead.
- **Trace-context propagation** (`propagation.py`) — `inject_trace_context()`
  and `extract_trace_context()` move W3C `traceparent` / `tracestate` to and
  from a plain `dict`. The event-bus SDK (issue 13) uses these to carry trace
  context in the event envelope's `trace_context` field, so a signal's flow
  from collector through the bus to the scoring engine forms a single trace.
- **Structured JSON logger** (`logging.py`) — `get_logger()` returns a logger
  emitting one JSON object per line with the §7f field schema: `timestamp`,
  `level`, `component`, `trace_id`, `span_id`, `message`, plus arbitrary
  event-specific fields. `trace_id` / `span_id` are populated from the active
  span automatically.
- **Instrumentation decorators** (`decorators.py`) — one per §7f surface:
  `instrument_http_request`, `instrument_db_query`, `instrument_produce`,
  `instrument_consume` and `instrument_scheduled_job`. Each opens an
  appropriately-kinded span, records exceptions and works on both synchronous
  and asynchronous callables. `instrument_consume` reads the trace-context
  carrier so the consumer span joins the producing component's trace.

## Usage sketch

```python
from observability import get_logger, init_tracing, instrument_http_request

init_tracing("scoring-engine")
log = get_logger("scoring-engine")


@instrument_http_request("GET /domains/{id}")
def get_domain(domain_id: str) -> dict[str, str]:
    log.info("domain fetched", domain_id=domain_id)
    return {"domain_id": domain_id}
```

## Directories

- `alerts/` — alert definitions as code (Postgres connection-pool exhaustion,
  reproducibility-test failure at Stage 0; per-component alerts added later).
  Populated by Stage 0 issue 21.

**Status:** instrumentation library implemented in Stage 0 issue 20. The
minimum-viable alert set lands in issue 21.
