# ---------------------------------------------------------------------------
# Application Load Balancer — shared between all environments. Per-env stacks
# attach their own target group and a host-based listener rule onto the
# HTTPS listener exposed below.
# ---------------------------------------------------------------------------

resource "aws_lb" "app" {
  name               = local.prefix
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

# ---------------------------------------------------------------------------
# HTTP Listener (port 80) — redirects all traffic to HTTPS
# ---------------------------------------------------------------------------

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

# ---------------------------------------------------------------------------
# HTTPS Listener (port 443) — TLS termination. The default action returns
# 404 for unknown hostnames; per-env stacks add aws_lb_listener_rule
# resources with host-based conditions to route traffic to their target
# groups.
# ---------------------------------------------------------------------------

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.app.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Unknown host"
      status_code  = "404"
    }
  }
}
