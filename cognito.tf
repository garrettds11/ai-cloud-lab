# Optional Amazon Cognito user directory for the lab users.
#
# When enable_cognito is true, Cognito holds the user accounts and is used as the
# sign-in for both Cloudflare Access (as an OIDC login method) and Open WebUI
# (OIDC single sign-on). Users are created by Terraform without passwords; run
# scripts/set-cognito-passwords.ps1 once to set them from Secrets Manager so no
# password is stored in Terraform state or user-data.

resource "aws_cognito_user_pool" "lab" {
  for_each = local.cognito_resources

  name                = "${var.project_name}-users"
  username_attributes = ["email"]
  mfa_configuration   = "OFF"
  deletion_protection = "INACTIVE"

  # Only administrators create users; there is no self-service sign-up.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_uppercase = false
    require_symbols   = false
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "admin_only"
      priority = 1
    }
  }

  lifecycle {
    precondition {
      condition     = var.enable_domain_access
      error_message = "enable_cognito requires enable_domain_access because the sign-in callbacks use the public domain name."
    }

    precondition {
      condition     = var.cognito_domain_prefix != null
      error_message = "cognito_domain_prefix must be set when enable_cognito is true."
    }

    precondition {
      condition     = !var.enable_cloudflare_access || var.cloudflare_access_team_domain != null
      error_message = "cloudflare_access_team_domain must be set when enable_cognito and enable_cloudflare_access are both true."
    }
  }
}

resource "aws_cognito_user_pool_domain" "lab" {
  for_each = local.cognito_resources

  domain       = var.cognito_domain_prefix
  user_pool_id = aws_cognito_user_pool.lab[each.key].id
}

# One app client shared by Cloudflare Access and Open WebUI. Both are server-side
# applications that keep the client secret off the browser.
resource "aws_cognito_user_pool_client" "lab" {
  for_each = local.cognito_resources

  name         = "${var.project_name}-web"
  user_pool_id = aws_cognito_user_pool.lab[each.key].id

  generate_secret                      = true
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]
  prevent_user_existence_errors        = "ENABLED"
  explicit_auth_flows                  = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]

  callback_urls = compact([
    var.cloudflare_access_team_domain == null ? "" : "https://${var.cloudflare_access_team_domain}/cdn-cgi/access/callback",
    "https://${var.domain_name}/oauth/oidc/callback",
  ])
  logout_urls = ["https://${var.domain_name}"]
}

# Logo and colors for the Cognito sign-in page. The classic hosted UI only accepts
# a logo image and a limited CSS file; it cannot show custom text. Edit
# branding/cognito.css and branding/logo.png to change the look.
resource "aws_cognito_user_pool_ui_customization" "lab" {
  for_each = local.cognito_resources

  user_pool_id = aws_cognito_user_pool.lab[each.key].id
  client_id    = aws_cognito_user_pool_client.lab[each.key].id
  css          = file("${path.module}/branding/cognito.css")
  image_file   = filebase64("${path.module}/branding/logo.png")

  depends_on = [aws_cognito_user_pool_domain.lab]
}

# Users are created without passwords and with the email marked verified, so no
# email is sent (the demo addresses cannot receive mail). Set passwords with
# scripts/set-cognito-passwords.ps1.
resource "aws_cognito_user" "lab" {
  for_each = var.enable_cognito ? local.cognito_users : {}

  user_pool_id   = aws_cognito_user_pool.lab["domain"].id
  username       = each.value.email
  message_action = "SUPPRESS"

  attributes = {
    email          = each.value.email
    email_verified = "true"
    name           = each.value.name
  }
}

# Roles for the control panel dashboard live in the panel's own DynamoDB table
# (panel_users), not in Cognito. Cognito here only proves who a person is.
#
# When control_panel_users_table is set, Terraform adds one row per demo user to
# that table so the demo works straight away: the odd demo users (the 1st, 3rd, 5th ...
# entry of open_webui_demo_users, so demo1, demo3 ... demo9) are user managers
# (user_mgrs), the even ones (demo2, demo4 ... demo10) are customers (operators), and the
# administrator account gets user_mgrs. The rows
# are removed again on destroy. Terraform never touches any other row, so real users
# are managed in the control panel. After a demo user signs in the panel adds a few
# fields (name, last seen) to their row, and the next apply puts the row back as seeded.
resource "aws_dynamodb_table_item" "panel_demo_user" {
  for_each = var.control_panel_users_table == null ? {} : local.panel_demo_roles

  table_name = var.control_panel_users_table
  hash_key   = "email"

  item = jsonencode({
    email  = { S = each.key }
    name   = { S = each.value.name }
    roles  = { L = [for role in each.value.roles : { S = role }] }
    source = { S = "lab" }
  })
}

# Cloudflare Access login method backed by the Cognito user pool.
resource "cloudflare_zero_trust_access_identity_provider" "cognito" {
  for_each = local.cognito_cloudflare_idp

  account_id = var.cloudflare_account_id
  name       = "AWS Cognito"
  type       = "oidc"

  config = {
    client_id        = aws_cognito_user_pool_client.lab[each.key].id
    client_secret    = aws_cognito_user_pool_client.lab[each.key].client_secret
    auth_url         = "https://${var.cognito_domain_prefix}.auth.${var.aws_region}.amazoncognito.com/oauth2/authorize"
    token_url        = "https://${var.cognito_domain_prefix}.auth.${var.aws_region}.amazoncognito.com/oauth2/token"
    certs_url        = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.lab[each.key].id}/.well-known/jwks.json"
    scopes           = ["openid", "email", "profile"]
    email_claim_name = "email"
    pkce_enabled     = true
  }

  depends_on = [aws_cognito_user_pool_domain.lab]
}

# Lets the instance read the app client secret at boot, so the secret is not in
# user-data.
resource "aws_iam_role_policy" "cognito_client" {
  for_each = local.cognito_resources

  name = "${var.project_name}-cognito-client"
  role = aws_iam_role.ssm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "cognito-idp:DescribeUserPoolClient"
      Resource = aws_cognito_user_pool.lab[each.key].arn
    }]
  })
}
