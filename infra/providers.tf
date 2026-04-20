terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial backend config — bucket, key, and dynamodb_table are supplied at
  # init time via -backend-config (see backend-dev.hcl / backend-prod.hcl).
  # Region is fixed here to avoid a "Missing region" error on plain `terraform init`.
  backend "s3" {
    region = "us-east-2"
  }
}

provider "aws" {
  region = var.aws_region
  # profile is intentionally omitted: in CI, credentials are supplied via
  # environment variables (AWS_ACCESS_KEY_ID / OIDC). For local runs, set
  # AWS_PROFILE in your shell or pass -var="aws_profile=myprofile" and
  # uncomment the line below.
  # profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
