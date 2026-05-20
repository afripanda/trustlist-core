# One managed PostgreSQL instance for the TrustList canonical store. Two are
# provisioned per environment — Foundation-side and commercial-entity-side — so
# the trust boundary holds at the database layer. Stage 0 PRD §7c, issue 08.

variable "environment" {
  type = string
}

variable "name" {
  type        = string
  description = "Logical instance name, e.g. \"foundation\" or \"commercial\"."
}

variable "db_subnet_group_name" {
  type = string
}

variable "vpc_security_group_ids" {
  type = list(string)
}

variable "instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "allocated_storage" {
  type    = number
  default = 20
}

variable "max_allocated_storage" {
  type        = number
  default     = 100
  description = "Upper bound for RDS storage autoscaling."
}

variable "multi_az" {
  type    = bool
  default = false
}

variable "backup_retention_period" {
  type        = number
  default     = 7
  description = "Days of automated backups; also the point-in-time-recovery window."
}

variable "deletion_protection" {
  type    = bool
  default = false
}

variable "skip_final_snapshot" {
  type    = bool
  default = true
}

resource "aws_db_instance" "this" {
  identifier     = "trustlist-${var.environment}-${var.name}"
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.instance_class

  allocated_storage     = var.allocated_storage
  max_allocated_storage = var.max_allocated_storage
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "trustlist"
  username = "trustlist_admin"

  # The master password is generated and held by RDS in AWS Secrets Manager.
  # It never appears in Terraform configuration or state.
  manage_master_user_password = true

  db_subnet_group_name   = var.db_subnet_group_name
  vpc_security_group_ids = var.vpc_security_group_ids
  publicly_accessible    = false

  multi_az                = var.multi_az
  backup_retention_period = var.backup_retention_period
  copy_tags_to_snapshot   = true

  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = var.skip_final_snapshot
  final_snapshot_identifier = var.skip_final_snapshot ? null : "trustlist-${var.environment}-${var.name}-final"

  auto_minor_version_upgrade = true
  apply_immediately          = false

  tags = { Name = "trustlist-${var.environment}-${var.name}" }
}

output "instance_identifier" {
  value = aws_db_instance.this.identifier
}

output "endpoint" {
  value = aws_db_instance.this.endpoint
}

output "master_user_secret_arn" {
  value       = aws_db_instance.this.master_user_secret[0].secret_arn
  description = "Secrets Manager secret holding the RDS-managed master password."
}
