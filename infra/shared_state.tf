# Read VPC/subnets/ALB outputs from the shared stack so the per-env stack can
# attach an ECS service + listener rule onto resources it does not own.
#
# The shared stack lives in infra/shared/ and uses the state key
# ebird-llm/shared/terraform.tfstate within the same backend bucket.
data "terraform_remote_state" "shared" {
  backend = "s3"

  config = {
    bucket = "${var.project_name}-tf-state-${data.aws_caller_identity.current.account_id}"
    key    = "${var.project_name}/shared/terraform.tfstate"
    region = var.aws_region
  }
}
