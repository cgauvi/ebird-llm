# ---------------------------------------------------------------------------
# SSM Parameter Store — API secrets
#
# Terraform creates the parameters with a placeholder value.
# Populate real values BEFORE the ECS service starts tasks:
#
#   aws ssm put-parameter \
#     --name "/<prefix>/EBIRD_API_KEY" \
#     --value "<your key>" --type SecureString --overwrite
#
# The lifecycle block prevents Terraform from reverting values you set manually.
# ---------------------------------------------------------------------------

resource "aws_ssm_parameter" "ebird_api_key" {
  name  = "/${local.prefix}/EBIRD_API_KEY"
  type  = "SecureString"
  value = "PLACEHOLDER"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "hf_api_token" {
  name  = "/${local.prefix}/HUGGINGFACE_API_TOKEN"
  type  = "SecureString"
  value = "PLACEHOLDER"

  lifecycle {
    ignore_changes = [value]
  }
}

# Cognito parameters (auto-populated by Terraform)
resource "aws_ssm_parameter" "cognito_user_pool_id" {
  name  = "/${local.prefix}/COGNITO_USER_POOL_ID"
  type  = "String"
  value = aws_cognito_user_pool.main.id
}

resource "aws_ssm_parameter" "cognito_client_id" {
  name  = "/${local.prefix}/COGNITO_CLIENT_ID"
  type  = "String"
  value = aws_cognito_user_pool_client.streamlit.id
}

resource "aws_ssm_parameter" "dynamodb_table_prefix" {
  name  = "/${local.prefix}/DYNAMODB_TABLE_PREFIX"
  type  = "String"
  value = local.prefix
}

# ---------------------------------------------------------------------------
# CloudWatch Log Group
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${local.prefix}"
  retention_in_days = 7
}

# ---------------------------------------------------------------------------
# ECS Cluster
# ---------------------------------------------------------------------------

resource "aws_ecs_cluster" "main" {
  name = local.prefix
}

# Associate both FARGATE and FARGATE_SPOT with the cluster so the service can
# switch between them without recreating the cluster.
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]

  default_capacity_provider_strategy {
    capacity_provider = var.enable_spot ? "FARGATE_SPOT" : "FARGATE"
    weight            = 1
    base              = 1
  }
}

# ---------------------------------------------------------------------------
# ECS Task Definition
# ---------------------------------------------------------------------------

resource "aws_ecs_task_definition" "app" {
  family                   = local.prefix
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "streamlit"
      image = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

      portMappings = [
        {
          containerPort = var.streamlit_port
          protocol      = "tcp"
        }
      ]

      # Secrets injected from SSM at container startup (never in plain text in task def)
      secrets = [
        { name = "EBIRD_API_KEY", valueFrom = aws_ssm_parameter.ebird_api_key.arn },
        { name = "HUGGINGFACE_API_TOKEN", valueFrom = aws_ssm_parameter.hf_api_token.arn }
      ]

      # Non-secret configuration from SSM
      environment = [
        { name = "COGNITO_USER_POOL_ID", value = aws_cognito_user_pool.main.id },
        { name = "COGNITO_CLIENT_ID", value = aws_cognito_user_pool_client.streamlit.id },
        { name = "DYNAMODB_TABLE_PREFIX", value = local.prefix },
        { name = "AWS_REGION", value = var.aws_region },
        { name = "APP_ENV", value = var.environment },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.ecs.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "streamlit"
        }
      }

      healthCheck = {
        command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:${var.streamlit_port}/_stcore/health')\" || exit 1"]
        interval    = 30
        timeout     = 10
        retries     = 3
        startPeriod = 60
      }
    }
  ])
}

# ---------------------------------------------------------------------------
# ECS Service
# ---------------------------------------------------------------------------

resource "aws_ecs_service" "app" {
  name            = local.prefix
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.desired_count

  # Use capacity_provider_strategy instead of launch_type so we can mix
  # FARGATE_SPOT (cheap, interruptible) with on-demand FARGATE (fallback).
  # NOTE: switching an existing service from launch_type to
  # capacity_provider_strategy requires a service replacement:
  #   terraform apply -replace=aws_ecs_service.app
  dynamic "capacity_provider_strategy" {
    # Spot enabled: prefer FARGATE_SPOT (weight 3) with FARGATE fallback (weight 1).
    # Spot disabled: use plain FARGATE only.
    for_each = var.enable_spot ? [
      { provider = "FARGATE_SPOT", weight = 3, base = 1 },
      { provider = "FARGATE", weight = 1, base = 0 },
    ] : [{ provider = "FARGATE", weight = 1, base = 1 }]

    content {
      capacity_provider = capacity_provider_strategy.value.provider
      weight            = capacity_provider_strategy.value.weight
      base              = capacity_provider_strategy.value.base
    }
  }

  network_configuration {
    subnets         = data.terraform_remote_state.shared.outputs.public_subnet_ids
    security_groups = [aws_security_group.ecs.id]
    # assign_public_ip allows ECR/HuggingFace API egress without a NAT gateway
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "streamlit"
    container_port   = var.streamlit_port
  }

  # The listener rule must exist before the service starts registering targets,
  # otherwise the ALB has no rule pointing at this target group.
  depends_on = [aws_lb_listener_rule.app]
}

# ---------------------------------------------------------------------------
# Scheduled scale-to-zero (off-hours cost saving)
# ---------------------------------------------------------------------------
# Set enable_scheduled_scaling = true in tfvars to activate.
# Cron expressions are in UTC; adjust scale_down_cron / scale_up_cron as needed.

resource "aws_appautoscaling_target" "ecs" {
  count              = var.enable_scheduled_scaling ? 1 : 0
  service_namespace  = "ecs"
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  min_capacity       = 0
  max_capacity       = max(var.desired_count, 1)
}

# Scale down to zero tasks (off-hours).
resource "aws_appautoscaling_scheduled_action" "scale_down" {
  count              = var.enable_scheduled_scaling ? 1 : 0
  name               = "${local.prefix}-scale-down"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.ecs[0].resource_id
  scalable_dimension = "ecs:service:DesiredCount"
  schedule           = var.scale_down_cron

  scalable_target_action {
    min_capacity = 0
    max_capacity = 0
  }
}

# Scale back up at the start of business.
resource "aws_appautoscaling_scheduled_action" "scale_up" {
  count              = var.enable_scheduled_scaling ? 1 : 0
  name               = "${local.prefix}-scale-up"
  service_namespace  = "ecs"
  resource_id        = aws_appautoscaling_target.ecs[0].resource_id
  scalable_dimension = "ecs:service:DesiredCount"
  schedule           = var.scale_up_cron

  scalable_target_action {
    min_capacity = 1
    max_capacity = max(var.desired_count, 1)
  }
}
