# ---------------------------------------------------------------------------
# Application Load Balancer
# ---------------------------------------------------------------------------

resource "aws_lb" "app" {
  name               = local.prefix
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

# ---------------------------------------------------------------------------
# Target Group
# ---------------------------------------------------------------------------

resource "aws_lb_target_group" "app" {
  name        = local.prefix
  port        = var.streamlit_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # required for Fargate (awsvpc network mode)

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
# HTTP Listener (port 80)
# To enable HTTPS: add an ACM certificate, create an aws_lb_listener on 443,
# and redirect port 80 to 443.
# ---------------------------------------------------------------------------

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.app.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
