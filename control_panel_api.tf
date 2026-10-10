# Wires the lab to the control panel API, and points the API's sign-in authorizer at this
# lab's Cognito pool.
#
# The API is its own Terraform stack (dashboards/api/terraform) and stays in place when the
# lab is gone. That stack publishes its address, IDs and table names in one SSM parameter,
# /<project_name>/control-panel-api/settings. With control_panel_api_from_ssm = true the lab
# reads them from there, so a rebuilt API needs no copying: the next lab plan picks up the
# new IDs. Any of the six values set in terraform.tfvars wins over the parameter.
#
# Until a lab exists the authorizer trusts an empty "holding" pool that can never issue a
# token, so every route answers 401. The terraform_data resource below only changes the
# authorizer's issuer and audience: to the lab's pool on apply, back to the holding pool on
# destroy. It never creates or deletes the authorizer, so the routes keep working across lab
# rebuilds.
#
# It runs the AWS CLI through PowerShell, with the same aws_profile Terraform uses (the
# default credentials when aws_profile is null).

locals {
  control_panel_api_parameter = "/${var.project_name}/control-panel-api/settings"
}

# Listing the path first lets a missing parameter be a warning (the check below) instead of
# a failed plan, so the lab can still be planned and destroyed when the API stack is gone.
data "aws_ssm_parameters_by_path" "control_panel_api" {
  count = var.control_panel_api_from_ssm ? 1 : 0

  path            = "/${var.project_name}/control-panel-api"
  with_decryption = false
}

data "aws_ssm_parameter" "control_panel_api" {
  count = var.control_panel_api_from_ssm && contains(try(data.aws_ssm_parameters_by_path.control_panel_api[0].names, []), local.control_panel_api_parameter) ? 1 : 0

  name            = local.control_panel_api_parameter
  with_decryption = false
}

locals {
  # The parameter holds IDs and names only, so its plain (insecure_value) form is used; the
  # sensitive form could not drive for_each below.
  control_panel_api_published = try(jsondecode(data.aws_ssm_parameter.control_panel_api[0].insecure_value), {})

  # The values the rest of the lab uses: terraform.tfvars first, then the parameter.
  panel_api = {
    url                = var.control_panel_api_url != null ? var.control_panel_api_url : try(tostring(local.control_panel_api_published.api_url), null)
    api_id             = var.control_panel_api_id != null ? var.control_panel_api_id : try(tostring(local.control_panel_api_published.api_id), null)
    authorizer_id      = var.control_panel_authorizer_id != null ? var.control_panel_authorizer_id : try(tostring(local.control_panel_api_published.authorizer_id), null)
    holding_pool_id    = var.control_panel_holding_pool_id != null ? var.control_panel_holding_pool_id : try(tostring(local.control_panel_api_published.holding_pool_id), null)
    users_table        = var.control_panel_users_table != null ? var.control_panel_users_table : try(tostring(local.control_panel_api_published.users_table), null)
    entitlements_table = var.control_panel_entitlements_table != null ? var.control_panel_entitlements_table : try(tostring(local.control_panel_api_published.entitlements_table), null)
  }
}

check "control_panel_api_settings_found" {
  assert {
    condition     = !var.control_panel_api_from_ssm || local.control_panel_api_published != {}
    error_message = "control_panel_api_from_ssm is true, but the SSM parameter ${local.control_panel_api_parameter} is missing or unreadable. Apply dashboards/api/terraform first (with lab_project_name = \"${var.project_name}\"), then plan the lab again. Until then the panel gets no API address and the API's authorizer is not pointed at this lab."
  }
}

locals {
  control_panel_authorizer = (
    length(local.control_panel_resources) > 0 &&
    local.panel_api.api_id != null &&
    local.panel_api.authorizer_id != null &&
    local.panel_api.holding_pool_id != null
  ) ? { authorizer = true } : {}
}

resource "terraform_data" "control_panel_authorizer" {
  for_each = local.control_panel_authorizer

  input = {
    region        = var.aws_region
    profile       = var.aws_profile != null ? var.aws_profile : ""
    api_id        = local.panel_api.api_id
    authorizer_id = local.panel_api.authorizer_id
    issuer        = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.lab["domain"].id}"
    audience      = aws_cognito_user_pool_client.control_panel["domain"].id
    holding       = "https://cognito-idp.${var.aws_region}.amazonaws.com/${local.panel_api.holding_pool_id}"
  }

  # Re-run the provisioners whenever the lab's pool or client changes, and also when the
  # authorizer itself changes (moving to another API, for example the Terraform-built one in
  # dashboards/api/terraform). Replacing first runs the destroy step with the old values, which
  # points the old authorizer back at its holding pool, then points the new one at this lab.
  triggers_replace = [
    aws_cognito_user_pool.lab["domain"].id,
    aws_cognito_user_pool_client.control_panel["domain"].id,
    local.panel_api.api_id,
    local.panel_api.authorizer_id,
    local.panel_api.holding_pool_id,
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
