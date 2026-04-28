# ---------------------------------------------------------------------------
# Target Group — lives in the shared VPC. The ALB itself and its HTTPS
# listener are owned by infra/shared/; this env attaches a listener rule
# to that shared listener with a host_header condition.
# ---------------------------------------------------------------------------

resource "aws_lb_target_group" "app" {
  name        = local.prefix
  port        = var.streamlit_port
  protocol    = "HTTP"
  vpc_id      = data.terraform_remote_state.shared.outputs.vpc_id
  target_type = "ip" # required for Fargate (awsvpc network mode)

  deregistration_delay = 30

  health_check {
    path                = "/_stcore/health"
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 10
    interval            = 30
    matcher             = "200"
  }
}

# ---------------------------------------------------------------------------
# Listener rule — forwards traffic for var.app_hostname to this env's
# target group. Priority is derived from the env name so dev and prod
# never collide on the same shared listener.
# ---------------------------------------------------------------------------

resource "aws_lb_listener_rule" "app" {
  listener_arn = data.terraform_remote_state.shared.outputs.https_listener_arn
  priority     = var.environment == "prod" ? 100 : 200

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }

  condition {
    host_header {
      values = [var.app_hostname]
    }
  }
}
