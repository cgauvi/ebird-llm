variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-2"
}

variable "aws_profile" {
  description = "AWS CLI named profile to use for authentication (local use only)"
  type        = string
  default     = ""
}

variable "project_name" {
  description = "Name of the project (used for resource naming)"
  type        = string
  default     = "ebird-llm"
}

variable "environment" {
  description = "Deployment environment (e.g. dev, staging, prod)"
  type        = string
  default     = "dev"
}

# ---------------------------------------------------------------------------
# ECR
# ---------------------------------------------------------------------------

variable "image_tag" {
  description = "Tag of the Docker image to deploy"
  type        = string
  default     = "latest"
}

# ---------------------------------------------------------------------------
# ECS
# ---------------------------------------------------------------------------

variable "task_cpu" {
  description = "CPU units for the Fargate task (1024 = 1 vCPU)"
  type        = number
  default     = 1024
}

variable "task_memory" {
  description = "Memory (MiB) for the Fargate task"
  type        = number
  default     = 2048
}

variable "desired_count" {
  description = "Number of running ECS tasks (set to 0 to pause the app)"
  type        = number
  default     = 1
}

variable "streamlit_port" {
  description = "Port Streamlit listens on inside the container"
  type        = number
  default     = 8501
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC (must not overlap between environments in the same account)"
  type        = string
  default     = "10.0.0.0/16"
}

variable "enable_spot" {
  description = "If true, prefer FARGATE_SPOT (with FARGATE fallback) for ECS tasks. Cheaper but interruptible."
  type        = bool
  default     = true
}

variable "enable_scheduled_scaling" {
  description = "If true, create Application Auto Scaling scheduled actions to scale the ECS service down/up off-hours."
  type        = bool
  default     = true
}

variable "scale_down_cron" {
  description = "Cron expression (UTC) for scaling the ECS service to zero tasks. Default is 22:00 America/Montreal in EDT (02:00 UTC); will drift +1h in EST (winter). Only used when enable_scheduled_scaling is true."
  type        = string
  default     = "cron(0 2 * * ? *)"
}

variable "scale_up_cron" {
  description = "Cron expression (UTC) for scaling the ECS service back up. Default is 08:00 America/Montreal in EDT (12:00 UTC); will drift +1h in EST (winter). Only used when enable_scheduled_scaling is true."
  type        = string
  default     = "cron(0 12 * * ? *)"
}

# ---------------------------------------------------------------------------
# TLS / HTTPS
# ---------------------------------------------------------------------------

variable "certificate_arn" {
  description = "ARN of the ACM certificate to attach to the HTTPS listener (must be in the same region as the ALB). Injected from the CERTIFICATE_ARN GitHub secret in CI."
  type        = string
  sensitive   = true
}


