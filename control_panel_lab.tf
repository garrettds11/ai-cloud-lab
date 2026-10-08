# Publishes what the control panel manages, so the panel follows the lab on its own.
#
# The panel's two Lambda functions read these parameters while running (no settings to copy
# by hand after a deployment). With no lab deployed they do not exist, so the panel shows no
# instances. Replacing the instance updates instance-ids in place; a destroy removes them
# all. The panel itself, its API and its roles are built by hand and are not touched here.
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

# The Open WebUI image the lab runs. The panel passes its version to the Open WebUI admin
# document, which reports whether the running Open WebUI matches what the actions were
# written for (and, from phase 2 of #55, refuses changes when it does not).
resource "aws_ssm_parameter" "control_panel_open_webui_image" {
  for_each = local.control_panel_lab
  #checkov:skip=CKV2_AWS_34:The value is not secret (a container image name); SecureString would only add a KMS dependency for every reader

  name        = "${local.control_panel_lab_prefix}/open-webui-image"
  description = "Container image of the lab's Open WebUI, for the control panel's version check."
  type        = "String"
  value       = var.open_webui_container_image
}

# One grant per demo user who has the operators role, for the lab instance, in the same
# shape the panel writes. Needs the lab (this file's instance) and the table name. Without
# a grant an operator sees nothing, because the panel shows only granted instances.
resource "aws_dynamodb_table_item" "control_panel_demo_grant" {
  for_each = var.control_panel_entitlements_table == null ? {} : {
    for email, user in local.panel_demo_roles : email => user if contains(user.roles, "operators") && var.control_panel_url != null
  }

  table_name = var.control_panel_entitlements_table
  hash_key   = "userId"
  range_key  = "instanceId"

  item = jsonencode({
    userId       = { S = lower(each.key) }
    instanceId   = { S = aws_instance.ai_lab.id }
    userName     = { S = each.value.name }
    role         = { S = join(", ", compact([contains(each.value.roles, "admin") ? "Administrator" : "", "Operator", contains(each.value.roles, "user_mgrs") ? "User manager" : ""])) }
    instanceName = { S = try(aws_instance.ai_lab.tags["Name"], aws_instance.ai_lab.id) }
    status       = { S = "applied" }
    grantedBy    = { S = "terraform" }
  })
}
