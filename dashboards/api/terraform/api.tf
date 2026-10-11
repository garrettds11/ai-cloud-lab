# The HTTP API: one JWT authorizer, two Lambda integrations, and one route per entry in
# local.routes. The route list must match ../openapi.yaml; ../tests/test_openapi.py checks it.

locals {
  # Route key => the function that answers it. /admin/* goes to the admin function.
  routes = {
    "POST /session"                                 = "customer"
    "GET /instances"                                = "customer"
    "POST /instances/{instanceId}/start"            = "customer"
    "POST /instances/{instanceId}/reset-timer"      = "customer"
    "GET /logins"                                   = "customer"
    "GET /logs"                                     = "customer"
    "GET /spend"                                    = "customer"
    "GET /admin/users"                              = "admin"
    "GET /admin/instances"                          = "admin"
    "GET /admin/users/{userId}/grants"              = "admin"
    "GET /admin/changes"                            = "admin"
    "GET /admin/logins"                             = "admin"
    "GET /admin/logs"                               = "admin"
    "PUT /admin/users/{userId}"                     = "admin"
    "GET /admin/spend-caps"                         = "admin"
    "PUT /admin/spend-caps"                         = "admin"
    "POST /admin/spend-caps/lab-override"           = "admin"
    "POST /admin/spend-caps/{userId}/confirm-email" = "admin"
    "GET /admin/timer-policy"                       = "admin"
    "PUT /admin/timer-policy"                       = "admin"
    "POST /admin/webui/actions"                     = "admin"
    "GET /admin/webui/actions/{commandId}"          = "admin"
    "GET /admin/webui/tool-tokens"                  = "admin"
    "GET /admin/webui/desired-state"                = "admin"
  }

  holding_issuer = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.holding.id}"
}

resource "aws_apigatewayv2_api" "panel" {
  name          = var.name_prefix
  description   = "AI Cloud Lab control panel API"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = [var.panel_origin]
    allow_methods = ["GET", "POST", "PUT", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
}

# An empty user pool: no users and no app clients, so it can never issue a token. The
# authorizer trusts it whenever no lab is deployed, so every route answers 401.
resource "aws_cognito_user_pool" "holding" {
  name = "${var.name_prefix}-holding"

  admin_create_user_config {
    allow_admin_create_user_only = true
  }
}

# Created pointing at the holding pool. The lab's Terraform (control_panel_api.tf at the
# repository root) then points it at the lab's Cognito pool on apply and back here on
# destroy, so the issuer and audience are deliberately not managed by this stack. The lab
# finds these IDs in the settings parameter (lab_settings.tf). For another sign-in provider,
# set the issuer and audience by hand; this stack will not undo it.
resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.panel.id
  authorizer_type  = "JWT"
  name             = "${var.name_prefix}-jwt"
  identity_sources = ["$request.header.Authorization"]

  jwt_configuration {
    issuer   = local.holding_issuer
    audience = ["holding-unused"]
  }

  lifecycle {
    ignore_changes = [jwt_configuration]
  }
}

resource "aws_apigatewayv2_integration" "function" {
  for_each = local.functions

  api_id                 = aws_apigatewayv2_api.panel.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.function[each.key].invoke_arn
  payload_format_version = "2.0"
  timeout_milliseconds   = 15000
}

resource "aws_apigatewayv2_route" "panel" {
  for_each = local.routes

  api_id             = aws_apigatewayv2_api.panel.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.function[each.value].id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_cloudwatch_log_group" "access" {
  #checkov:skip=CKV_AWS_338:Operational access log for a lab; a shorter retention is deliberate (cost and privacy)
  #checkov:skip=CKV_AWS_158:Encrypted at rest by CloudWatch; a customer managed key adds cost for no lab benefit
  name              = "/aws/apigateway/${var.name_prefix}"
  retention_in_days = var.log_retention_days
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.panel.id
  name        = "$default"
  auto_deploy = true

  default_route_settings {
    throttling_rate_limit  = var.throttle_rate_limit
    throttling_burst_limit = var.throttle_burst_limit
  }

  # Who called what and the result. No request bodies and no tokens are logged.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId = "$context.requestId"
      time      = "$context.requestTime"
      ip        = "$context.identity.sourceIp"
      route     = "$context.routeKey"
      status    = "$context.status"
      email     = "$context.authorizer.claims.email"
      latencyMs = "$context.integrationLatency"
      error     = "$context.authorizer.error"
    })
  }
}

# API Gateway may invoke each function, from this API only. The admin function only from
# the /admin/* routes.
resource "aws_lambda_permission" "api" {
  for_each = local.functions

  statement_id  = "AllowControlPanelApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.function[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = each.key == "admin" ? "${aws_apigatewayv2_api.panel.execution_arn}/*/*/admin/*" : "${aws_apigatewayv2_api.panel.execution_arn}/*/*"
}
