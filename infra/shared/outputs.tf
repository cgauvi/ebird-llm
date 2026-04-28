output "vpc_id" {
  description = "ID of the shared VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets where ECS tasks run"
  value       = aws_subnet.public[*].id
}

output "alb_arn" {
  description = "ARN of the shared ALB"
  value       = aws_lb.app.arn
}

output "alb_dns_name" {
  description = "DNS name of the shared ALB. Point each env hostname at this via CNAME."
  value       = aws_lb.app.dns_name
}

output "alb_zone_id" {
  description = "Route 53 hosted zone ID of the shared ALB (for ALIAS records)"
  value       = aws_lb.app.zone_id
}

output "alb_security_group_id" {
  description = "ID of the ALB security group — referenced by per-env ECS SG ingress rules"
  value       = aws_security_group.alb.id
}

output "https_listener_arn" {
  description = "ARN of the HTTPS listener — per-env stacks attach listener rules here"
  value       = aws_lb_listener.https.arn
}
