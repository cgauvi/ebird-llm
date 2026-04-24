# Usage:
#   make init ENV=dev
#   make plan ENV=dev
#   make apply ENV=dev
#   make deploy ENV=dev
#   make deploy ENV=dev IMAGE_TAG=abc1234   # redeploy existing image, skip build

ENV        ?= dev
AWS_REGION ?= us-east-2

# Derive account ID and bucket name from current AWS credentials
ACCOUNT_ID  = $(shell aws sts get-caller-identity --query Account --output text)
BUCKET      = ebird-llm-tf-state-$(ACCOUNT_ID)
CLUSTER     = ebird-llm-$(ENV)
ECR_REPO    = $(ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com/ebird-llm-$(ENV)
IMAGE_TAG  ?= $(shell git rev-parse --short HEAD)

TF          = terraform -chdir=infra

TF_BACKEND  = -backend-config="bucket=$(BUCKET)" \
              -backend-config="key=ebird-llm/$(ENV)/terraform.tfstate" \
              -backend-config="dynamodb_table=ebird-llm-tf-locks" \
              -backend-config="encrypt=true"

TF_VARS     = -var-file="$(ENV).tfvars" \
              -var "certificate_arn=$(CERTIFICATE_ARN)" \
              -var "image_tag=$(IMAGE_TAG)"

.PHONY: init plan apply

init:
	$(TF) init -reconfigure $(TF_BACKEND)

plan: init
	$(TF) plan $(TF_VARS) -out=tfplan

apply:
	$(TF) apply tfplan && rm -f tfplan

login:
	aws ecr get-login-password --region $(AWS_REGION) \
	  | docker login --username AWS --password-stdin $(ECR_REPO)

deploy: login
ifdef IMAGE_TAG
	@echo "Skipping build — deploying existing image $(ECR_REPO):$(IMAGE_TAG)"
else
	docker build --target runtime -t $(ECR_REPO):$(IMAGE_TAG) .
	docker push $(ECR_REPO):$(IMAGE_TAG)
	docker tag  $(ECR_REPO):$(IMAGE_TAG) $(ECR_REPO):$(ENV)-latest
	docker push $(ECR_REPO):$(ENV)-latest
endif
	@NEW_DEF=$$(aws ecs describe-task-definition --task-definition $(CLUSTER) \
	    --query taskDefinition --output json \
	    | jq 'del(.taskDefinitionArn,.revision,.status,.requiresAttributes,.compatibilities,.registeredAt,.registeredBy) \
	           | .containerDefinitions[0].image = "$(ECR_REPO):$(IMAGE_TAG)"'); \
	 NEW_ARN=$$(aws ecs register-task-definition --cli-input-json "$$NEW_DEF" \
	    --query taskDefinition.taskDefinitionArn --output text); \
	 aws ecs update-service --cluster $(CLUSTER) --service $(CLUSTER) \
	    --task-definition $$NEW_ARN --region $(AWS_REGION)
	@echo "Deployed $(ECR_REPO):$(IMAGE_TAG)"
