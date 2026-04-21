# eBird LLM — AWS Infrastructure

Terraform code to deploy the Streamlit app on **AWS ECS Fargate** behind a
public **Application Load Balancer**. Two fully isolated environments
(**dev** and **prod**) are managed from the same Terraform code using separate
state files and `.tfvars`. API keys are stored as **SSM SecureString**
parameters and injected at container startup — never in plain text in the task
definition.

> For local development and app documentation see the [root README](../README.md).

---

## Architecture

```
Internet
    │  HTTP :80
    ▼
┌───────────────────────────┐
│  Application Load Balancer │  (public, multi-AZ)
└────────────┬──────────────┘
             │ HTTP :8501
             ▼
┌───────────────────────────┐
│  ECS Fargate Task          │  (1 task, public subnet, no NAT gateway)
│  └── streamlit container  │
│       ├── EBIRD_API_KEY   │◄── SSM SecureString
│       └── HF_API_TOKEN    │◄── SSM SecureString
│                            │
│  Authentication:           │
│  └── Cognito User Pool   │  (sign-up / sign-in / email verification)
│                            │
│  Usage tracking:           │
│  ├── DynamoDB (usage)    │  (session + prompt counters per user/month)
│  └── DynamoDB (llm-calls)│  (per-call audit log with GSI on month)
│                            │
│  Model selection: Streamlit sidebar (runtime, per-session)
└───────────────────────────┘
             │ HTTPS egress
             ▼
    HuggingFace Inference API
    eBird API v2
```

**Resources created per environment:**

| File | Resources |
|---|---|
| `networking.tf` | VPC, 2 public subnets, IGW, ALB security group, ECS security group |
| `ecr_iam.tf` | ECR repository, ECS task execution role, ECS task role, SSM read policy |
| `ecs.tf` | SSM parameters (API keys + Cognito + DynamoDB prefix), CloudWatch log group, ECS cluster, task definition, service |
| `alb.tf` | ALB, target group, HTTP listener |
| `auth_usage.tf` | Cognito User Pool + app client, DynamoDB `usage` + `llm-calls` tables, IAM policy for task role |
| `oidc.tf` | Data sources referencing the bootstrap-managed OIDC provider and deploy role |
| `outputs.tf` | `app_url`, `cognito_user_pool_id`, `cognito_client_id`, `dynamodb_usage_table`, `dynamodb_llm_calls_table`, `set_secrets_commands`, `docker_push_commands`, `github_deploy_role_arn` |

All resources are namespaced by `ebird-llm-<environment>` (e.g. `ebird-llm-dev`,
`ebird-llm-prod`). The two environments use non-overlapping VPC CIDRs
(`10.0.0.0/16` for dev, `10.1.0.0/16` for prod).

---

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with a profile that has sufficient IAM permissions
- [Docker](https://docs.docker.com/get-docker/) (for building and pushing the image)

---

## First-time Setup (run once)

### Step 0 — Create the remote state bucket

The main module uses S3 + DynamoDB for remote state. These resources must exist
before the first `terraform init`. The `bootstrap/` sub-module creates them
with local state so they never depend on themselves.

```bash
cd infra/bootstrap
terraform init
terraform apply
# Outputs: state_bucket_name = "ebird-llm-tf-state-<account_id>"
```

The bucket name is already filled in `backend-dev.hcl` and `backend-prod.hcl`.
If you recreate the bootstrap in a different account, update the `bucket` value
in both files.

### Step 1 — Bootstrap the GitHub OIDC role (enables CI/CD)

The OIDC provider, deploy role, and deploy policy are managed in
`infra/bootstrap/` alongside the state bucket. They require admin credentials
and must exist before the CI/CD pipeline can authenticate. Run this once:

```bash
cd infra/bootstrap
terraform apply
# Outputs: github_deploy_role_arn = "arn:aws:iam::..."
```

Copy the role ARN into GitHub:
```bash
# GitHub repo → Settings → Secrets → Actions → New secret: AWS_DEPLOY_ROLE_ARN
```

This bootstrap is per AWS account and repository, not per environment.
In the current setup:

- `backend-dev.hcl` and `backend-prod.hcl` use the same S3 state bucket and
  DynamoDB lock table, with different state keys
- `develop`, `main`, and `master` all assume the same
  `ebird-llm-github-deploy` role
- Dev and prod are separated by different `.tfvars` values and different
  backend state files, not by separate GitHub OIDC roles

You do **not** need a second OIDC bootstrap just because you are deploying
prod. You only need a separate bootstrap if you later move prod to a different
AWS account or decide to use a distinct prod deploy role.

### If CI deploy fails with AccessDenied during terraform plan/apply

If the GitHub Actions deploy workflow starts but Terraform fails while reading
existing AWS resources, the `ebird-llm-github-deploy` role is missing refresh
permissions. This is an IAM policy issue, not a `workflow_run` issue.

Typical symptom:

- The deploy workflow runs after `Unit Tests` passes
- `aws-actions/configure-aws-credentials` succeeds
- `terraform init` succeeds
- `terraform plan` or `terraform apply` fails with `AccessDenied` on
  `Describe*`, `Get*`, or `List*` API calls

Minimum additional actions required for the current stack:

```text
elasticloadbalancing:DescribeListenerAttributes

cognito-idp:DescribeUserPool
cognito-idp:DescribeUserPoolClient
cognito-idp:GetUserPoolMfaConfig
cognito-idp:CreateUserPool
cognito-idp:UpdateUserPool
cognito-idp:DeleteUserPool
cognito-idp:CreateUserPoolClient
cognito-idp:UpdateUserPoolClient
cognito-idp:DeleteUserPoolClient
cognito-idp:ListTagsForResource
cognito-idp:TagResource
cognito-idp:UntagResource

dynamodb:CreateTable
dynamodb:DeleteTable
dynamodb:DescribeContinuousBackups
dynamodb:DescribeTable
dynamodb:UpdateTable
dynamodb:ListTagsOfResource
dynamodb:TagResource
dynamodb:UntagResource

ecr:ListTagsForResource

iam:GetOpenIDConnectProvider

ssm:DescribeParameters
```

Notes:

- `ssm:DescribeParameters` should be granted on `*`. AWS does not evaluate that
  action against individual parameter ARNs.
- The current SSM parameters are named with a leading slash, for example
  `/ebird-llm-dev/EBIRD_API_KEY`. The matching ARN pattern must therefore look
  like `arn:aws:ssm:*:<account-id>:parameter/ebird-llm-*`, not a pattern that
  assumes the slash is preserved after `parameter/`.
- If the deploy role cannot yet manage its own policy, run a one-time local
  Terraform apply with an admin-capable AWS identity targeting the OIDC role
  resources first.

If the deploy role policy is out of date, update it locally with admin credentials:

```bash
cd infra/bootstrap
terraform apply
```

After that bootstrap, normal CI/CD can update the rest of the stack.

---

## Manual Deployment

Use this when deploying outside of CI/CD (e.g. first full apply, debugging).

### Dev environment

```bash
cd infra
terraform init -backend-config=backend-dev.hcl
terraform apply -var-file=dev.tfvars
```

### Prod environment

```bash
cd infra
terraform init -backend-config=backend-prod.hcl -reconfigure
terraform apply -var-file=prod.tfvars
```

If prod has never been deployed before, this first full apply is the prod
bootstrap of the infrastructure itself. It creates the prod-namespaced
resources such as `ebird-llm-prod`, `ebird-llm-prod-users`, and the prod SSM
parameters. It does not require a separate OIDC bootstrap as long as the
shared GitHub deploy role already has the correct permissions.

> Always pass `-reconfigure` when switching between environments to avoid
> Terraform trying to migrate state between backends.

### Set real API key values in SSM

After the first apply, SSM parameters are created with `PLACEHOLDER` values.
Populate them before starting the ECS service:

```bash
terraform output -raw set_secrets_commands
```

Which produces:

```bash
aws ssm put-parameter \
  --name "/ebird-llm-dev/EBIRD_API_KEY" \
  --value "<YOUR_EBIRD_API_KEY>" \
  --type SecureString --overwrite --region us-east-2

aws ssm put-parameter \
  --name "/ebird-llm-dev/HUGGINGFACE_API_TOKEN" \
  --value "<YOUR_HUGGINGFACE_API_TOKEN>" \
  --type SecureString --overwrite --region us-east-2
```

> The `lifecycle { ignore_changes = [value] }` block means Terraform will
> never revert values you set manually.

### Build and push the Docker image (manual)

```bash
cd ..
terraform -chdir=infra output -raw docker_push_commands
```

The commands authenticate Docker with ECR, build the `runtime` stage, push it,
and force a new ECS deployment.

### Access the app

```bash
terraform output app_url
```

---

## CI/CD (GitHub Actions)

After the OIDC role is bootstrapped (Step 1 above), all deployments are
automated:

| Branch | Trigger | Deploys to |
|---|---|---|
| `develop` | push | `ebird-llm-dev` |
| `master` | push | `ebird-llm-prod` |

The deploy workflow (`.github/workflows/deploy.yml`) only runs after the
`Unit Tests` workflow succeeds. It:

1. Builds the `runtime` Docker stage and pushes to ECR tagged with the git SHA
2. Runs `terraform apply` with the environment-specific `-backend-config` and `-var-file`

---

## Variables Reference

| Variable | Default | Description |
|---|---|---|
| `aws_region` | `us-east-2` | AWS region |
| `aws_profile` | `aws_perso_beneva` | AWS CLI named profile (local use only) |
| `project_name` | `ebird-llm` | Prefix for all resource names |
| `environment` | `dev` | Deployment environment — set via `.tfvars` |
| `vpc_cidr` | `10.0.0.0/16` | VPC CIDR block — must differ between envs |
| `image_tag` | `latest` | ECR image tag to deploy — overridden by CI with git SHA |
| `task_cpu` | `1024` | Fargate CPU units (1024 = 1 vCPU) |
| `task_memory` | `2048` | Fargate memory (MiB) |
| `desired_count` | `1` | Number of running ECS tasks (set to `0` to pause) |
| `streamlit_port` | `8501` | Container port Streamlit listens on |

---

## Provider Lock File

The `.terraform.lock.hcl` file pins the exact provider versions and checksums
used by this module. It is committed to the repository so that every `terraform
init` uses the same provider build, regardless of when or where it runs.

**Generate / regenerate the lock file** (run once after adding or upgrading a provider):

```bash
cd infra
terraform init   # creates or updates .terraform.lock.hcl
git add .terraform.lock.hcl
```

To pre-populate checksums for all deployment platforms at once:

```bash
terraform providers lock \
  -platform=linux_amd64 \
  -platform=darwin_amd64 \
  -platform=darwin_arm64
```

**Recreate:** any `terraform init` that finds `.terraform.lock.hcl` will
automatically download the pinned provider versions. No extra flags needed.

---

## Day-2 Operations

**Pause the app (stop billing for compute):**
```bash
terraform apply -var-file=dev.tfvars -var="desired_count=0"
```

**Resume:**
```bash
terraform apply -var-file=dev.tfvars -var="desired_count=1"
```

**View logs:**
```bash
aws logs tail /ecs/ebird-llm-dev --follow --region us-east-2
# prod:
aws logs tail /ecs/ebird-llm-prod --follow --region us-east-2
```

**Tear down an environment:**
```bash
# Dev
terraform init -backend-config=backend-dev.hcl -reconfigure
terraform destroy -var-file=dev.tfvars

# Prod
terraform init -backend-config=backend-prod.hcl -reconfigure
terraform destroy -var-file=prod.tfvars
```

---

## Adding HTTPS

1. Request or import a certificate in [AWS Certificate Manager](https://console.aws.amazon.com/acm/).
2. Add an `aws_lb_listener` on port 443 with the ACM certificate ARN.
3. Change the port-80 listener's `default_action` to redirect to HTTPS.
4. Update the ALB security group to also allow port 443 inbound.

---

## Cost Estimate

| Resource | Dev (512 CPU / 1 GB) | Prod (1 vCPU / 2 GB) |
|---|---|---|
| Fargate task (24/7) | ~$9 | ~$35 |
| ALB | ~$18 | ~$18 |
| ECR storage (~1 GB) | ~$0.10 | ~$0.10 |
| CloudWatch Logs | < $1 | < $1 |
| SSM parameters | Free tier | Free tier |
| **Total** | **~$55/month** |

Set `desired_count = 0` when the app is not in use to eliminate Fargate costs.
