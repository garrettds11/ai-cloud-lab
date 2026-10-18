output "backend_instance_id" { value = aws_instance.backend.id }
output "backend_private_ip" { value = aws_instance.backend.private_ip }
output "archive_bucket" { value = aws_s3_bucket.archive.bucket }
output "ssm_package_names" { value = { for name, package in aws_ssm_document.agent : name => package.name } }
output "ui_tunnel_command" {
  value = "aws ssm start-session --target ${aws_instance.backend.id} --document-name AWS-StartPortForwardingSession --parameters \"portNumber=${local.ui_port},localPortNumber=18080\" --region ${var.aws_region}${var.aws_profile == null ? "" : " --profile ${var.aws_profile}"}"
}
output "agent_association_ids" { value = { for name, assoc in aws_ssm_association.agent : name => assoc.association_id } }
output "uninstall_agent_commands" {
  value = local.lab == null ? {} : { for name, package in aws_ssm_document.agent : name => "aws ssm send-command --document-name AWS-ConfigureAWSPackage --instance-ids ${local.lab.instance_id} --parameters action=Uninstall,name=${package.name} --region ${var.aws_region}${var.aws_profile == null ? "" : " --profile ${var.aws_profile}"}" }
}
output "ui_port" { value = local.ui_port }
output "lab_found" {
  description = "Whether the lab's settings were found; false means the collector is not installed yet."
  value       = local.lab != null
}
