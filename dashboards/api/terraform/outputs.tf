output "api_url" {
  description = "The API's address. Put it in the lab's control_panel_api_url so config.js points the pages here."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "api_id" {
  description = "For the lab's control_panel_api_id."
  value       = aws_apigatewayv2_api.panel.id
}

output "authorizer_id" {
  description = "For the lab's control_panel_authorizer_id."
  value       = aws_apigatewayv2_authorizer.jwt.id
}

output "holding_pool_id" {
  description = "For the lab's control_panel_holding_pool_id."
  value       = aws_cognito_user_pool.holding.id
}

output "tables" {
  description = "Table names. The users and grants tables go in the lab's control_panel_users_table and control_panel_entitlements_table."
  value = {
    users        = aws_dynamodb_table.users.name
    entitlements = aws_dynamodb_table.entitlements.name
    events       = aws_dynamodb_table.events.name
  }
}

output "functions" {
  description = "Function names."
  value       = { for k, f in aws_lambda_function.function : k => f.function_name }
}

output "webui_admin_document" {
  description = "The SSM document the admin function runs for Open WebUI actions. It accepts only the listed action names."
  value       = aws_ssm_document.webui_admin.name
}

output "webui_desired_state_table" {
  description = "Table for the Open WebUI settings the panel will own (phase 2 of #55). Not read or written yet."
  value       = aws_dynamodb_table.webui_desired_state.name
}

output "lab_settings_parameter" {
  description = "The SSM parameter this stack publishes for the lab. With control_panel_api_from_ssm = true the lab reads the API address, IDs and table names from it, so nothing is copied by hand."
  value       = aws_ssm_parameter.lab_settings.name
}

output "lab_tfvars" {
  description = "Only if the lab does not read the settings parameter (control_panel_api_from_ssm = false): lines to put in the lab's terraform.tfvars (repository root). Values set there win over the parameter."
  value       = <<-EOT
    control_panel_api_url            = "${aws_apigatewayv2_stage.default.invoke_url}"
    control_panel_api_id             = "${aws_apigatewayv2_api.panel.id}"
    control_panel_authorizer_id      = "${aws_apigatewayv2_authorizer.jwt.id}"
    control_panel_holding_pool_id    = "${aws_cognito_user_pool.holding.id}"
    control_panel_users_table        = "${aws_dynamodb_table.users.name}"
    control_panel_entitlements_table = "${aws_dynamodb_table.entitlements.name}"
  EOT
}
