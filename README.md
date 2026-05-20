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
