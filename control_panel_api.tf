# Points the control panel API's sign-in authorizer at this lab's Cognito pool.
#
# The API, its routes and its authorizer are built by hand and stay in place when the lab
# is gone. Until a lab exists the authorizer trusts an empty "holding" pool that can never
# issue a token, so every route answers 401. This resource only changes the authorizer's
# issuer and audience: to the lab's pool on apply, back to the holding pool on destroy.
# It never creates or deletes the authorizer, so the routes keep working across lab rebuilds.
#
# It runs the AWS CLI through PowerShell, with the same aws_profile Terraform uses (the
# default credentials when aws_profile is null).

locals {
  control_panel_authorizer = (
    length(local.control_panel_resources) > 0 &&
    var.control_panel_api_id != null &&
    var.control_panel_authorizer_id != null &&
    var.control_panel_holding_pool_id != null
  ) ? { authorizer = true } : {}
}

resource "terraform_data" "control_panel_authorizer" {
  for_each = local.control_panel_authorizer

  input = {
    region        = var.aws_region
    profile       = var.aws_profile != null ? var.aws_profile : ""
    api_id        = var.control_panel_api_id
    authorizer_id = var.control_panel_authorizer_id
    issuer        = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.lab["domain"].id}"
    audience      = aws_cognito_user_pool_client.control_panel["domain"].id
    holding       = "https://cognito-idp.${var.aws_region}.amazonaws.com/${var.control_panel_holding_pool_id}"
  }

  # Re-run the provisioners whenever the lab's pool or client changes, and also when the
  # authorizer itself changes (moving to another API, for example the Terraform-built one in
  # dashboards/api/terraform). Replacing first runs the destroy step with the old values, which
  # points the old authorizer back at its holding pool, then points the new one at this lab.
  triggers_replace = [
    aws_cognito_user_pool.lab["domain"].id,
    aws_cognito_user_pool_client.control_panel["domain"].id,
    var.control_panel_api_id,
    var.control_panel_authorizer_id,
    var.control_panel_holding_pool_id,
  ]

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-NoProfile", "-Command"]
    command     = "$p = @(); if ($env:PANEL_PROFILE) { $p = @('--profile', $env:PANEL_PROFILE) }; aws apigatewayv2 update-authorizer @p --region $env:PANEL_REGION --api-id $env:PANEL_API_ID --authorizer-id $env:PANEL_AUTHORIZER_ID --jwt-configuration \"Issuer=$env:PANEL_ISSUER,Audience=$env:PANEL_AUDIENCE\"; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
    environment = {
      PANEL_REGION        = self.input.region
      PANEL_PROFILE       = self.input.profile
      PANEL_API_ID        = self.input.api_id
      PANEL_AUTHORIZER_ID = self.input.authorizer_id
      PANEL_ISSUER        = self.input.issuer
      PANEL_AUDIENCE      = self.input.audience
    }
  }

  provisioner "local-exec" {
    when        = destroy
    on_failure  = continue
    interpreter = ["PowerShell", "-NoProfile", "-Command"]
    command     = "$p = @(); if ($env:PANEL_PROFILE) { $p = @('--profile', $env:PANEL_PROFILE) }; aws apigatewayv2 update-authorizer @p --region $env:PANEL_REGION --api-id $env:PANEL_API_ID --authorizer-id $env:PANEL_AUTHORIZER_ID --jwt-configuration \"Issuer=$env:PANEL_ISSUER,Audience=holding-unused\""
    environment = {
      PANEL_REGION        = self.input.region
      PANEL_PROFILE       = self.input.profile
      PANEL_API_ID        = self.input.api_id
      PANEL_AUTHORIZER_ID = self.input.authorizer_id
      PANEL_ISSUER        = self.input.holding
    }
  }
}
