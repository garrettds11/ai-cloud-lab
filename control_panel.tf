# Sign-in settings for the control panel dashboard (dashboards/).
#
# The dashboard's own hosting, DNS and API are built by hand and are not managed
# here. Terraform supplies only what lives in Cognito: a public app client for the
# single-page app, and the control_panel_config output (region, user pool ID, app
# client ID, API address). scripts/make-panel-config.ps1 turns that output into
# dashboards/config.js, the file the page loads. Terraform never writes any page.

locals {
  control_panel_resources = var.enable_cognito && var.control_panel_url != null ? { domain = true } : {}
}

# A browser app cannot keep a secret, so this client has none and uses the
# authorization code flow with PKCE. The sign-in token carries the user's groups
# (operators, user_mgrs) in the cognito:groups claim.
resource "aws_cognito_user_pool_client" "control_panel" {
  for_each = local.control_panel_resources

  name         = "${var.project_name}-control-panel"
  user_pool_id = aws_cognito_user_pool.lab["domain"].id

  generate_secret                      = false
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  prevent_user_existence_errors        = "ENABLED"
  explicit_auth_flows                  = ["ALLOW_REFRESH_TOKEN_AUTH"]

  callback_urls = ["${var.control_panel_url}/"]
  logout_urls   = ["${var.control_panel_url}/"]
}
