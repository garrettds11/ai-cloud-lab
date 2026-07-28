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

output "ssh_tunnel_command" {
  description = "SSH tunnel command when SSH is enabled. Otherwise use the SSM port-forwarding output."

  value = var.enable_ssh ? "ssh -i <path-to-key.pem> -L ${var.open_webui_host_port}:localhost:${var.open_webui_host_port} ubuntu@${aws_instance.ai_lab.public_ip}" : "SSH is disabled. Use ssm_open_webui_port_forward_command instead."
}

output "ssm_open_webui_port_forward_command" {
  description = "Windows PowerShell command to create a private SSM tunnel to Open WebUI."

  value = <<-EOT
    aws ssm start-session --target ${aws_instance.ai_lab.id} --document-name AWS-StartPortForwardingSession --parameters portNumber="${var.open_webui_host_port}",localPortNumber="${var.open_webui_host_port}" --region ${var.aws_region} --profile ${var.aws_profile}
  EOT
}

output "ssm_shell_command" {
  description = "Open a command-line SSM session."

  value = <<-EOT
    aws ssm start-session --target ${aws_instance.ai_lab.id} --region ${var.aws_region} --profile ${var.aws_profile}
  EOT
}
