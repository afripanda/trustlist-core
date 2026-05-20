# Infrastructure — Terraform

Infrastructure-as-code for the TrustList cloud foundation. This directory
provisions Stage 0 issues **08** (canonical-store Postgres), **09** (evidence
object storage) and **15** (secrets management) on AWS, per ADR-0010 (AWS,
`af-south-1`) and Stage 0 PRD §7c / §7g.

> **Status — draft, not yet applied.** No AWS account exists yet. These
> configurations are written and ready to `terraform apply` once the AWS
> Organization and the `trustlist-dev` / `trustlist-prod` member accounts are
> created.

## Layout

```
infra/terraform/
├── modules/
│   ├── network/         VPC, private subnets, DB subnet group, security group
│   ├── postgres/        a managed RDS PostgreSQL instance
│   ├── object-storage/  an S3 bucket (versioned, encrypted, lifecycle-tiered)
│   └── secrets/         AWS Secrets Manager secret containers
└── environments/
    ├── dev/             the trustlist-dev account
    └── prod/            the trustlist-prod account
```

`dev` and `prod` are **separate AWS accounts** (Stage 0 PRD §7c — blast-radius
and trust-boundary isolation), so each is a separate Terraform root, applied
independently with its own state.

## What it provisions, per environment

- **Network** — a dedicated VPC with private subnets across two availability
  zones; the databases are not internet-reachable.
- **Two PostgreSQL instances** (issue 08) — one Foundation-side, one
  commercial-entity-side. The Foundation / commercial-entity trust boundary is
  enforced at the database, account and network layers, not within one shared
  database. Storage is encrypted; the master password is generated and held by
  RDS in Secrets Manager and never touches Terraform state.
- **The Foundation evidence bucket** (issue 09) — versioned, KMS-encrypted, all
  public access blocked, with a lifecycle rule moving blobs to cold storage
  after 90 days. The `object-storage` module is reused per brand customer for
  the isolated per-customer audit buckets in a later stage.
- **Secrets Manager secret containers** (issue 15) — named, empty secrets for
  the RedPanda, Timescale, Honeycomb and Clerk credentials plus the application
  database logins. **Values are populated out of band** (see below).

## Prerequisites

1. The AWS Organization and the `trustlist-dev` / `trustlist-prod` member
   accounts exist, with `af-south-1` enabled.
2. AWS CLI SSO profiles named `trustlist-dev` and `trustlist-prod` are
   configured (`aws configure sso`).
3. Terraform >= 1.6.
4. `account_id` filled in for each environment's `terraform.tfvars`.

## State backend (one-time bootstrap)

Terraform state lives in S3 with a DynamoDB lock table — which must exist
*before* the first `terraform init`. Create them once per account (shown for
`dev`; repeat with the `prod` names):

```sh
aws s3api create-bucket --bucket trustlist-dev-tfstate \
  --region af-south-1 \
  --create-bucket-configuration LocationConstraint=af-south-1 \
  --profile trustlist-dev
aws s3api put-bucket-versioning --bucket trustlist-dev-tfstate \
  --versioning-configuration Status=Enabled --profile trustlist-dev
aws dynamodb create-table --table-name trustlist-dev-tflock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST --region af-south-1 --profile trustlist-dev
```

## Applying

```sh
cd environments/dev      # or environments/prod
terraform init
terraform plan           # review carefully — this creates billable resources
terraform apply
```

## After apply — populate the secrets

Terraform creates the secret *containers* but never their values. Populate each
out of band once the upstream service exists, for example:

```sh
aws secretsmanager put-secret-value \
  --secret-id trustlist/dev/observability/honeycomb \
  --secret-string '{"api_key":"..."}' --profile trustlist-dev
```

The RDS master-password secrets are created and rotated by RDS itself — their
ARNs are surfaced in `terraform output`.

## Cost note

This provisions **billable** resources — two RDS instances, S3, KMS and Secrets
Manager — per environment. `dev` is tuned lean (single-AZ `db.t4g.micro`,
7-day backups); `prod` runs for resilience (Multi-AZ `db.t4g.medium`, 30-day
point-in-time recovery). Review the sizes in each `terraform.tfvars` and always
run `terraform plan` before `apply`.

## Deliberately deferred

- **Connection pooling** (RDS Proxy / PgBouncer, PRD §7c) — added when
  application compute lands and there is a pooling target.
- **Public subnets / NAT / interface VPC endpoints** — no application compute
  exists yet; the VPC carries private database subnets only.
- **Per-customer evidence buckets** — the `object-storage` module supports them;
  none are instantiated until brand customers exist.
- **A `terraform fmt` / `validate` CI job** — worth adding in a follow-up.
- **RedPanda Cloud / Timescale Cloud** (issues 11, 10) — BYOC, provisioned
  through their own consoles; their credentials land in the secret containers
  created here.
