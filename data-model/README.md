# data-model

The canonical-store schema for TrustList: Postgres table definitions, Alembic
migrations and ORM models.

**Status:** the Stage 0 canonical schema — the eight domain-side tables and
seven authentication-side tables of Stage 0 PRD §7a — is implemented in
migration `0001_create_canonical_data_model` (Stage 0 issue 12).

## Layout

| Path | Contents |
| --- | --- |
| `trustlist_data_model/` | The SQLAlchemy ORM models. `models.py` defines all fifteen tables and the enum types; `Base.metadata` is the single source of truth Alembic compares against. |
| `migrations/` | The Alembic environment (`env.py`), the revision template (`script.py.mako`) and the revision files under `versions/`. |
| `alembic.ini` | Alembic configuration. Carries no credentials — the URL is supplied at runtime. |

## The fifteen tables

**Domain-side (8):** `domain`, `pool`, `domain_pool_membership`, `attestation`,
`evidence`, `provenance`, `score`, `score_history`.

**Authentication-side (7):** `user_account`, `user_session`,
`user_role_assignment`, `auth_audit_event`, `operator_account_extension`,
`brand_customer_account_extension`, `foundation_user_account_extension`.

The Foundation / commercial-entity database split (§7c) is a *deployment*
concern: operator and Foundation-internal `user_account` rows live in the
Foundation database, brand-customer rows in the commercial-entity database. One
migration and one `metadata` describe both; the physical placement is enforced
at the network and IAM layer, not by separate schemas.

## Migration discipline

- **Forward-only.** Rollbacks are expressed as new forward migrations, never as
  `downgrade` runs against production. `downgrade()` is a deliberate no-op.
- **Append-only tables** (`evidence`, `provenance`, `score_history`,
  `attestation`, `auth_audit_event`) are enforced by withholding UPDATE/DELETE
  grants from the application database role, not by application logic — see
  *Application role* below.
- Every schema change ships with an integration test exercising the new shape
  against a real Postgres (no mocks) — see `tests/test_data_model.py`.

## Application role

Migration `0001` creates a login-less role, `trustlist_app`, that the deployed
application connects *as a member of*. The role carries:

- `SELECT, INSERT` on **every** table;
- `UPDATE, DELETE` on the **mutable** tables only;
- no `UPDATE` or `DELETE` on the five append-only tables.

This is the operational enforcement of the append-only discipline: an `UPDATE`
or `DELETE` against `evidence`, `provenance`, `score_history`, `attestation` or
`auth_audit_event` from the application role is refused by Postgres with a
permission error. Per-environment login roles (provisioned from the secrets
store, §7g) are granted membership of `trustlist_app`.

## `evidence_current` materialised view

`evidence_current` exposes the single most-recent `evidence` row per
`(domain_id, signal_class, source_url)` — the natural key fixed by the PRD §10
content-scrape addendum. It is the Stage 2 scoring-engine read path for current
features.

**Refresh strategy — `REFRESH MATERIALIZED VIEW CONCURRENTLY`.** PRD §10
question 2 left the choice open between concurrent materialised-view refresh,
on-write trigger maintenance and a streaming projection. Concurrent refresh is
chosen as the simplest defensible default:

- it keeps the Stage 2 read path *unblocked* during a refresh (a plain
  `REFRESH` takes an `ACCESS EXCLUSIVE` lock; `CONCURRENTLY` does not), which
  matters because the scoring engine reads this view on the hot path;
- it adds no write-path latency to collectors — the per-observation commit
  discipline of §7a "Constraints surfaced architecturally" stays cheap, unlike
  an on-write trigger that would fire on every `evidence` INSERT;
- `CONCURRENTLY` requires a `UNIQUE` index on the view, which the migration
  creates (`ux_evidence_current_natural_key`) on the natural key.

The trade-off is refresh staleness: the view lags the base table by the refresh
cadence. No Stage 0 component exercises the read path, so the cadence is left to
Stage 2 to tune (a scheduled job or a debounce off the `score.rescore-request`
flow). On-write trigger maintenance and a streaming projection remain available
as a forward migration should Stage 2 measurements show the staleness window is
too wide; revisiting this is explicitly a Stage 2 decision.

## Database URL

The database URL is supplied at runtime via the `TRUSTLIST_DB_URL` environment
variable (see `migrations/env.py`). No credentials are stored in `alembic.ini`
or anywhere else in the repository.

## Usage

```sh
# Start a local Postgres (see the repo-root docker-compose.dev.yml).
docker compose -f ../docker-compose.dev.yml up -d postgres

export TRUSTLIST_DB_URL="postgresql+psycopg://trustlist:trustlist-dev@localhost:5432/trustlist"
cd data-model
uv run alembic upgrade head

# Run the integration tests (from the repo root).
cd ..
uv run pytest -m integration
```

## Tooling notes

- The `migrations/versions/` tree is excluded from `ruff` and `mypy`: future
  revisions are produced by `alembic revision --autogenerate` from a fixed
  template whose conditional import blocks do not survive strict linting and
  type-checking. The type-checked surface that matters is the ORM models in
  `trustlist_data_model/`, which `mypy --strict` covers. The licence-header
  check already skips the same directory.
- `env.py` is excluded from `mypy` strict checking (it is Alembic-framework
  glue executed by Alembic's own runner, against a loosely-typed `context`
  API).
