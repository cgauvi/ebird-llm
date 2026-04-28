output "app_url" {
  description = "Public URL of the Streamlit app (resolves once the hostname's CNAME is pointed at shared_alb_dns_name)"
  value       = "https://${var.app_hostname}"
}

output "shared_alb_dns_name" {
  description = "DNS name of the shared ALB. Create a CNAME record at var.app_hostname pointing here."
  value       = data.terraform_remote_state.shared.outputs.alb_dns_name
}

output "ecr_repository_url" {
  description = "Full URI of the ECR repository"
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Name of the ECS service"
  value       = aws_ecs_service.app.name
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group for container output"
  value       = aws_cloudwatch_log_group.ecs.name
}

output "ssm_parameter_names" {
  description = "SSM parameters to populate with real values before deploying"
  value = {
    ebird_api_key = aws_ssm_parameter.ebird_api_key.name
    hf_api_token  = aws_ssm_parameter.hf_api_token.name
  }
}

output "set_secrets_commands" {
  description = "Commands to populate SSM parameters with real API keys"
  value       = <<-EOT
    aws ssm put-parameter \
      --name "${aws_ssm_parameter.ebird_api_key.name}" \
      --value "<YOUR_EBIRD_API_KEY>" \
      --type SecureString --overwrite --region ${var.aws_region}

    aws ssm put-parameter \
      --name "${aws_ssm_parameter.hf_api_token.name}" \
      --value "<YOUR_HUGGINGFACE_API_TOKEN>" \
      --type SecureString --overwrite --region ${var.aws_region}
  EOT
}

output "docker_push_commands" {
  description = "Commands to build and push the Docker image to ECR"
  value       = <<-EOT
    # 1. Authenticate Docker with ECR
    aws ecr get-login-password --region ${var.aws_region} \
      | docker login --username AWS --password-stdin ${aws_ecr_repository.app.repository_url}

    # 2. Build (from project root) and push
    docker build --target runtime -t ${aws_ecr_repository.app.repository_url}:${var.image_tag} .
    docker push ${aws_ecr_repository.app.repository_url}:${var.image_tag}

    # 3. Force a new ECS deployment to pull the latest image
    aws ecs update-service \
      --cluster ${aws_ecs_cluster.main.name} \
      --service ${aws_ecs_service.app.name} \
      --force-new-deployment \
      --region ${var.aws_region}
  EOT
}

output "github_deploy_role_arn" {
  description = "ARN of the IAM role assumed by GitHub Actions via OIDC — copy into GitHub secret AWS_DEPLOY_ROLE_ARN"
  value       = data.aws_iam_role.github_deploy.arn
}

# ---------------------------------------------------------------------------
# Auth & Usage
# ---------------------------------------------------------------------------

output "cognito_user_pool_id" {
  description = "Cognito User Pool ID (set as COGNITO_USER_POOL_ID env-var for local dev)"
  value       = aws_cognito_user_pool.main.id
}

output "cognito_client_id" {
  description = "Cognito App Client ID (set as COGNITO_CLIENT_ID env-var for local dev)"
  value       = aws_cognito_user_pool_client.streamlit.id
}

output "dynamodb_usage_table" {
  description = "DynamoDB table for monthly usage counters"
  value       = aws_dynamodb_table.usage.name
}

output "dynamodb_llm_calls_table" {
  description = "DynamoDB table for LLM call audit log"
  value       = aws_dynamodb_table.llm_calls.name
}
