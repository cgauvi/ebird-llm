# eBird LLM — AWS Infrastructure

Terraform code to deploy the Streamlit app on **AWS ECS Fargate** behind a
public **Application Load Balancer**. API keys are stored as
**SSM SecureString** parameters and injected at container startup — never in
plain text in the task definition.

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

**Resources created:**

| File | Resources |
|---|---|
| `networking.tf` | VPC, 2 public subnets, IGW, ALB security group, ECS security group |
| `ecr_iam.tf` | ECR repository, ECS task execution role, ECS task role, SSM read policy |
| `ecs.tf` | SSM parameters, CloudWatch log group, ECS cluster, task definition, service |
| `alb.tf` | ALB, target group, HTTP listener |
| `outputs.tf` | `app_url`, `set_secrets_commands`, `docker_push_commands` |

---

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) ≥ 1.5
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with a profile that has sufficient IAM permissions
- [Docker](https://docs.docker.com/get-docker/) (for building and pushing the image)

---

## Deployment

### 1. Initialise Terraform

```bash
cd infra
terraform init
```

### 2. (Optional) Override defaults

Create a `terraform.tfvars` file:

```hcl
aws_region    = "us-east-1"
aws_profile   = "default"
project_name  = "ebird-llm"
environment   = "dev"
task_cpu      = 1024             # 1 vCPU
task_memory   = 2048             # MiB
desired_count = 1
```

### 3. Create infrastructure + ECR repository

```bash
terraform apply
```

This creates all resources including SSM parameters with `PLACEHOLDER` values.

### 4. Set real API key values in SSM

Copy the commands from the Terraform output and run them:

```bash
terraform output -raw set_secrets_commands
```

Which produces something like:

```bash
aws ssm put-parameter \
  --name "/ebird-llm-dev/EBIRD_API_KEY" \
  --value "<YOUR_EBIRD_API_KEY>" \
  --type SecureString --overwrite --region us-east-1

aws ssm put-parameter \
  --name "/ebird-llm-dev/HUGGINGFACE_API_TOKEN" \
  --value "<YOUR_HUGGINGFACE_API_TOKEN>" \
  --type SecureString --overwrite --region us-east-1
```

> The `lifecycle { ignore_changes = [value] }` block means Terraform will
> never revert values you set here.

### 5. Build and push the Docker image

Run the commands from the output (from the **project root**, not `infra/`):

```bash
cd ..
terraform -chdir=infra output -raw docker_push_commands
```

The commands authenticate Docker with ECR, build the image, push it, and
force a new ECS deployment to pull the latest tag.

### 6. Access the app

```bash
terraform output app_url
```

The Streamlit UI will be available at the printed URL over HTTP on port 80.

---

## Variables Reference

| Variable | Default | Description |
|---|---|---|
| `aws_region` | `us-east-1` | AWS region |
| `aws_profile` | `aws_perso_beneva` | AWS CLI named profile |
| `project_name` | `ebird-llm` | Prefix for all resource names |
| `environment` | `dev` | Deployment environment |
| `image_tag` | `latest` | ECR image tag to deploy |
| `task_cpu` | `1024` | Fargate CPU units (1024 = 1 vCPU) |
| `task_memory` | `2048` | Fargate memory (MiB) |
| `desired_count` | `1` | Number of running ECS tasks (set to `0` to pause) |
| `streamlit_port` | `8501` | Container port Streamlit listens on |

---

## Day-2 Operations

**Pause the app (stop billing for compute):**
```bash
terraform apply -var="desired_count=0"
```

**Resume:**
```bash
terraform apply -var="desired_count=1"
```

**Redeploy after a code change:**
```bash
# From project root
docker build -t <ecr_url>:latest .
docker push <ecr_url>:latest
aws ecs update-service --cluster ebird-llm-dev --service ebird-llm-dev \
  --force-new-deployment --region us-east-1
```

**View logs:**
```bash
aws logs tail /ecs/ebird-llm-dev --follow --region us-east-1
```

**Tear down all resources:**
```bash
terraform destroy
```

---

## Adding HTTPS

1. Request or import a certificate in [AWS Certificate Manager](https://console.aws.amazon.com/acm/).
2. Add an `aws_lb_listener` on port 443 with the ACM certificate ARN.
3. Change the port-80 listener's `default_action` to redirect to HTTPS.
4. Update the ALB security group to also allow port 443 inbound.

---

## Cost Estimate (dev defaults)

| Resource | Approx. monthly cost |
|---|---|
| Fargate task (1 vCPU / 2 GB, 24/7) | ~$35 |
| ALB | ~$18 |
| ECR storage (1 image ~1 GB) | ~$0.10 |
| CloudWatch Logs (light traffic) | < $1 |
| SSM parameters | Free tier |
| **Total** | **~$55/month** |

Set `desired_count = 0` when the app is not in use to eliminate Fargate costs.
