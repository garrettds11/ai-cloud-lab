output "instance_id" {
  description = "EC2 instance ID."
  value       = aws_instance.ai_lab.id
}

output "private_ip" {
  description = "EC2 private IP address."
  value       = aws_instance.ai_lab.private_ip
}

output "public_ip" {
  description = "EC2 public IP address used for outbound Internet connectivity."
  value       = aws_instance.ai_lab.public_ip
}

output "ollama_model" {
  description = "Model automatically installed in Ollama."
  value       = var.ollama_model
}

output "open_webui_local_url" {
  description = "Local browser URL after starting an SSH or SSM tunnel."
  value       = "http://localhost:${var.open_webui_host_port}"
}

output "open_webui_admin_password_secret_arn" {
  description = "Pre-created Secrets Manager ARN used by Open WebUI bootstrap."
  value       = var.open_webui_admin_password_secret_arn
}

output "open_webui_domain_url" {
  description = "Public HTTPS URL when domain access is enabled."
  value       = var.enable_domain_access ? "https://${var.domain_name}" : null
}

output "open_webui_alb_dns_name" {
  description = "ALB DNS name when domain access is enabled."
  value       = var.enable_domain_access ? aws_lb.domain["domain"].dns_name : null
}

output "open_webui_target_group_arn" {
  description = "ALB target group ARN for health checks when domain access is enabled."
  value       = var.enable_domain_access ? aws_lb_target_group.domain["domain"].arn : null
}

output "ssh_tunnel_command" {
  description = "SSH tunnel command when SSH is enabled. Otherwise use the SSM port-forwarding output."

  value = var.enable_ssh ? "ssh -i <path-to-key.pem> -L ${var.open_webui_host_port}:localhost:${var.open_webui_host_port} ubuntu@${aws_instance.ai_lab.public_ip}" : "SSH is disabled. Use ssm_open_webui_port_forward_command instead."
}

output "ssm_open_webui_port_forward_command" {
  description = "Windows PowerShell command to create a private SSM tunnel to Open WebUI."

  value = <<-EOT
    aws ssm start-session --target ${aws_instance.ai_lab.id} --document-name AWS-StartPortForwardingSession --parameters portNumber="${var.open_webui_host_port}",localPortNumber="${var.open_webui_host_port}" --region ${var.aws_region}${local.aws_cli_profile_arg}
  EOT
}

output "ssm_shell_command" {
  description = "Open a command-line SSM session."

  value = <<-EOT
    aws ssm start-session --target ${aws_instance.ai_lab.id} --region ${var.aws_region}${local.aws_cli_profile_arg}
  EOT
}

output "cognito_user_pool_id" {
  description = "Cognito user pool ID when enable_cognito is true."
  value       = var.enable_cognito ? aws_cognito_user_pool.lab["domain"].id : null
}

output "cognito_hosted_login_domain" {
  description = "Cognito hosted sign-in domain when enable_cognito is true."
  value       = var.enable_cognito ? "${var.cognito_domain_prefix}.auth.${var.aws_region}.amazoncognito.com" : null
}

output "cognito_user_emails" {
  description = "Emails of the Cognito users Terraform creates. Used by scripts/set-cognito-passwords.ps1."
  value       = var.enable_cognito ? sort(keys(local.cognito_users)) : []
}
