output "backend_instance_id" { value = module.track.backend_instance_id }
output "backend_private_ip" { value = module.track.backend_private_ip }
output "archive_bucket" { value = module.track.archive_bucket }
output "ui_tunnel_command" { value = module.track.ui_tunnel_command }
output "ssm_package_names" { value = module.track.ssm_package_names }
output "agent_association_ids" { value = module.track.agent_association_ids }
output "uninstall_agent_commands" { value = module.track.uninstall_agent_commands }
output "lab_found" { value = module.track.lab_found }
