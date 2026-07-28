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

output "desktop_username" {
  description = "Username for XRDP."
  value       = "ubuntu"
}

output "desktop_password" {
  description = "Generated XRDP password. Stored in Terraform state."
  value       = random_password.desktop_password.result
  sensitive   = true
}

output "ssm_rdp_command_windows" {
  description = "Windows PowerShell command to create the local RDP tunnel."

  value = <<-EOT
    aws ssm start-session --target ${aws_instance.ai_lab.id} --document-name AWS-StartPortForwardingSession --parameters portNumber="3389",localPortNumber="13389" --region ${var.aws_region} --profile ${var.aws_profile}
  EOT
}

output "ssm_shell_command" {
  description = "Open a command-line SSM session."

  value = <<-EOT
    aws ssm start-session --target ${aws_instance.ai_lab.id} --region ${var.aws_region} --profile ${var.aws_profile}
  EOT
}
