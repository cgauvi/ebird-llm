environment              = "dev"
app_hostname             = "ebird-llm-dev.charlesgauvin.ca"
task_cpu                 = 256
task_memory              = 512
desired_count            = 1
image_tag                = "latest"
enable_spot              = true
enable_scheduled_scaling = true
# certificate_arn is injected from the CERTIFICATE_ARN GitHub secret in CI.
