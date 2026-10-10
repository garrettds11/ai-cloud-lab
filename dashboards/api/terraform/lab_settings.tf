# Publishes what the lab needs to know about this API, so the lab can wire itself to it
# without copying IDs by hand. The lab reads this one parameter when its
# control_panel_api_from_ssm is true (control_panel_api.tf at the repository root).
#
# The path uses the lab's project_name, so it must match the lab's: lab_project_name here
# and project_name in the root terraform.tfvars. It is outside /<lab_project_name>/control-panel,
# so the panel's functions cannot read or change it.
#
# The values are IDs and names, not secrets. Apply this stack before the lab, and apply the
# lab again whenever this stack is created again, because the IDs change.

locals {
  lab_settings_parameter = "/${var.lab_project_name}/control-panel-api/settings"
}

resource "aws_ssm_parameter" "lab_settings" {
  #checkov:skip=CKV2_AWS_34:The value is not secret (API, authorizer, pool and table IDs); SecureString would only add a KMS dependency for the lab's plan

  name        = local.lab_settings_parameter
  description = "Control panel API settings for the lab (written by dashboards/api/terraform). Do not edit; apply that stack again to refresh it."
  type        = "String"
  value = jsonencode({
    api_url            = aws_apigatewayv2_stage.default.invoke_url
    api_id             = aws_apigatewayv2_api.panel.id
    authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
    holding_pool_id    = aws_cognito_user_pool.holding.id
    users_table        = aws_dynamodb_table.users.name
    entitlements_table = aws_dynamodb_table.entitlements.name
  })
}
