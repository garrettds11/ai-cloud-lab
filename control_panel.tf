# Sign-in settings for the control panel dashboard (dashboards/).
#
# The dashboard's own hosting, DNS and API are built by hand and are not managed
# here. Terraform supplies only what lives in Cognito: a public app client for the
# single-page app, and the control_panel_config output (region, user pool ID, app
# client ID, API address). control_panel_site.tf publishes that output as config.js, the
# file the page loads, to the panel's bucket. Terraform writes no other page.

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

# Sign-in page look for the control panel client. Customizations are per app client, so
# this is separate from the lab's. Neutral AI Cloud Lab logo and the panel's colors; see
# branding/README.md.
resource "aws_cognito_user_pool_ui_customization" "control_panel" {
  for_each = local.control_panel_resources

  user_pool_id = aws_cognito_user_pool.lab["domain"].id
  client_id    = aws_cognito_user_pool_client.control_panel[each.key].id
  css          = file("${path.module}/branding/cognito-panel.css")
  image_file   = filebase64("${path.module}/branding/logo-panel.png")

  depends_on = [aws_cognito_user_pool_domain.lab]
}
