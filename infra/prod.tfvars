environment   = "prod"
vpc_cidr      = "10.1.0.0/16"
task_cpu      = 1024
task_memory   = 2048
desired_count = 1
# image_tag is overridden by CI with the git SHA — this default is a fallback only
image_tag     = "latest"
