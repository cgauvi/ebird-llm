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

# ---------------------------------------------------------------------------
# Cost optimisation
# ---------------------------------------------------------------------------

variable "enable_spot" {
  description = "Use FARGATE_SPOT capacity provider with on-demand fallback. Reduces compute costs ~70% but tasks may be interrupted with 2-min notice."
  type        = bool
  default     = false
}

variable "enable_scheduled_scaling" {
  description = "Scale the ECS service to zero during off-hours and back up at the start of business."
  type        = bool
  default     = false
}

variable "scale_down_cron" {
  description = "Cron expression (UTC) at which to scale the service to zero tasks. Only used when enable_scheduled_scaling = true."
  type        = string
  default     = "cron(0 23 * * ? *)" # 11 PM UTC
}

variable "scale_up_cron" {
  description = "Cron expression (UTC) at which to scale the service back to desired_count. Only used when enable_scheduled_scaling = true."
  type        = string
  default     = "cron(0 11 * * ? *)" # 11 AM UTC
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC (must not overlap between environments in the same account)"
  type        = string
  default     = "10.0.0.0/16"
}

# ---------------------------------------------------------------------------
# TLS / HTTPS
# ---------------------------------------------------------------------------

variable "certificate_arn" {
  description = "ARN of the ACM certificate to attach to the HTTPS listener (must be in the same region as the ALB)"
  type        = string
}


