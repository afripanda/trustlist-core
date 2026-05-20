# trustlist-core

`trustlist-core` is the canonical monorepo for **TrustList**, an ad-trust
foundation building a public-good dataset of domain trust scores. It holds the
shared foundations every later component depends on: the canonical data model,
the event-bus SDKs, the scoring-engine kernel, the signal-collector framework,
and the common authentication, secrets and observability libraries.

This repository contains code only. The architecture, PRDs, ADRs and planning
documents live in the separate **TrustList2** planning repository.

## Repository layout

| Directory | Contents |
| --- | --- |
| `data-model/` | Postgres schema, Alembic migrations and ORM models for the canonical store. |
| `event-bus-sdk/` | Producer/consumer SDKs (Python and TypeScript) for the RedPanda event bus. |
| `event-schema/` | JSON Schema definitions for event payloads, registered with the schema registry. |
| `scoring-engine/` | The scoring-engine kernel: event subscription, score write-back, version stamping. |
| `signal-collector-framework/` | Base classes and lifecycle for Stage 1 signal collectors. |
| `auth/` | The three-archetype authentication library (operator, brand customer, Foundation-internal). |
| `secrets/` | Client wrapper over the cloud-native secrets store. |
| `observability/` | OpenTelemetry instrumentation library, structured-log conventions and alert definitions. |
| `ci/` | Shared CI scripts and reusable workflow logic. |
| `docs/` | Internal engineering documentation. |
| `tests/` | Repository-level tests. |

Each directory currently holds a placeholder `README.md`; the modules land
across later Stage 0 and Stage 1 implementation issues.

## Tooling and conventions

These are the technical lead's tactical choices, recorded here as required by
the Stage 0 Foundation Setup PRD (§7d).

- **CI runner platform — GitHub Actions.** Workflows live in `.github/workflows/`.
- **Migration framework — Alembic.** The project is Python-led; the collectors
  and the scoring engine are Python, so a Python-native migration tool keeps the
  toolchain coherent. Alembic is scaffolded under `data-model/`.
- **Infrastructure-as-code — Terraform.** Chosen for portability. No Terraform
  code exists yet; provisioning lands in the storage and event-bus issues.
- **Python toolchain — `uv`.** Dependency management and virtual environments
  via `uv`; linting via `ruff`; type-checking via `mypy`; testing via `pytest`.
- **Commit messages — Conventional Commits.** Enforced in CI.

## Local development

A `docker-compose.dev.yml` at the repository root stands up the backing services
needed to build and smoke-test the foundation locally, with no cloud account:

- `postgres` — the canonical store.
- `timescaledb` — the local stand-in for the managed time-series store.
- `redpanda` — the event bus, including its built-in schema registry.

```sh
docker compose -f docker-compose.dev.yml up -d
uv sync
uv run pytest
```

See `docs/local-development.md` for detail. This local-first workflow is an
addition beyond the literal PRD §7d scope; the rationale is recorded in that
document.

## Continuous integration

Every pull request runs: lint, type-check, unit tests, integration tests,
licence-header check, secret-leak scan, the reproducibility test, and a
Conventional Commits message check. The `main` branch is protected — merging
requires CI green plus technical-lead approval.

The `reproducibility` job, like the `integration-tests` job, runs inside a
container with a real Postgres and a real RedPanda as `services:` — the
reproducibility test exercises the full event-bus → canonical-store path and
re-migrates the schema between runs, so it needs the same real backing services
the smoke test does.

## End-to-end smoke test

`tests/smoke/` holds the Stage 0 end-to-end smoke test (PRD §8 acceptance
criterion 2): the synthetic-signal round-trip that proves a signal flows
producer → event bus → evidence-writer → canonical store. It comprises the
evidence-writer service stub (`tests/smoke/evidence_writer.py`) and the
integration harness (`tests/smoke/test_smoke_roundtrip.py`). It runs as an
`@pytest.mark.integration` test, so the CI `integration-tests` job — which
already stands up Postgres and RedPanda — picks it up via `pytest -m integration`.

To run it locally, stand up isolated containers on distinct high ports (the
`docker-compose.dev.yml` and `mvp0-*` containers occupy the standard ports),
apply the migrations and export the connection details:

```sh
docker run -d --name trustlist-issue22-pg \
  -e POSTGRES_USER=trustlist -e POSTGRES_PASSWORD=trustlist-dev \
  -e POSTGRES_DB=trustlist -p 55432:5432 postgres:16

docker run -d --name trustlist-issue22-redpanda \
  -p 19293:19293 -p 18193:18193 -p 18194:18194 \
  redpandadata/redpanda:v24.2.7 \
  redpanda start --mode dev-container --smp 1 \
  --kafka-addr internal://0.0.0.0:9092,external://0.0.0.0:19293 \
  --advertise-kafka-addr internal://localhost:9092,external://localhost:19293 \
  --schema-registry-addr internal://0.0.0.0:8081,external://0.0.0.0:18193 \
  --pandaproxy-addr internal://0.0.0.0:8082,external://0.0.0.0:18194 \
  --rpc-addr 0.0.0.0:33145 --advertise-rpc-addr localhost:33145

export TRUSTLIST_DB_URL=postgresql+psycopg://trustlist:trustlist-dev@localhost:55432/trustlist
export TRUSTLIST_EVENT_BUS_BROKERS=localhost:19293
export TRUSTLIST_SCHEMA_REGISTRY_URL=http://localhost:18193
(cd data-model && uv run alembic upgrade head)
uv run pytest -m integration tests/smoke
```

## Reproducibility test

`tests/reproducibility/` holds the Stage 0 reproducibility test (PRD §8
acceptance criterion 5): it processes a deterministic fixture signal — the same
fixed synthetic event the smoke test uses — through the canonical store, asserts
the resulting `domain` / `provenance` / `evidence` rows are *exactly* the
checked-in expected snapshot (`expected_snapshot.json`), then re-runs against a
freshly-migrated database and asserts the rows are byte-identical (modulo the
unavoidably-varying surrogate UUIDs and `now()` timestamps, which it asserts are
the only difference). It extends the smoke-test harness and runs as an
`@pytest.mark.integration` test.

It needs the same isolated containers and connection variables as the smoke test
(above); the dedicated CI `reproducibility` job stands up its own Postgres and
RedPanda. To run it locally, with the variables exported and the migrations
applied:

```sh
uv run pytest -m integration tests/reproducibility
```

If a deliberate change to the data model or the evidence-writer alters the
canonical store's deterministic output, regenerate `expected_snapshot.json` and
review the diff before committing.

## Contributing

Pull requests must use the template in `.github/pull_request_template.md`, which
requires a linked ADR or grilling document for the design decision, the linked
Stage and issue, updated tests and updated documentation.

## Licensing

All code in this repository is licensed under the **Apache License 2.0** — see
`LICENSE`. Data artefacts published by TrustList (handled in a later stage) are
released under the **Open Database License (ODbL)**. The commercial
brand-customer portal is closed-source and lives in a separate, private
repository.
