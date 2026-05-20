# auth

The shared authentication library for TrustList — the foundation every
archetype's auth surface (operator, brand customer, Foundation-internal) builds
on. It wraps the identity provider behind a stable interface so application
code never binds to the provider directly.

**Status:** the Stage 0 shared library and core primitives — Stage 0 PRD §7e —
are implemented in the `trustlist_auth` package (Stage 0 issue 16). The
per-archetype authentication surfaces are issues 17 (operator), 18 (brand
customer) and 19 (Foundation-internal); the real Clerk provider adapter is
wired by those issues.

## What this library provides

| Module | Responsibility |
| --- | --- |
| `passwords` | Argon2id password hashing (RFC 9106 high-memory profile). |
| `totp` | RFC 6238 TOTP 2FA — secret generation, provisioning URI, verification. |
| `tokens` | HMAC-signed, expiring email-verification tokens. |
| `rbac` | The RBAC framework — a common permission model and per-archetype role catalogues. |
| `role_store` | Persistence of role assignments over `user_role_assignment`. |
| `provider` | The `IdentityProvider` abstraction — the stable interface a provider sits behind. |
| `fake_provider` | `InMemoryIdentityProvider` — the reference in-memory provider used by tests and local development. |
| `audit` | Authentication audit-trail emission to `auth_audit_event`, with the `auth.audit` event-bus mirror seam. |
| `service` | `AuthService` — the composition layer the per-archetype surfaces hold. |
| `testing` | Exported test utilities — the trust-boundary test harness (PRD §7e). |

## The provider abstraction

ADR-0014 chose Clerk as the identity provider, but PRD §7e makes the
*abstraction* a hard architectural requirement. `IdentityProvider` (a
`typing.Protocol`) is the stable interface — `authenticate`, `issue_session`,
`revoke_session`, `enrol_totp`, `verify_totp`, plus registration and lookup.
Two implementations sit behind it:

- `InMemoryIdentityProvider` — a complete, in-memory provider. It is the
  reference implementation and the fixture this library's tests run against; no
  network, no Clerk account.
- the Clerk adapter — *not built in this issue*. Issues 17–19 wire a thin Clerk
  adapter behind the same protocol. The protocol is shaped so that adapter is a
  translation layer, not a rewrite.

The provider is injected into `AuthService` at construction, so swapping it — a
fake in a test, the real Clerk adapter in production — changes no caller code.

## The `auth.audit` event-bus seam

PRD §7b mirrors `auth_audit_event` writes onto the `auth.audit` topic. The
Python event-bus SDK that publishes there is issue 13, which is not yet in
`main`. To avoid a hard dependency on unmerged code, `audit` mirrors through an
`AuditEventSink` protocol:

- `NullAuditEventSink` — the Stage-0 default; discards the event. The database
  row is the system of record, so this loses no audit data.
- `LoggingAuditEventSink` — mirrors to the structured log.
- the event-bus sink — added when issue 13 lands. It is a one-class change: the
  `AuditEvent` value object already carries everything the §7b envelope needs.
  Every spot the future adapter touches is marked with an `# EVENT-BUS SEAM`
  comment.

## Out of scope for issue 16

- The per-archetype authentication surfaces — issues 17, 18, 19.
- The real Clerk provider adapter — wired by issues 17–19.
- WebAuthn 2FA — Stage 4b (the RBAC and provider APIs accept it without change).
- Tier-2 operator verification and brand-customer custom roles — Stage 4b / 5.
