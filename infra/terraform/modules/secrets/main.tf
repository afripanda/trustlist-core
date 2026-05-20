# AWS Secrets Manager secret containers for the credentials TrustList consumes
# (Stage 0 PRD §7g, issue 15). This module creates the named secrets only — the
# secret VALUES are populated out of band (`aws secretsmanager put-secret-value`)
# so that no credential is ever written to Terraform configuration or state.

variable "environment" {
  type = string
}

variable "secret_names" {
  type        = map(string)
  description = "Map of secret short-name => human-readable description."
}

variable "recovery_window_days" {
  type        = number
  default     = 7
  description = "Window during which a deleted secret can be restored."
}

resource "aws_secretsmanager_secret" "this" {
  for_each = var.secret_names

  name                    = "trustlist/${var.environment}/${each.key}"
  description             = each.value
  recovery_window_in_days = var.recovery_window_days
}

output "secret_arns" {
  value       = { for key, secret in aws_secretsmanager_secret.this : key => secret.arn }
  description = "Map of secret short-name => created secret ARN."
}
