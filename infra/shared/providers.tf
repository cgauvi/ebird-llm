terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Partial backend config — bucket, key, and dynamodb_table are supplied at
  # init time via -backend-config CLI flags. State key is
  # ebird-llm/shared/terraform.tfstate.
  backend "s3" {
    region = "us-east-2"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      Scope     = "shared"
      ManagedBy = "terraform"
    }
  }
}
