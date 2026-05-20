variable "environment" {
  type    = string
  default = "prod"
}

variable "region" {
  type    = string
  default = "af-south-1"
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI / SSO profile for the trustlist-prod account."
  default     = "trustlist-prod"
}

variable "account_id" {
  type        = string
  description = "The 12-digit trustlist-prod account ID — keeps S3 bucket names globally unique."
}

variable "availability_zones" {
  type    = list(string)
  default = ["af-south-1a", "af-south-1b"]
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.medium"
}

variable "db_multi_az" {
  type    = bool
  default = true
}

variable "db_backup_retention_period" {
  type    = number
  default = 30
}

variable "db_deletion_protection" {
  type    = bool
  default = true
}

variable "db_skip_final_snapshot" {
  type    = bool
  default = false
}

variable "secret_names" {
  type = map(string)
  default = {
    "event-bus/redpanda"      = "RedPanda Cloud bootstrap URL and SASL credentials (ADR-0011)."
    "time-series/timescale"   = "Timescale Cloud connection string (ADR-0013)."
    "observability/honeycomb" = "Honeycomb ingest API key (ADR-0012)."
    "auth/clerk-foundation"   = "Clerk secret key for the trustlist-foundation application (ADR-0014)."
    "auth/clerk-commercial"   = "Clerk secret key for the trustlist-commercial application (ADR-0014)."
    "database/app-foundation" = "Login credentials for the trustlist_app role on the Foundation database."
    "database/app-commercial" = "Login credentials for the trustlist_app role on the commercial-entity database."
  }
}
