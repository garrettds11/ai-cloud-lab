# Offline checks of variable validation, lifecycle preconditions and the default
# network exposure. Providers are mocked, so no AWS or Cloudflare credentials, and no
# network access, are needed. Run from the repository root:
#
#   Invoke-TerraformWithCloudflareToken -Arguments @('init', '-backend=false')
#   Invoke-TerraformWithCloudflareToken -Arguments @('test')
#
# Requires Terraform 1.7 or later (mock_provider). A local terraform.tfvars is still
# loaded by the test command, so the variables block below pins every setting the tests
# depend on; values in the test file take precedence over terraform.tfvars.

mock_provider "aws" {
  mock_data "aws_vpc" {
    defaults = {
      id = "vpc-0123456789abcdef0"
    }
  }

  mock_data "aws_subnets" {
    defaults = {
      ids = ["subnet-0123456789abcdef0", "subnet-0fedcba9876543210"]
    }
  }

  mock_data "aws_ssm_parameter" {
    defaults = {
      value = "ami-0123456789abcdef0"
    }
  }

  mock_data "aws_secretsmanager_secret" {
    defaults = {
      arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-AbCdEf"
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
    }
  }

  mock_data "aws_route53_zone" {
    defaults = {
      zone_id = "Z0123456789ABCDEFGHIJ"
    }
  }

  # Resources that other resources reference by ARN. The provider validates ARN
  # arguments, so the mock must return well-formed ARNs, not random strings.
  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/test-role"
    }
  }

  mock_resource "aws_iam_instance_profile" {
    defaults = {
      arn = "arn:aws:iam::123456789012:instance-profile/test-profile"
    }
  }

  mock_resource "aws_sns_topic" {
    defaults = {
      arn = "arn:aws:sns:us-east-1:123456789012:test-topic"
    }
  }

  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:us-east-1:123456789012:function:test-function"
      invoke_arn = "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/arn:aws:lambda:us-east-1:123456789012:function:test-function/invocations"
    }
  }

  mock_resource "aws_cloudwatch_event_rule" {
    defaults = {
      arn = "arn:aws:events:us-east-1:123456789012:rule/test-rule"
    }
  }

  mock_resource "aws_cloudwatch_log_group" {
    defaults = {
      arn = "arn:aws:logs:us-east-1:123456789012:log-group:/test/log-group"
    }
  }

  mock_resource "aws_security_group" {
    defaults = {
      arn = "arn:aws:ec2:us-east-1:123456789012:security-group/sg-0123456789abcdef0"
    }
  }

  mock_resource "aws_instance" {
    defaults = {
      arn = "arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0"
    }
  }
}

mock_provider "cloudflare" {}

# A valid baseline. Each run block below changes only what it tests.
variables {
  # Secrets and alerts
  open_webui_admin_password_secret_arn     = "arn:aws:secretsmanager:us-east-1:123456789012:secret:admin-AbCdEf"
  open_webui_demo_user_password_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:demo-AbCdEf"
  auto_stop_alert_email                    = "test@example.com"

  # Everything that can change which resources exist, pinned to the plain defaults
  enable_domain_access            = false
  enable_cloudflare_access        = false
  enable_alb_http_redirect        = false
  enable_cognito                  = false
  enable_origin_lockdown          = false
  enable_ssh                      = false
  open_webui_enable_local_login   = true
  allowed_ssh_cidr                = null
  ssh_key_name                    = null
  extra_egress_cidrs              = []
  enable_grafana_telemetry        = false
  grafana_otlp_endpoint           = null
  grafana_otlp_instance_id        = null
  grafana_credentials_secret_arn  = null
  origin_lockdown_extra_cidrs     = []
  cloudflare_account_id           = null
  cloudflare_api_token_secret_arn = null
  cloudflare_access_team_domain   = null
  control_panel_url               = null
  cognito_domain_prefix           = null
  cognito_extra_users             = []
  root_volume_size                = 80
  open_webui_host_port            = 8080
  auto_stop_idle_minutes          = 60
  auto_stop_max_uptime_minutes    = 0
  vuln_mcp_table_name             = null
  vuln_mcp_token_secret_arn       = null
}

run "baseline_plan_succeeds" {
  command = plan

  assert {
    condition     = local.lab_unavailable_worker_script == null && length(cloudflare_workers_script.lab_unavailable_page) == 0 && length(cloudflare_workers_route.lab_unavailable_page) == 0
    error_message = "Without a control panel URL, no fallback script or route may be created."
  }
}

run "fallback_disabled_without_cloudflare" {
  command = plan

  variables {
    control_panel_url = "https://cp.example.com"
  }

  assert {
    condition     = local.lab_unavailable_worker_script == null && length(cloudflare_workers_script.lab_unavailable_page) == 0
    error_message = "A control panel URL alone must not enable the Cloudflare fallback."
  }
}

# Target the Worker to test script rendering without unrelated Cloudflare zone lookup.
run "fallback_enabled_with_cloudflare_and_panel_url" {
  command = plan

  plan_options {
    target = [cloudflare_workers_script.lab_unavailable_page]
  }

  variables {
    enable_cloudflare_access = true
    cloudflare_account_id    = "0123456789abcdef0123456789abcdef"
    control_panel_url        = "https://cp.example.com"
  }

  assert {
    condition     = length(cloudflare_workers_script.lab_unavailable_page) == 1
    error_message = "Cloudflare access with a panel URL must create the fallback Worker."
  }

  assert {
    condition     = strcontains(cloudflare_workers_script.lab_unavailable_page["domain"].content, "const CONTROL_PANEL_URL = \"https://cp.example.com/\";")
    error_message = "The fallback must redirect to the configured panel URL with a trailing slash."
  }
}

# ---- Default network exposure ----
# These runs use apply (still mocked and offline) because the security group rule sets
# are not known during a plan.

run "no_inbound_rules_by_default" {
  command = apply

  assert {
    condition     = length(aws_security_group.ai_lab.ingress) == 0
    error_message = "The instance security group must have no inbound rules by default."
  }
}

run "outbound_limited_to_https_and_http_by_default" {
  command = apply

  assert {
    condition     = length(aws_security_group.ai_lab.egress) == 2
    error_message = "The instance must allow exactly two outbound rules (TCP 443 and 80) by default."
  }

  assert {
    condition     = alltrue([for rule in aws_security_group.ai_lab.egress : rule.protocol == "tcp" && contains([80, 443], rule.from_port)])
    error_message = "Default outbound rules must be TCP 80 or 443 only."
  }
}

run "extra_egress_cidrs_adds_one_rule" {
  command = apply

  variables {
    extra_egress_cidrs = ["10.0.0.0/16"]
  }

  assert {
    condition     = length(aws_security_group.ai_lab.egress) == 3
    error_message = "extra_egress_cidrs must add exactly one outbound rule."
  }
}

run "extra_egress_cidrs_rejects_invalid_cidr" {
  command = plan

  variables {
    extra_egress_cidrs = ["not-a-cidr"]
  }

  expect_failures = [var.extra_egress_cidrs]
}

# ---- SSH ----

run "ssh_from_anywhere_is_rejected" {
  command = plan

  variables {
    allowed_ssh_cidr = "0.0.0.0/0"
  }

  expect_failures = [var.allowed_ssh_cidr]
}

run "ssh_without_key_is_rejected" {
  command = plan

  variables {
    enable_ssh       = true
    allowed_ssh_cidr = "203.0.113.5/32"
  }

  expect_failures = [aws_instance.ai_lab]
}

run "ssh_with_key_and_cidr_opens_only_port_22" {
  command = apply

  variables {
    enable_ssh       = true
    allowed_ssh_cidr = "203.0.113.5/32"
    ssh_key_name     = "test-key"
  }

  assert {
    condition     = length(aws_security_group.ai_lab.ingress) == 1 && one(aws_security_group.ai_lab.ingress).from_port == 22
    error_message = "With SSH enabled, the only inbound rule must be TCP 22."
  }
}

# ---- Sizing and ports ----

run "root_volume_too_small_is_rejected" {
  command = plan

  variables {
    root_volume_size = 20
  }

  expect_failures = [var.root_volume_size]
}

run "invalid_host_port_is_rejected" {
  command = plan

  variables {
    open_webui_host_port = 70000
  }

  expect_failures = [var.open_webui_host_port]
}

# ---- Secrets and sign-in ----

# A null admin secret ARN is not tested here: the aws_secretsmanager_secret data
# source rejects it during provider schema validation ("one of `arn,name` must be
# specified"), before any Terraform check can run, so expect_failures cannot catch it.

run "malformed_admin_secret_arn_is_rejected" {
  command = plan

  variables {
    open_webui_admin_password_secret_arn = "not-an-arn"
  }

  expect_failures = [var.open_webui_admin_password_secret_arn]
}

run "demo_users_need_a_demo_password_secret" {
  command = plan

  variables {
    open_webui_demo_user_password_secret_arn = null
  }

  expect_failures = [aws_instance.ai_lab]
}

run "local_login_off_requires_cognito" {
  command = plan

  variables {
    open_webui_enable_local_login = false
    enable_cognito                = false
  }

  expect_failures = [aws_instance.ai_lab]
}

# ---- Origin lockdown ----

run "origin_lockdown_requires_cloudflare_access" {
  command = plan

  variables {
    enable_domain_access     = true
    enable_origin_lockdown   = true
    enable_cloudflare_access = false
  }

  expect_failures = [aws_security_group.alb]
}

run "origin_lockdown_extra_cidrs_rejects_the_whole_internet" {
  command = plan

  variables {
    origin_lockdown_extra_cidrs = ["0.0.0.0/0"]
  }

  expect_failures = [var.origin_lockdown_extra_cidrs]
}

# ---- Auto-stop ----

run "auto_stop_idle_minutes_above_a_day_is_rejected" {
  command = plan

  variables {
    auto_stop_idle_minutes = 2000
  }

  expect_failures = [var.auto_stop_idle_minutes]
}

run "auto_stop_max_uptime_below_the_floor_is_rejected" {
  command = plan

  variables {
    auto_stop_max_uptime_minutes = 10
  }

  expect_failures = [var.auto_stop_max_uptime_minutes]
}

run "auto_stop_off_creates_no_watchdog" {
  command = plan

  variables {
    auto_stop_idle_minutes       = 0
    auto_stop_max_uptime_minutes = 0
  }

  assert {
    condition     = length(aws_lambda_function.auto_stop_watchdog) == 0
    error_message = "With both auto-stop controls at 0, no watchdog Lambda may be created."
  }
}

run "auto_stop_alert_email_must_look_like_an_email" {
  command = plan

  variables {
    auto_stop_alert_email = "not-an-email"
  }

  expect_failures = [var.auto_stop_alert_email]
}

# ---- Grafana telemetry ----

run "grafana_telemetry_needs_endpoint_and_secret" {
  command = plan

  variables {
    enable_grafana_telemetry = true
  }

  expect_failures = [aws_instance.ai_lab]
}

run "grafana_telemetry_plan_succeeds_when_configured" {
  command = plan

  variables {
    enable_grafana_telemetry       = true
    grafana_otlp_endpoint          = "https://otlp-gateway-prod-us-east-3.grafana.net/otlp"
    grafana_otlp_instance_id       = "1234567"
    grafana_credentials_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:grafana-AbCdEf"
  }
}

run "grafana_endpoint_must_be_https" {
  command = plan

  variables {
    grafana_otlp_endpoint = "http://otlp-gateway-prod-us-east-3.grafana.net/otlp"
  }

  expect_failures = [var.grafana_otlp_endpoint]
}

run "grafana_endpoint_rejects_trailing_slash" {
  command = plan

  variables {
    grafana_otlp_endpoint = "https://otlp-gateway-prod-us-east-3.grafana.net/otlp/"
  }

  expect_failures = [var.grafana_otlp_endpoint]
}

run "grafana_secret_arn_must_be_valid" {
  command = plan

  variables {
    grafana_credentials_secret_arn = "not-an-arn"
  }

  expect_failures = [var.grafana_credentials_secret_arn]
}

run "grafana_instance_id_must_be_numeric" {
  command = plan

  variables {
    grafana_otlp_instance_id = "stack-1234567"
  }

  expect_failures = [var.grafana_otlp_instance_id]
}

run "vuln_mcp_off_creates_nothing" {
  command = plan

  assert {
    condition     = length(aws_lambda_function.vuln_mcp) == 0 && length(aws_lambda_function_url.vuln_mcp) == 0
    error_message = "With vuln_mcp_table_name unset, no MCP Lambda or Function URL may be created."
  }
}

run "vuln_mcp_on_creates_function_and_url" {
  command = plan

  variables {
    vuln_mcp_table_name       = "aiwebdemo-vuln-findings"
    vuln_mcp_token_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vuln-mcp-token-AbCdEf"
  }

  assert {
    condition     = length(aws_lambda_function.vuln_mcp) == 1 && length(aws_lambda_function_url.vuln_mcp) == 1
    error_message = "Setting vuln_mcp_table_name must create one Lambda and one Function URL."
  }

  assert {
    condition     = aws_lambda_function.vuln_mcp["vuln_mcp"].handler == "mcp_handler.lambda_handler"
    error_message = "The MCP Lambda must use mcp_handler.lambda_handler."
  }
}

run "vuln_mcp_table_needs_a_token_secret" {
  command = plan

  variables {
    vuln_mcp_table_name       = "aiwebdemo-vuln-findings"
    vuln_mcp_token_secret_arn = null
  }

  expect_failures = [aws_lambda_function.vuln_mcp]
}

run "vuln_mcp_token_secret_arn_must_be_valid" {
  command = plan

  variables {
    vuln_mcp_token_secret_arn = "not-an-arn"
  }

  expect_failures = [var.vuln_mcp_token_secret_arn]
}

run "vuln_mcp_table_name_must_be_valid" {
  command = plan

  variables {
    vuln_mcp_table_name = "bad name!"
  }

  expect_failures = [var.vuln_mcp_table_name]
}
