# GitHub Actions OIDC Federation
#
# The OIDC provider, deploy role, and deploy policy are managed in
# infra/bootstrap/ and applied once locally with admin credentials.
# The deploy role is looked up here as a data source so outputs.tf
# can reference its ARN without owning the resource lifecycle.

data "aws_iam_role" "github_deploy" {
  name = "${var.project_name}-github-deploy"
}
