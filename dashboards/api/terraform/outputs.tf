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

output "lab_tfvars" {
  description = "Lines to paste into the lab's terraform.tfvars (repository root) so the lab wires itself to this API."
  value       = <<-EOT
    control_panel_api_url            = "${aws_apigatewayv2_stage.default.invoke_url}"
    control_panel_api_id             = "${aws_apigatewayv2_api.panel.id}"
    control_panel_authorizer_id      = "${aws_apigatewayv2_authorizer.jwt.id}"
    control_panel_holding_pool_id    = "${aws_cognito_user_pool.holding.id}"
    control_panel_users_table        = "${aws_dynamodb_table.users.name}"
    control_panel_entitlements_table = "${aws_dynamodb_table.entitlements.name}"
  EOT
}
