# Publishes the control panel's sign-in settings (config.js) to the panel's site bucket.
#
# The panel's bucket, CloudFront distribution and DNS are built by hand and stay in place
# when the lab is gone. This file writes only config.js into that bucket, built from this
# lab's Cognito pool and app client, then clears that file from CloudFront's cache. It never
# touches index.html or any other page. On destroy the object is deleted, so the panel
# says it has no sign-in settings until a lab is applied again.
#
# The cache clearing runs the AWS CLI through PowerShell, with the credentials Terraform
# already uses: the aws_profile variable, or the default credentials when it is null.

locals {
  control_panel_site = (
    length(local.control_panel_resources) > 0 &&
    var.control_panel_bucket != null
  ) ? { site = true } : {}

  control_panel_config_body = length(local.control_panel_site) > 0 ? join("", [
    "// Written by Terraform (control_panel_site.tf). Do not edit; apply again to refresh it.\n",
    "window.PANEL_CONFIG = ",
    jsonencode({
      region            = var.aws_region
      userPoolId        = aws_cognito_user_pool.lab["domain"].id
      appClientId       = aws_cognito_user_pool_client.control_panel["domain"].id
      hostedLoginDomain = "${var.cognito_domain_prefix}.auth.${var.aws_region}.amazoncognito.com"
      redirectUri       = "${var.control_panel_url}/"
      apiUrl            = local.panel_api.url
      grafanaUrl        = var.grafana_dashboard_url
    }),
    ";\n",
  ]) : ""
}

resource "aws_s3_object" "control_panel_config" {
  for_each = local.control_panel_site

  bucket        = var.control_panel_bucket
  key           = "config.js"
  content       = local.control_panel_config_body
  content_type  = "application/javascript; charset=utf-8"
  cache_control = "no-cache"
  etag          = md5(local.control_panel_config_body)
}

locals {
  control_panel_invalidation = (
    length(local.control_panel_site) > 0 &&
    var.control_panel_distribution_id != null
  ) ? { config = true } : {}
}

resource "terraform_data" "control_panel_config_cache" {
  for_each = local.control_panel_invalidation

  triggers_replace = [aws_s3_object.control_panel_config["site"].etag]

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-NoProfile", "-Command"]
    command     = "$p = @(); if ($env:PANEL_PROFILE) { $p = @('--profile', $env:PANEL_PROFILE) }; aws cloudfront create-invalidation @p --distribution-id $env:PANEL_DISTRIBUTION_ID --paths /config.js | Out-Null; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }"
    environment = {
      PANEL_DISTRIBUTION_ID = var.control_panel_distribution_id
      PANEL_PROFILE         = var.aws_profile != null ? var.aws_profile : ""
    }
  }
}
