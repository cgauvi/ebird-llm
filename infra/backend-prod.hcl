# Prod environment backend config.
# Fill in `bucket` with the value output by `terraform apply` in infra/bootstrap/.
#
# Usage:
#   terraform init -backend-config=backend-prod.hcl

bucket         = "ebird-llm-tf-state-038083667790"
key            = "ebird-llm/prod/terraform.tfstate"
dynamodb_table = "ebird-llm-tf-locks"
encrypt        = true
