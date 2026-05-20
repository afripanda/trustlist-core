# Network foundation for one TrustList environment: a dedicated VPC with private
# subnets for the canonical-store databases. Stage 0 PRD §7c.

variable "environment" {
  type        = string
  description = "Environment name, e.g. \"dev\" or \"prod\"."
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "availability_zones" {
  type        = list(string)
  description = "AZs for the private database subnets — at least two, for RDS Multi-AZ."
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "trustlist-${var.environment}" }
}

# Private subnets — one per AZ. The canonical-store databases live here and are
# not internet-reachable. Public subnets, an internet gateway and NAT are added
# when application compute lands in a later stage.
resource "aws_subnet" "private" {
  count             = length(var.availability_zones)
  vpc_id            = aws_vpc.this.id
  availability_zone = var.availability_zones[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 4, count.index)

  tags = { Name = "trustlist-${var.environment}-private-${count.index}" }
}

resource "aws_db_subnet_group" "this" {
  name       = "trustlist-${var.environment}"
  subnet_ids = aws_subnet.private[*].id
}

# The databases accept connections only from within the VPC. Application
# compute, when it lands, gets its own security group referenced here.
resource "aws_security_group" "database" {
  name        = "trustlist-${var.environment}-database"
  description = "Canonical-store database access — VPC-internal only."
  vpc_id      = aws_vpc.this.id

  ingress {
    description = "PostgreSQL from within the VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "trustlist-${var.environment}-database" }
}

output "vpc_id" {
  value = aws_vpc.this.id
}

output "db_subnet_group_name" {
  value = aws_db_subnet_group.this.name
}

output "database_security_group_id" {
  value = aws_security_group.database.id
}
