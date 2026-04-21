# GitHub Actions OIDC Federation
#
# The OIDC provider, deploy role, and deploy policy are managed in
# infra/bootstrap/ and applied once locally with admin credentials.
# They are referenced here as data sources so the rest of the stack
# (e.g. outputs.tf) can depend on them without owning their lifecycle.

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_role" "github_deploy" {
  name = "${var.project_name}-github-deploy"
}
