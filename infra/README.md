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

Dev and prod share a single VPC and a single ALB; per-env traffic is routed
by host header on the HTTPS listener. The shared stack lives in
[`infra/shared/`](shared/) and has its own state file.

```
Internet
    │  HTTPS :443
    ▼
┌───────────────────────────────────────────────────┐
│  Shared ALB (infra/shared/, multi-AZ)              │
│  HTTPS listener with host-based rules:             │
│   ├── ebird-llm-dev.charlesgauvin.ca → dev TG     │
│   └── ebird-llm.charlesgauvin.ca     → prod TG    │
└───────────┬───────────────────────┬───────────────┘
            │ HTTP :8501             │ HTTP :8501
            ▼                        ▼
┌───────────────────────┐  ┌───────────────────────┐
│  ECS Fargate (dev)    │  │  ECS Fargate (prod)   │
│  streamlit container  │  │  streamlit container  │
└───────────────────────┘  └───────────────────────┘
            │                        │
            └──── HTTPS egress ──────┘
                       ▼
            HuggingFace Inference API
            eBird API v2
```

**Resources owned by the shared stack (`infra/shared/`, state key `ebird-llm/shared/terraform.tfstate`):**

| File | Resources |
|---|---|
| `networking.tf` | VPC, 2 public subnets, IGW, route table, ALB security group |
| `alb.tf` | ALB, HTTP listener (redirect to HTTPS), HTTPS listener (default 404) |
| `outputs.tf` | `vpc_id`, `public_subnet_ids`, `alb_arn`, `alb_dns_name`, `alb_zone_id`, `alb_security_group_id`, `https_listener_arn` |

**Resources created per environment (`infra/`, state key `ebird-llm/<env>/terraform.tfstate`):**

| File | Resources |
|---|---|
| `shared_state.tf` | `data.terraform_remote_state.shared` reading the shared stack outputs |
| `networking.tf` | ECS security group (in the shared VPC), allows shared ALB → tasks |
| `ecr_iam.tf` | ECR repository, ECS task execution role, ECS task role, SSM read policy |
| `ecs.tf` | SSM parameters, CloudWatch log group, ECS cluster, task definition, service (uses shared subnets) |
| `alb.tf` | Target group + `aws_lb_listener_rule` with `host_header = [var.app_hostname]` attached to the shared HTTPS listener |
| `auth_usage.tf` | Cognito User Pool + app client, DynamoDB `usage` + `llm-calls` tables, IAM policy for task role |
| `oidc.tf` | Data sources referencing the bootstrap-managed OIDC provider and deploy role |
| `outputs.tf` | `app_url`, `shared_alb_dns_name`, `cognito_*`, `dynamodb_*`, `set_secrets_commands`, `docker_push_commands`, `github_deploy_role_arn` |

Per-env resources are namespaced by `ebird-llm-<environment>` (e.g.
`ebird-llm-dev`, `ebird-llm-prod`). Both environments live in the
shared VPC (`10.0.0.0/16`); listener-rule priorities (`100` for prod,
`200` for dev) are derived from `var.environment` so the rules never
collide.

> **Tradeoff:** sharing a VPC and ALB across dev and prod removes
> environment isolation — a misconfigured SG or a noisy dev workload can
> affect prod. Acceptable for a hobby/portfolio project; not recommended
> for production at a company. To restore isolation, split the shared
> stack into per-env stacks (one ALB each) and roll back this consolidation.

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

The bucket name is derived from your AWS account ID
(`ebird-llm-tf-state-<account_id>`) and is passed to `terraform init` as
`-backend-config` CLI flags — see the "Manual Deployment" section below and
[.github/workflows/infra.yml](../.github/workflows/infra.yml) for the exact
invocation. No per-env backend file is committed.

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

- Both environments share the same S3 state bucket and DynamoDB lock table,
  with different state keys (`ebird-llm/dev/terraform.tfstate` vs
  `ebird-llm/prod/terraform.tfstate`), supplied via `-backend-config` CLI flags
- `develop`, `main`, and `master` all assume the same
  `ebird-llm-github-deploy` role
- Dev and prod are separated by different `.tfvars` values and different
  backend state files, not by separate GitHub OIDC roles

You do **not** need a second OIDC bootstrap just because you are deploying
prod. You only need a separate bootstrap if you later move prod to a different
AWS account or decide to use a distinct prod deploy role.
 

---

## Manual Deployment

Use this when deploying outside of CI/CD (e.g. first full apply, debugging).

Backend configuration is passed as `-backend-config` CLI flags rather than a
committed `.hcl` file, so export your AWS account ID once per shell:

```bash
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
```

### Shared stack (run before any per-env apply)

The shared stack owns the VPC and ALB that both environments attach to. It
must be applied first; per-env stacks read its outputs via
`data "terraform_remote_state" "shared"` and will fail to plan if the
shared state is missing.

```bash
export CERTIFICATE_ARN=xxxx # ACM cert covering both per-env hostnames
make init-shared
make plan-shared
make apply-shared
```

After apply, note the shared ALB's DNS name and create a CNAME record for
each hostname:

```
ebird-llm-dev.charlesgauvin.ca → <shared ALB DNS>
ebird-llm.charlesgauvin.ca     → <shared ALB DNS>
```

```bash
terraform -chdir=infra/shared output -raw alb_dns_name
```

### Dev environment

run from the root:

```
export CERTIFICATE_ARN=xxxx # set this to the valid certificate
make init ENV=dev
make plan ENV=dev
make apply ENV=dev
make deploy ENV=dev
```

### Prod environment

alternatively (simpler), run from the root:

```
export CERTIFICATE_ARN=xxxx # set this to the valid certificate
make init ENV=prod
make plan ENV=prod
make apply ENV=prod
make deploy ENV=prod
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
Populate them before starting the ECS service.

**Dev:**

Assuming local env var EBIRD_API_KEY and HUGGINGFACE_API_TOKEN contains the key values

```bash
aws ssm put-parameter \
  --name "/ebird-llm-dev/EBIRD_API_KEY" \
  --value $EBIRD_API_KEY  \
  --type SecureString --overwrite --region us-east-2



aws ssm put-parameter \
  --name "/ebird-llm-dev/HUGGINGFACE_API_TOKEN" \
  --value $HUGGINGFACE_API_TOKEN \
  --type SecureString --overwrite --region us-east-2
```

**Prod:**

```bash
aws ssm put-parameter \
  --name "/ebird-llm-prod/EBIRD_API_KEY" \
  --value $EBIRD_API_KEY \
  --type SecureString --overwrite --region us-east-2

aws ssm put-parameter \
  --name "/ebird-llm-prod/HUGGINGFACE_API_TOKEN" \
  --value $HUGGINGFACE_API_TOKEN \
  --type SecureString --overwrite --region us-east-2
```

Alternatively, let Terraform print the commands for the current workspace:

```bash
terraform output -raw set_secrets_commands
```

> The `lifecycle { ignore_changes = [value] }` block means Terraform will
> never revert values you set manually.

> Secrets are injected into the container at task startup. After updating an
> SSM value, force a new ECS deployment so running tasks pick up the change:
>
> ```bash
> # Dev
> aws ecs update-service --cluster ebird-llm-dev --service ebird-llm-dev \
>   --force-new-deployment --region us-east-2
>
> # Prod
> aws ecs update-service --cluster ebird-llm-prod --service ebird-llm-prod \
>   --force-new-deployment --region us-east-2
> ```

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
| `main` / `master` | push | `ebird-llm-prod` |

### Application deploy (`.github/workflows/deploy.yml`)

Runs only after the `Unit Tests` workflow succeeds. It:

1. Builds the `runtime` Docker stage and pushes to ECR with three tags:
   - `<7-char git SHA>` — immutable, for traceability
   - `<env>-latest` — env-scoped mutable pointer
   - `latest` — keeps the Terraform fallback tag resolvable
2. Registers a new ECS task definition revision pointing to the SHA-tagged image
3. Updates the ECS service to use the new revision

### Shared infrastructure (`.github/workflows/infra-shared.yml`)

Triggered on pushes and pull requests to `develop`, `main`, or `master`
whenever `infra/shared/**` changes. Plans on every event, applies on
push events through the same gated `terraform-apply` GitHub Environment
used by prod, so changes to the shared VPC/ALB pause for the same
manual review as a prod apply. The shared stack must be applied before
any per-env apply that depends on its outputs.

### Per-env infrastructure (`.github/workflows/infra.yml`)

Triggered on pushes and pull requests to `develop`, `main`, or `master`
whenever `infra/**` changes (excluding `infra/shared/**`, which is
handled by the workflow above). It plans unconditionally and applies on
push events, with the apply job gated by a GitHub Environment for
safety.

The `plan` job:

1. Picks the state key and tfvars file from the branch
   (`ebird-llm/dev/terraform.tfstate` + `dev.tfvars` for `develop`,
   `ebird-llm/prod/terraform.tfstate` + `prod.tfvars` for `main`/`master`).
   The bucket and lock table are shared; backend values are passed to
   `terraform init` as `-backend-config` CLI flags.
2. Reads the currently running image tag from the live ECS task definition
   so an infra-only apply does not accidentally roll back the running app
   version to the `latest` fallback in tfvars.
3. Runs a targeted `terraform apply` against `aws_iam_role_policy.github_deploy`
   so the deploy role can refresh state on the next plan (no-op once in sync).
4. Runs `terraform plan -detailed-exitcode -out=tfplan`, posts the plan to the
   workflow summary and (on PRs) as a comment, and uploads `tfplan` as an
   artifact.

The `apply` job runs on push events only and downloads the exact plan
artifact from the `plan` job, then runs `terraform apply tfplan`. No
`-auto-approve` is needed because a saved plan skips the prompt.

#### GitHub Environments required

The `apply` job routes to a different environment per branch:

```yaml
environment: ${{ github.ref_name == 'develop' && 'terraform-apply-dev' || 'terraform-apply' }}
```

Configure both in **Settings → Environments**:

| Environment | Required reviewers | Used by |
|---|---|---|
| `terraform-apply-dev` | none | pushes to `develop` (per-env infra.yml) — auto-applies |
| `terraform-apply` | you / your team | pushes to `main`/`master` (per-env infra.yml) AND any push touching `infra/shared/**` (infra-shared.yml) — pauses for manual approval |

If `terraform-apply-dev` does not exist, pushes to `develop` will fail with
an environment-not-found error. If the `terraform-apply` environment has no
reviewers configured, prod pushes will apply without human review.

#### Concurrency

One Terraform operation per branch at a time (`cancel-in-progress: false`),
so a running apply is never interrupted by a newer push.

---

## Variables Reference

| Variable | Default | Description |
|---|---|---|
| `aws_region` | `us-east-2` | AWS region |
| `aws_profile` | `aws_perso_beneva` | AWS CLI named profile (local use only) |
| `project_name` | `ebird-llm` | Prefix for all resource names |
| `environment` | `dev` | Deployment environment — set via `.tfvars` |
| `app_hostname` | — (required, per-env) | Public hostname for this env. Used as the host_header on the shared ALB and must be CNAMEd to the shared ALB DNS name. |
| `vpc_cidr` (shared stack only) | `10.0.0.0/16` | CIDR for the shared VPC |
| `image_tag` | `latest` | ECR image tag to deploy — overridden by CI with git SHA |
| `task_cpu` | `1024` | Fargate CPU units (1024 = 1 vCPU) |
| `task_memory` | `2048` | Fargate memory (MiB) |
| `desired_count` | `1` | Number of running ECS tasks (set to `0` to pause) |
| `streamlit_port` | `8501` | Container port Streamlit listens on |
| `enable_spot` | `false` | If `true`, prefer `FARGATE_SPOT` (weight 3) with on-demand `FARGATE` fallback (weight 1). Cheaper but interruptible. |
| `enable_scheduled_scaling` | `false` | If `true`, create Application Auto Scaling scheduled actions to scale the ECS service to zero off-hours and back up during business hours. |
| `scale_down_cron` | `cron(0 2 * * ? *)` | UTC cron for scale-to-zero. Default = 22:00 America/Montreal in EDT; drifts +1h in EST (winter). |
| `scale_up_cron` | `cron(0 12 * * ? *)` | UTC cron for scale-up. Default = 08:00 America/Montreal in EDT; drifts +1h in EST (winter). |
| `certificate_arn` | — (required) | ACM certificate ARN for the HTTPS listener. Injected from the `CERTIFICATE_ARN` GitHub secret in CI. |

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

**Tear down an environment (app infrastructure):**

Requires `AWS_ACCOUNT_ID` exported — see "Manual Deployment" above.

Per-env stacks must be destroyed before the shared stack, otherwise the
target group / listener rule will dangle on the shared ALB and the
shared `terraform destroy` will fail to remove the listener.

```bash
cd infra

# Dev
terraform init -reconfigure \
  -backend-config="bucket=ebird-llm-tf-state-${AWS_ACCOUNT_ID}" \
  -backend-config="key=ebird-llm/dev/terraform.tfstate" \
  -backend-config="dynamodb_table=ebird-llm-tf-locks" \
  -backend-config="encrypt=true"
terraform destroy -var-file=dev.tfvars

# Prod
terraform init -reconfigure \
  -backend-config="bucket=ebird-llm-tf-state-${AWS_ACCOUNT_ID}" \
  -backend-config="key=ebird-llm/prod/terraform.tfstate" \
  -backend-config="dynamodb_table=ebird-llm-tf-locks" \
  -backend-config="encrypt=true"
terraform destroy -var-file=prod.tfvars

# Shared (only after BOTH dev and prod have been destroyed)
make destroy-shared
```

The ECR repo has `force_delete = true`, so images will not block destroy.
SSM parameters, Cognito user pools, DynamoDB app tables, the per-env
target group / listener rule, and (after `destroy-shared`) the ALB and
VPC all destroy cleanly.

**Tear down the bootstrap (state bucket, lock table, OIDC provider, deploy role):**

Only do this if you want to fully remove the project from the AWS account.
It must be run **after** both `dev` and `prod` have been destroyed, since the
bootstrap owns the bucket that stores their state.

Two things block `terraform destroy` by default:

1. `aws_s3_bucket.tf_state` has `lifecycle { prevent_destroy = true }`. Remove
   that block in [bootstrap/main.tf](bootstrap/main.tf) before destroying.
2. The state bucket has versioning enabled and no `force_destroy`, so every
   object version and delete marker must be removed first.

```bash
cd infra/bootstrap

# 1. Empty all object versions and delete markers
BUCKET="ebird-llm-tf-state-${AWS_ACCOUNT_ID}"
aws s3api delete-objects --bucket "$BUCKET" \
  --delete "$(aws s3api list-object-versions --bucket "$BUCKET" \
    --query '{Objects: Versions[].{Key:Key,VersionId:VersionId}}')"
aws s3api delete-objects --bucket "$BUCKET" \
  --delete "$(aws s3api list-object-versions --bucket "$BUCKET" \
    --query '{Objects: DeleteMarkers[].{Key:Key,VersionId:VersionId}}')"

# 2. Remove the prevent_destroy lifecycle block on aws_s3_bucket.tf_state,
#    then destroy — bootstrap uses LOCAL state, so no backend init needed.
terraform destroy
```

Destroying the bootstrap also removes the `ebird-llm-github-deploy` role and
the GitHub OIDC provider, which will break any further CI/CD runs until
re-bootstrapped.

---

## Adding HTTPS

1. Request or import a certificate in [AWS Certificate Manager](https://console.aws.amazon.com/acm/).
2. Add an `aws_lb_listener` on port 443 with the ACM certificate ARN.
3. Change the port-80 listener's `default_action` to redirect to HTTPS.
4. Update the ALB security group to also allow port 443 inbound.

---

## Cost Estimate

Default tfvars enable `enable_spot` and `enable_scheduled_scaling`, so the
figures below assume Fargate Spot (~70% off) and a 14 h/day schedule. Dev
and prod share a single ALB (the largest fixed line item), so the ALB
charge is billed once across both environments rather than twice.

| Resource | Shared | Dev (256 CPU / 0.5 GB) | Prod (256 CPU / 0.5 GB) |
|---|---|---|---|
| ALB (shared between dev + prod) | ~$18 | — | — |
| Fargate task (24/7, Spot) | — | ~$3 | ~$3 |
| Fargate task (scheduled scaling 14h/day, Spot) | — | ~$1.60 | ~$1.60 |
| ECR storage (~1 GB) | — | ~$0.10 | ~$0.10 |
| CloudWatch Logs | — | < $1 | < $1 |
| SSM parameters | — | Free tier | Free tier |
| **Subtotal (defaults: Spot + scheduled scaling)** | **~$18** | **~$3** | **~$3** |

**Combined dev + prod with defaults: ~$24/month.**

For comparison, the previous architecture (one ALB per env, on-demand
Fargate, larger task specs) was **~$82/month**. The savings come from:

- Sharing the ALB across both envs (-$18)
- Dropping Fargate to the 256 CPU / 512 MB minimum (-$30 from prod, -$10 from dev)
- Fargate Spot pricing (-~70% on remaining compute)
- Scheduled scale-to-zero for ~10 h/day (-~40% on remaining compute)

The ALB (~$18/env, billed flat) is now the dominant cost — Fargate compute is
under $2/env/month with the defaults. To get below ~$40/month combined you
must remove an ALB (tear down one environment, or consolidate both behind a
single ALB with host-based routing).

### Ramp-up / ramp-down (scheduled scaling)

Set `enable_scheduled_scaling = true` to have the ECS service scale to 0 tasks
overnight and back to `desired_count` during the day. With the default crons
(`scale_up_cron = cron(0 12 * * ? *)`, `scale_down_cron = cron(0 2 * * ? *)`)
the service runs ~14 h/day (08:00–22:00 America/Montreal in EDT), which is
58% of 24/7 — hence the reduced Fargate line above.

Only the Fargate compute cost shrinks with scheduled scaling. The ALB is
billed whether or not a task is running, and ECR / CloudWatch / SSM are
effectively flat, so it is the dominant lever only when the task is large
(prod) or running in both environments at once.

For a full pause set `desired_count = 0` (eliminates Fargate entirely but
leaves the ALB). To also eliminate the ALB charge, `terraform destroy` the
environment — see [Day-2 Operations](#day-2-operations).
