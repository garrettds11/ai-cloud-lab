# Publishes what the control panel manages, so the panel follows the lab on its own.
#
# The panel's two Lambda functions read these parameters while running (no settings to copy
# by hand after a deployment). With no lab deployed they do not exist, so the panel shows no
# instances. Replacing the instance updates instance-ids in place; a destroy removes all
# three. The panel itself, its API and its roles are built by hand and are not touched here.
#
# Also tags the instance control-panel=managed. The customer function's IAM role may start
# only instances that carry this tag, whatever the project's other tags are.

locals {
  control_panel_lab_prefix = "/${var.project_name}/control-panel"
  control_panel_lab        = var.control_panel_url != null ? { lab = true } : {}
  control_panel_lab_domain = var.control_panel_url != null && var.enable_domain_access ? { lab = true } : {}
}

resource "aws_ssm_parameter" "control_panel_instance_ids" {
  for_each = local.control_panel_lab

  name        = "${local.control_panel_lab_prefix}/instance-ids"
  description = "Instance IDs the control panel manages, comma separated."
  type        = "String"
  value       = aws_instance.ai_lab.id
}

resource "aws_ssm_parameter" "control_panel_target_group_arn" {
  for_each = local.control_panel_lab_domain

  name        = "${local.control_panel_lab_prefix}/target-group-arn"
  description = "ALB target group whose health is the control panel's HTTP readiness check."
  type        = "String"
  value       = aws_lb_target_group.domain["domain"].arn
}

resource "aws_ssm_parameter" "control_panel_service_url" {
  for_each = local.control_panel_lab_domain

  name        = "${local.control_panel_lab_prefix}/service-url"
  description = "Where the control panel's Access button goes."
  type        = "String"
  value       = "https://${var.domain_name}"
}
