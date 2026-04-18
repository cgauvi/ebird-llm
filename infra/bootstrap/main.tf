# Bootstrap — creates the S3 bucket and DynamoDB table used by all Terraform
# workspaces in this project as a remote state backend.
#
# This module uses LOCAL state intentionally — it only needs to be applied once
# with local credentials and must itself never be stored in the remote backend
# it is creating.
#
# Usage (one-time, with local AWS credentials):
#   cd infra/bootstrap
#   terraform init
#   terraform apply
#
# After apply, copy the outputs into infra/backend-dev.hcl and
# infra/backend-prod.hcl before running any other terraform commands.

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region where the state bucket and lock table will be created"
  type        = string
  default     = "us-east-2"
}

variable "project_name" {
  description = "Project name — used as a prefix for resource names"
  type        = string
  default     = "ebird-llm"
}

# ---------------------------------------------------------------------------
# Account ID — used to ensure a globally unique S3 bucket name
# ---------------------------------------------------------------------------

data "aws_caller_identity" "current" {}

locals {
  bucket_name = "${var.project_name}-tf-state-${data.aws_caller_identity.current.account_id}"
  table_name  = "${var.project_name}-tf-locks"
}

# ---------------------------------------------------------------------------
# S3 Bucket — Terraform state storage
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "tf_state" {
  bucket = local.bucket_name

  # Prevent accidental deletion of state
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ---------------------------------------------------------------------------
# DynamoDB Table — state lock to prevent concurrent applies
# ---------------------------------------------------------------------------

resource "aws_dynamodb_table" "tf_locks" {
  name         = local.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }
}

# ---------------------------------------------------------------------------
# Outputs — copy these into infra/backend-dev.hcl and infra/backend-prod.hcl
# ---------------------------------------------------------------------------

output "state_bucket_name" {
  description = "S3 bucket name for Terraform state — use as 'bucket' in backend config"
  value       = aws_s3_bucket.tf_state.id
}

output "dynamodb_table_name" {
  description = "DynamoDB table name for state locking — use as 'dynamodb_table' in backend config"
  value       = aws_dynamodb_table.tf_locks.name
}

output "aws_region" {
  description = "Region where resources were created"
  value       = var.aws_region
}
