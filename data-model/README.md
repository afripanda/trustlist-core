# data-model

The canonical-store schema for TrustList: Postgres table definitions, Alembic
migrations and ORM models.

**Status:** Alembic is scaffolded (`alembic.ini`, `migrations/`); no migrations
exist yet. The canonical schema — the eight domain-side tables and seven
authentication-side tables of Stage 0 PRD §7a — lands in Stage 0 issue 12.

## Migration discipline

- **Forward-only.** Rollbacks are expressed as new forward migrations, never as
  `down` migrations run against production.
- **Append-only tables** (`evidence`, `provenance`, `score_history`,
  `attestation`, `auth_audit_event`) are enforced by withholding UPDATE/DELETE
  grants from the application role, not by application logic.
- Every schema change ships with an integration test exercising the new shape
  against a real Postgres (no mocks).

## Database URL

The database URL is supplied at runtime via the `TRUSTLIST_DB_URL` environment
variable (see `migrations/env.py`). No credentials are stored in `alembic.ini`
or anywhere else in the repository.

## Usage

```sh
export TRUSTLIST_DB_URL="postgresql+psycopg://trustlist:trustlist-dev@localhost:5432/trustlist"
cd data-model
alembic upgrade head
```
