environment     = "prod"
vpc_cidr        = "10.1.0.0/16"
task_cpu        = 1024
task_memory     = 2048
desired_count   = 1
# image_tag is overridden at apply-time by infra.yml with the currently running
# tag read from ECS; this default is only used on the very first apply.
image_tag       = "latest"
# certificate_arn is injected from the CERTIFICATE_ARN GitHub secret in CI.
