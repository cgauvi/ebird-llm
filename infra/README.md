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
│
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
| `ecs.tf` | SSM parameters, CloudWatch log group, ECS cluster, task definition, service |
| `alb.tf` | ALB, target group, HTTP listener |
| `oidc.tf` | GitHub Actions OIDC provider, deploy IAM role + policy |
| `outputs.tf` | `app_url`, `set_secrets_commands`, `docker_push_commands`, `github_deploy_role_arn` |

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

The OIDC role in `oidc.tf` allows GitHub Actions to deploy without storing
long-lived AWS credentials. Apply it once from local credentials before the
pipeline is active:

```bash
cd infra
terraform init -backend-config=backend-dev.hcl
terraform apply -var-file=dev.tfvars \
  -target=aws_iam_openid_connect_provider.github \
  -target=aws_iam_role.github_deploy \
  -target=aws_iam_role_policy.github_deploy
```

Copy the role ARN into GitHub:
```bash
terraform output github_deploy_role_arn
# → GitHub repo → Settings → Secrets → Actions → New secret: AWS_DEPLOY_ROLE_ARN
```

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
| `github_repo` | `cgauvi/ebird-llm` | GitHub repo for OIDC trust policy |

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
| SSM parameters | Free tier |
| **Total** | **~$55/month** |

Set `desired_count = 0` when the app is not in use to eliminate Fargate costs.
