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
        { name = "EBIRD_API_KEY",          valueFrom = aws_ssm_parameter.ebird_api_key.arn },
        { name = "HUGGINGFACE_API_TOKEN",   valueFrom = aws_ssm_parameter.hf_api_token.arn }
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
        command     = ["CMD-SHELL", "curl -sf http://localhost:${var.streamlit_port}/_stcore/health || exit 1"]
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
  launch_type     = "FARGATE"
  desired_count   = var.desired_count

  network_configuration {
    subnets         = aws_subnet.public[*].id
    security_groups = [aws_security_group.ecs.id]
    # assign_public_ip allows ECR/HuggingFace API egress without a NAT gateway
    assign_public_ip = true
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "streamlit"
    container_port   = var.streamlit_port
  }

  depends_on = [aws_lb_listener.http]
}
