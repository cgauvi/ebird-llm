variable "aws_region" {
  description = "AWS region to deploy resources"
  type        = string
  default     = "us-east-2"
}

variable "aws_profile" {
  description = "AWS CLI named profile to use for authentication"
  type        = string
  default     = "default"
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


