# An S3 bucket for TrustList evidence blobs. The Foundation evidence bucket is
# created from this module at Stage 0 (issue 09); the same module is reused per
# brand customer for the isolated per-customer audit buckets (ADR-0009).

variable "bucket_name" {
  type = string
}

variable "cold_tier_transition_days" {
  type        = number
  default     = 90
  description = "Age at which blobs move to the Glacier cold tier. Evidence is retained indefinitely."
}

resource "aws_s3_bucket" "this" {
  bucket = var.bucket_name

  tags = { Name = var.bucket_name }
}

# Versioning underpins the append-only evidence discipline (Stage 0 PRD §7c).
resource "aws_s3_bucket_versioning" "this" {
  bucket = aws_s3_bucket.this.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
    bucket_key_enabled = true
  }
}

# Evidence buckets are never public — isolation is enforced by IAM alone.
resource "aws_s3_bucket_public_access_block" "this" {
  bucket = aws_s3_bucket.this.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Blobs older than the transition window move to cold storage; evidence is
# retained indefinitely, so there is no expiration rule.
resource "aws_s3_bucket_lifecycle_configuration" "this" {
  bucket = aws_s3_bucket.this.id

  rule {
    id     = "cold-tier-transition"
    status = "Enabled"

    filter {}

    transition {
      days          = var.cold_tier_transition_days
      storage_class = "GLACIER"
    }

    noncurrent_version_transition {
      noncurrent_days = var.cold_tier_transition_days
      storage_class   = "GLACIER"
    }
  }
}

output "bucket_id" {
  value = aws_s3_bucket.this.id
}

output "bucket_arn" {
  value = aws_s3_bucket.this.arn
}
