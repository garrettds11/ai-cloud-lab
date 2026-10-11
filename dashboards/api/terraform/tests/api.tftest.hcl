# Offline checks of the control panel API stack. Providers are mocked: no AWS credentials or
# network are needed. From dashboards/api/terraform:
#
#   terraform init -backend=false
#   terraform test

mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition = "aws"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/test-role"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:test"
    }
  }

  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:us-east-1:123456789012:function:test"
      invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123456789012:function:test/invocations"
    }
  }

  mock_resource "aws_apigatewayv2_api" {
    defaults = {
      id            = "abc123defg"
      execution_arn = "arn:aws:execute-api:us-east-1:123456789012:abc123defg"
    }
  }

  mock_resource "aws_cognito_user_pool" {
    defaults = {
      id = "us-east-1_HOLDING01"
    }
  }
}

mock_provider "archive" {}

override_resource {
  target = aws_dynamodb_table.users
  values = { arn = "arn:aws:dynamodb:us-east-1:123456789012:table/panel_users" }
}

override_resource {
  target = aws_dynamodb_table.entitlements
  values = { arn = "arn:aws:dynamodb:us-east-1:123456789012:table/instance_entitlements" }
}

override_resource {
  target = aws_dynamodb_table.events
  values = { arn = "arn:aws:dynamodb:us-east-1:123456789012:table/control_panel_events" }
}

variables {
  aws_region            = "us-east-1"
  lab_project_name      = "aiwebdemo"
  panel_origin          = "https://cp.example.com"
  bootstrap_admins      = ["admin@example.com"]
  adopt_existing_tables = false
}

run "spend_emails_are_off_by_default_and_need_no_topic" {
  command = plan

  assert {
    condition     = length(aws_sns_topic.spend) == 0 && aws_lambda_function.spend.environment[0].variables["SPEND_TOPIC_ARN"] == ""
    error_message = "With spend_emails_enabled false there must be no topic and the job must get no topic address."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.spend.policy, "sns:") && !strcontains(aws_iam_role_policy.spend.policy, "ses:")
    error_message = "With the emails off, the spend job must have no email permissions."
  }
}

run "spend_emails_on_give_the_job_only_its_own_topic" {
  command = plan

  variables {
    spend_emails_enabled = true
  }

  assert {
    condition     = length(aws_sns_topic.spend) == 1 && strcontains(aws_iam_role_policy.spend.policy, "sns:Subscribe") && !strcontains(aws_iam_role_policy.spend.policy, "ses:")
    error_message = "With the emails on, the job may publish and subscribe on the spend topic and must not use SES."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.spend.policy, "cognito-idp:AdminGetUser")
    error_message = "The job must be able to read a Cognito user to check that the email address is verified."
  }
}

run "every_route_exists_and_admin_routes_go_to_the_admin_function" {
  command = apply

  assert {
    condition     = length(aws_apigatewayv2_route.panel) == 15
    error_message = "The API must have the fifteen routes the handler serves (see ../openapi.yaml)."
  }

  assert {
    condition = alltrue([for key, route in aws_apigatewayv2_route.panel :
    route.target == "integrations/${aws_apigatewayv2_integration.function[startswith(split(" ", key)[1], "/admin/") ? "admin" : "customer"].id}"])
    error_message = "Every /admin/* route must go to the admin function and every other route to the customer function."
  }

  assert {
    condition     = alltrue([for route in aws_apigatewayv2_route.panel : route.authorization_type == "JWT" && route.authorizer_id == aws_apigatewayv2_authorizer.jwt.id])
    error_message = "Every route must require the JWT authorizer."
  }

  assert {
    condition     = endswith(aws_lambda_permission.api["admin"].source_arn, "/*/*/admin/*")
    error_message = "API Gateway may invoke the admin function only from /admin/* routes."
  }
}

run "the_authorizer_starts_on_the_empty_holding_pool" {
  command = apply

  assert {
    condition     = aws_apigatewayv2_authorizer.jwt.jwt_configuration[0].issuer == "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_HOLDING01"
    error_message = "The authorizer must trust only the holding pool until the lab points it elsewhere."
  }

  assert {
    condition     = aws_apigatewayv2_authorizer.jwt.jwt_configuration[0].audience == toset(["holding-unused"])
    error_message = "The holding audience must be one no client can have."
  }

  assert {
    condition     = aws_apigatewayv2_api.panel.cors_configuration[0].allow_origins == toset(["https://cp.example.com"])
    error_message = "Only the panel's own origin may call the API from a browser."
  }
}

run "the_customer_role_writes_only_the_timer_reset_and_cannot_set_roles" {
  command = apply

  assert {
    condition     = strcontains(aws_iam_role_policy.function["customer"].policy, "\"ssm:PutParameter\"") && strcontains(aws_iam_role_policy.function["customer"].policy, "parameter/aiwebdemo/auto-stop/reset-at")
    error_message = "The customer role must be able to write the reset parameter."
  }

  assert {
    condition = length([for s in jsondecode(aws_iam_role_policy.function["customer"].policy).Statement : s
    if contains(flatten([s.Action]), "ssm:PutParameter") && flatten([s.Resource]) != ["arn:aws:ssm:us-east-1:123456789012:parameter/aiwebdemo/auto-stop/reset-at", "arn:aws:ssm:us-east-1:123456789012:parameter/aiwebdemo/auto-stop/reset-count"]]) == 0
    error_message = "ssm:PutParameter must be allowed on the reset time and the reset count only, never on the auto-stop rule or the policy."
  }

  assert {
    condition = alltrue([for s in jsondecode(aws_iam_role_policy.function["customer"].policy).Statement :
    !contains(flatten([s.Action]), "dynamodb:PutItem") || !contains(flatten([s.Resource]), aws_dynamodb_table.users.arn) || can(s.Condition["ForAllValues:StringEquals"]["dynamodb:Attributes"])])
    error_message = "The customer role may write users rows only with the attribute condition that keeps out roles."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.function["customer"].policy, "aws:ResourceTag/control-panel")
    error_message = "Starting instances must be limited to instances tagged control-panel=managed."
  }
}

run "the_admin_role_cannot_start_instances_or_write_ssm" {
  command = apply

  assert {
    condition = !strcontains(aws_iam_role_policy.function["admin"].policy, "ec2:StartInstances") && length([
      for st in jsondecode(aws_iam_role_policy.function["admin"].policy).Statement : st
      if contains(flatten([st.Action]), "ssm:PutParameter") && st.Sid != "WriteTimerPolicyOnly"
    ]) == 0
    error_message = "The admin role must not start instances, and the only SSM parameter it may write is the timer policy."
  }
}

run "open_webui_actions_run_only_the_panel_document_on_managed_instances" {
  command = apply

  assert {
    condition     = jsondecode(aws_ssm_document.webui_admin.content).parameters.action.allowedValues == [
      "status", "usage", "set-role", "chat-test", "tool-servers", "settings", "export-config",
      "set-default-model", "set-model-params", "set-feature", "upsert-tool-server", "remove-tool-server", "apply-desired",
    ]
    error_message = "The document must accept only the listed action names."
  }

  assert {
    condition     = jsondecode(aws_ssm_document.webui_admin.content).parameters.expectedVersion.allowedPattern == "^([0-9]{1,4}\\.[0-9]{1,4}\\.[0-9]{1,4})?$"
    error_message = "expectedVersion must be limited to an empty value or a dotted version number."
  }

  assert {
    condition     = strcontains(join("\n", jsondecode(aws_ssm_document.webui_admin.content).mainSteps[0].inputs.runCommand), "ACTION='{{ action }}'")
    error_message = "The document must run ../webui-admin.sh."
  }

  assert {
    condition = length([for s in jsondecode(aws_iam_role_policy.function["admin"].policy).Statement : s
      if contains(flatten([s.Action]), "ssm:SendCommand") && !(
        flatten([s.Resource]) == ["arn:aws:ssm:us-east-1:123456789012:document/${aws_ssm_document.webui_admin.name}"] ||
        try(s.Condition.StringEquals["ssm:resourceTag/control-panel"], "") == "managed"
    )]) == 0
    error_message = "ssm:SendCommand must be limited to the panel's document and to instances tagged control-panel=managed."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.function["customer"].policy, "ssm:SendCommand")
    error_message = "Only the admin function may run Open WebUI actions."
  }

  assert {
    condition     = aws_lambda_function.function["admin"].environment[0].variables["WEBUI_DOCUMENT"] == aws_ssm_document.webui_admin.name
    error_message = "The admin function must be told the document's name."
  }
}

run "open_webui_write_actions_have_tight_parameters_and_the_stored_settings_have_their_own_access" {
  command = apply

  assert {
    condition     = jsondecode(aws_ssm_document.webui_admin.content).parameters.payload.allowedPattern == "^[A-Za-z0-9+/=]{0,4000}$"
    error_message = "The write request must be limited to base64 text."
  }

  assert {
    condition     = jsondecode(aws_ssm_document.webui_admin.content).parameters.toolTokenPrefix.allowedPattern == "^[A-Za-z0-9/_.-]{0,100}$"
    error_message = "The token prefix must be limited to a secret name prefix."
  }

  assert {
    condition = aws_lambda_function.function["admin"].environment[0].variables["TOOL_TOKEN_PREFIX"] == "aiwebdemo/tool-tokens/" && aws_lambda_function.spend.environment[0].variables["TOOL_TOKEN_PREFIX"] == "aiwebdemo/tool-tokens/" && aws_lambda_function.function["admin"].environment[0].variables["WEBUI_DESIRED_TABLE"] == aws_dynamodb_table.webui_desired_state.name && aws_lambda_function.spend.environment[0].variables["WEBUI_DESIRED_TABLE"] == aws_dynamodb_table.webui_desired_state.name
    error_message = "The admin function and the spend job must be told the tool token prefix and the desired-state table."
  }

  assert {
    condition = length([for st in jsondecode(aws_iam_role_policy.function["admin"].policy).Statement : st if st.Sid == "ListToolTokenNames" && st.Action == "secretsmanager:ListSecrets"]) == 1 && !strcontains(aws_iam_role_policy.function["admin"].policy, "GetSecretValue")
    error_message = "The admin function may list secret names but never read a secret."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.function["customer"].policy, "secretsmanager") && !strcontains(aws_iam_role_policy.function["customer"].policy, "webui-desired-state")
    error_message = "Only the admin function and the spend job touch the stored Open WebUI settings and the tool token names."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.spend.policy, "secretsmanager")
    error_message = "The spend job must not touch Secrets Manager."
  }

  assert {
    condition     = aws_lambda_function.function["admin"].environment[0].variables["PRIVATE_TOOL_HOSTS"] == ""
    error_message = "No private tool server host is allowed unless it is listed."
  }
}

run "private_tool_hosts_must_be_host_names" {
  command = plan

  variables {
    private_tool_hosts = ["Bad Host"]
  }

  expect_failures = [var.private_tool_hosts]
}

run "the_tables_are_protected_and_keyed_as_the_code_expects" {
  command = apply

  assert {
    condition     = aws_dynamodb_table.users.deletion_protection_enabled && aws_dynamodb_table.entitlements.deletion_protection_enabled && aws_dynamodb_table.events.deletion_protection_enabled
    error_message = "All three tables must have deletion protection."
  }

  assert {
    condition     = aws_dynamodb_table.users.hash_key == "email" && aws_dynamodb_table.entitlements.hash_key == "userId" && aws_dynamodb_table.entitlements.range_key == "instanceId" && aws_dynamodb_table.events.range_key == "sk"
    error_message = "Table keys must match what handler.py reads and writes."
  }

  assert {
    condition     = aws_dynamodb_table.events.ttl[0].attribute_name == "expiresAt" && aws_dynamodb_table.events.ttl[0].enabled
    error_message = "Events must expire on expiresAt."
  }

  assert {
    condition     = aws_lambda_function.function["customer"].environment[0].variables["RESET_PARAMETER"] == "/aiwebdemo/auto-stop/reset-at" && !contains(keys(aws_lambda_function.function["admin"].environment[0].variables), "RESET_PARAMETER")
    error_message = "Only the customer function is told where the reset parameter is."
  }
}

run "the_api_is_throttled_and_logged" {
  command = apply

  assert {
    condition     = aws_apigatewayv2_stage.default.default_route_settings[0].throttling_rate_limit == 10 && aws_apigatewayv2_stage.default.default_route_settings[0].throttling_burst_limit == 20
    error_message = "The stage must throttle requests."
  }

  assert {
    condition     = !strcontains(aws_apigatewayv2_stage.default.access_log_settings[0].format, "header.Authorization")
    error_message = "The access log must never include the token."
  }
}

run "the_lab_settings_are_published_for_the_lab" {
  command = apply

  assert {
    condition     = aws_ssm_parameter.lab_settings.name == "/aiwebdemo/control-panel-api/settings" && aws_ssm_parameter.lab_settings.type == "String"
    error_message = "The settings must be published at /<lab_project_name>/control-panel-api/settings, where the lab looks for them."
  }

  assert {
    condition = jsondecode(aws_ssm_parameter.lab_settings.value) == {
      api_url            = aws_apigatewayv2_stage.default.invoke_url
      api_id             = "abc123defg"
      authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
      holding_pool_id    = "us-east-1_HOLDING01"
      users_table        = aws_dynamodb_table.users.name
      entitlements_table = aws_dynamodb_table.entitlements.name
    }
    error_message = "The settings must hold exactly the six values the lab reads."
  }

  assert {
    condition     = !startswith(aws_ssm_parameter.lab_settings.name, "/aiwebdemo/control-panel/")
    error_message = "The settings must sit outside the path the panel's functions may read."
  }
}

run "panel_origin_with_a_path_is_rejected" {
  command = plan

  variables {
    panel_origin = "https://cp.example.com/"
  }

  expect_failures = [var.panel_origin]
}

run "bootstrap_admins_must_not_be_empty" {
  command = plan

  variables {
    bootstrap_admins = []
  }

  expect_failures = [var.bootstrap_admins]
}
