# ---------------------------------------------------------------------------
# ECS Security Group — lives in the shared VPC, allows the shared ALB to
# reach this env's tasks. The VPC, subnets, IGW, route table, and ALB SG
# are owned by infra/shared/.
# ---------------------------------------------------------------------------

resource "aws_security_group" "ecs" {
  name        = "${local.prefix}-ecs-sg"
  description = "Allow Streamlit traffic from the shared ALB only; allow all egress (HF API calls)"
  vpc_id      = data.terraform_remote_state.shared.outputs.vpc_id

  ingress {
    description     = "Streamlit from shared ALB"
    from_port       = var.streamlit_port
    to_port         = var.streamlit_port
    protocol        = "tcp"
    security_groups = [data.terraform_remote_state.shared.outputs.alb_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
