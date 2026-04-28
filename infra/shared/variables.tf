variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-2"
}

variable "project_name" {
  description = "Name of the project (used for resource naming)"
  type        = string
  default     = "ebird-llm"
}

variable "vpc_cidr" {
  description = "CIDR block for the shared VPC. Both dev and prod ECS tasks run in subnets of this VPC."
  type        = string
  default     = "10.0.0.0/16"
}

variable "certificate_arn" {
  description = "ARN of the ACM certificate to attach to the HTTPS listener (must cover all per-env hostnames). Injected from the CERTIFICATE_ARN GitHub secret in CI."
  type        = string
  sensitive   = true
}
