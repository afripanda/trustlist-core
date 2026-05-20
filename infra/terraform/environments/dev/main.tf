# TrustList — dev environment (the trustlist-dev AWS account).
# Provisions the canonical-store databases, the evidence object storage and the
# secrets containers. Stage 0 issues 08, 09, 15.

terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
  }

  # State lives in S3 with a DynamoDB lock table. Both must exist before the
  # first `terraform init` — see infra/terraform/README.md ("State backend").
  backend "s3" {
    bucket         = "trustlist-dev-tfstate"
    key            = "stage-0/terraform.tfstate"
    region         = "af-south-1"
    dynamodb_table = "trustlist-dev-tflock"
    encrypt        = true
  }
}

provider "aws" {
  region  = var.region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = "TrustList"
      Environment = var.environment
      ManagedBy   = "Terraform"
    }
  }
}

module "network" {
  source             = "../../modules/network"
  environment        = var.environment
  availability_zones = var.availability_zones
}

# Two databases — the Foundation / commercial-entity trust boundary is enforced
# at the database, account and network layers (Stage 0 PRD §7c).
module "postgres_foundation" {
  source = "../../modules/postgres"

  environment             = var.environment
  name                    = "foundation"
  db_subnet_group_name    = module.network.db_subnet_group_name
  vpc_security_group_ids  = [module.network.database_security_group_id]
  instance_class          = var.db_instance_class
  multi_az                = var.db_multi_az
  backup_retention_period = var.db_backup_retention_period
  deletion_protection     = var.db_deletion_protection
  skip_final_snapshot     = var.db_skip_final_snapshot
}

module "postgres_commercial" {
  source = "../../modules/postgres"

  environment             = var.environment
  name                    = "commercial"
  db_subnet_group_name    = module.network.db_subnet_group_name
  vpc_security_group_ids  = [module.network.database_security_group_id]
  instance_class          = var.db_instance_class
  multi_az                = var.db_multi_az
  backup_retention_period = var.db_backup_retention_period
  deletion_protection     = var.db_deletion_protection
  skip_final_snapshot     = var.db_skip_final_snapshot
}

module "evidence_storage" {
  source      = "../../modules/object-storage"
  bucket_name = "trustlist-${var.environment}-foundation-evidence-${var.account_id}"
}

module "secrets" {
  source       = "../../modules/secrets"
  environment  = var.environment
  secret_names = var.secret_names
}

output "foundation_db_endpoint" {
  value = module.postgres_foundation.endpoint
}

output "foundation_db_master_secret_arn" {
  value = module.postgres_foundation.master_user_secret_arn
}

output "commercial_db_endpoint" {
  value = module.postgres_commercial.endpoint
}

output "commercial_db_master_secret_arn" {
  value = module.postgres_commercial.master_user_secret_arn
}

output "evidence_bucket" {
  value = module.evidence_storage.bucket_id
}

output "secret_arns" {
  value = module.secrets.secret_arns
}
