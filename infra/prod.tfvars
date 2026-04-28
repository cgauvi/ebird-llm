environment   = "prod"
app_hostname  = "ebird-llm.charlesgauvin.ca"
task_cpu      = 256
task_memory   = 512
desired_count = 1
# image_tag is overridden at apply-time by infra.yml with the currently running
# tag read from ECS; this default is only used on the very first apply.
image_tag                = "latest"
enable_spot              = true
enable_scheduled_scaling = true
# certificate_arn is injected from the CERTIFICATE_ARN GitHub secret in CI.
