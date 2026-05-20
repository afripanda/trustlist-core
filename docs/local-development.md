# Local development

`trustlist-core` is built and smoke-tested locally against containerised
backing services, so engineering can proceed without a cloud account and
without spend. Cloud provisioning (AWS, RedPanda Cloud, Timescale Cloud) runs
as a parallel track and is required only for the in-cloud Stage 0 acceptance
run.

## Scope note

`docker-compose.dev.yml` is an addition beyond the literal scope of Stage 0
issue 06 and PRD §7d. It was introduced to enable a local-first
build-and-smoke-test workflow — the fastest route to a demonstrable pipeline —
while the cloud accounts are being provisioned. The PRD's `dev` / `prod` cloud
environment topology (§7c) remains the target for Stage 0 acceptance; the local
compose stack is a development convenience, not a replacement for it.

## Services

| Service | Purpose | Host port(s) |
| --- | --- | --- |
| `postgres` | Canonical store (Foundation + commercial-entity schemas). | 5432 |
| `timescaledb` | Local stand-in for the managed time-series store. | 5433 |
| `redpanda` | Event bus, including the built-in schema registry. | 19092 (Kafka), 18081 (schema registry) |

## Usage

```sh
# Start the backing services
docker compose -f docker-compose.dev.yml up -d

# Install dependencies (creates .venv)
uv sync

# Run the test suite
uv run pytest
```

Connection strings reach the application code via environment variables (for
example `TRUSTLIST_DB_URL`); no credentials are committed to the repository.

## Stopping

```sh
docker compose -f docker-compose.dev.yml down        # stop, keep data volumes
docker compose -f docker-compose.dev.yml down -v     # stop and discard data
```
