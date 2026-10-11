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
      id         = "vpc-0123456789abcdef0"
      cidr_block = "172.31.0.0/16"
      cidr_block_associations = [
        { association_id = "vpc-cidr-assoc-0aaa", cidr_block = "172.31.0.0/16", state = "associated" },
        { association_id = "vpc-cidr-assoc-0bbb", cidr_block = "100.64.0.0/16", state = "associated" },
      ]
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
  mock_resource "aws_lb" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/test-alb/0123456789abcdef"
    }
  }

  mock_resource "aws_lb_target_group" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:us-east-1:123456789012:targetgroup/test-tg/0123456789abcdef"
    }
  }

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
  enable_domain_access             = false
  enable_cloudflare_access         = false
  enable_alb_http_redirect         = false
  enable_cognito                   = false
  enable_origin_lockdown           = false
  enable_ssh                       = false
  open_webui_enable_local_login    = true
  allowed_ssh_cidr                 = null
  ssh_key_name                     = null
  extra_egress_cidrs               = []
  enable_grafana_telemetry         = false
  grafana_otlp_endpoint            = null
  grafana_otlp_instance_id         = null
  grafana_credentials_secret_arn   = null
  grafana_cloudwatch_account_id    = null
  grafana_cloudwatch_external_id   = null
  grafana_dashboard_url            = null
  origin_lockdown_extra_cidrs      = []
  cloudflare_account_id            = null
  cloudflare_api_token_secret_arn  = null
  cloudflare_access_team_domain    = null
  control_panel_url                = null
  control_panel_api_from_ssm       = false
  control_panel_api_url            = null
  control_panel_api_id             = null
  control_panel_authorizer_id      = null
  control_panel_holding_pool_id    = null
  control_panel_users_table        = null
  control_panel_entitlements_table = null
  control_panel_bucket             = null
  cognito_domain_prefix            = null
  cognito_extra_users              = []
  root_volume_size                 = 80
  instance_type                    = "c7i.4xlarge"
  ollama_context_length            = 16384
  open_webui_host_port             = 8080
  auto_stop_idle_minutes           = 60
  auto_stop_max_uptime_minutes     = 0
  vuln_mcp_table_name              = null
  vuln_mcp_token_secret_arn        = null
  log_mcp_loki_url                 = null
  log_mcp_loki_user                = null
  log_mcp_loki_token_secret_arn    = null
  log_mcp_stream_selector          = "service_name=~\".+\""
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

  assert {
    condition     = strcontains(cloudflare_workers_script.lab_unavailable_page["domain"].content, "export default {") && strcontains(cloudflare_workers_script.lab_unavailable_page["domain"].content, "fetch(request)")
    error_message = "The module Worker must export a fetch handler."
  }
}

# ---- Control panel API settings from SSM ----
# The API stack (dashboards/api/terraform) publishes /<project_name>/control-panel-api/settings.

run "api_settings_are_not_read_unless_asked" {
  command = plan

  assert {
    condition     = length(data.aws_ssm_parameters_by_path.control_panel_api) == 0 && length(data.aws_ssm_parameter.control_panel_api) == 0
    error_message = "With control_panel_api_from_ssm = false the lab must not read the API's parameter."
  }

  assert {
    condition     = alltrue([for v in values(local.panel_api) : v == null])
    error_message = "Without the parameter or tfvars values, the lab must have no API settings."
  }
}

run "api_settings_come_from_the_published_parameter" {
  command = plan

  variables {
    project_name               = "aiwebdemo"
    control_panel_api_from_ssm = true
  }

  override_data {
    target = data.aws_ssm_parameters_by_path.control_panel_api
    values = { names = ["/aiwebdemo/control-panel-api/settings"] }
  }

  override_data {
    target = data.aws_ssm_parameter.control_panel_api
    values = {
      insecure_value = "{\"api_url\":\"https://abc123defg.execute-api.us-east-1.amazonaws.com/\",\"api_id\":\"abc123defg\",\"authorizer_id\":\"auth01\",\"holding_pool_id\":\"us-east-1_HOLDING01\",\"users_table\":\"panel_users\",\"entitlements_table\":\"instance_entitlements\"}"
    }
  }

  assert {
    condition = local.panel_api == {
      url                = "https://abc123defg.execute-api.us-east-1.amazonaws.com/"
      api_id             = "abc123defg"
      authorizer_id      = "auth01"
      holding_pool_id    = "us-east-1_HOLDING01"
      users_table        = "panel_users"
      entitlements_table = "instance_entitlements"
    }
    error_message = "All six API settings must come from the parameter when tfvars leaves them null."
  }

  assert {
    condition     = length(aws_dynamodb_table_item.panel_demo_user) > 0
    error_message = "The users table name from the parameter must seed the demo users."
  }
}

run "api_settings_in_tfvars_win_over_the_parameter" {
  command = plan

  variables {
    project_name               = "aiwebdemo"
    control_panel_api_from_ssm = true
    control_panel_api_url      = "https://override.example.com/"
  }

  override_data {
    target = data.aws_ssm_parameters_by_path.control_panel_api
    values = { names = ["/aiwebdemo/control-panel-api/settings"] }
  }

  override_data {
    target = data.aws_ssm_parameter.control_panel_api
    values = {
      insecure_value = "{\"api_url\":\"https://abc123defg.execute-api.us-east-1.amazonaws.com/\",\"api_id\":\"abc123defg\",\"authorizer_id\":\"auth01\",\"holding_pool_id\":\"us-east-1_HOLDING01\",\"users_table\":\"panel_users\",\"entitlements_table\":\"instance_entitlements\"}"
    }
  }

  assert {
    condition     = local.panel_api.url == "https://override.example.com/" && local.panel_api.api_id == "abc123defg"
    error_message = "A value set in tfvars must win; the rest must still come from the parameter."
  }
}

run "missing_api_parameter_is_a_warning_not_a_failure" {
  command = plan

  variables {
    project_name               = "aiwebdemo"
    control_panel_api_from_ssm = true
  }

  override_data {
    target = data.aws_ssm_parameters_by_path.control_panel_api
    values = { names = [] }
  }

  expect_failures = [check.control_panel_api_settings_found]

  assert {
    condition     = length(data.aws_ssm_parameter.control_panel_api) == 0 && alltrue([for v in values(local.panel_api) : v == null])
    error_message = "Without the parameter the lab must plan without API settings instead of failing."
  }
}

# ---- Instance type, image and placement ----

run "cpu_instance_boots_plain_ubuntu" {
  command = plan

  variables {
    instance_type = "c7i.4xlarge"
  }

  assert {
    condition     = length(data.aws_ssm_parameter.gpu_ami) == 0 && !local.gpu_instance
    error_message = "A CPU instance type must not look up the GPU AMI."
  }
}

run "nvidia_gpu_instance_boots_the_gpu_ami" {
  command = plan

  variables {
    instance_type = "g6.xlarge"
  }

  assert {
    condition     = length(data.aws_ssm_parameter.gpu_ami) == 1 && local.gpu_instance
    error_message = "An NVIDIA GPU instance type must boot the Deep Learning Base GPU AMI."
  }
}

run "instance_type_no_zone_offers_is_rejected" {
  command = plan

  override_data {
    target = data.aws_subnets.lab
    values = { ids = [] }
  }

  expect_failures = [aws_instance.ai_lab]
}

run "root_volume_smaller_than_the_ami_is_rejected" {
  command = plan

  variables {
    root_volume_size = 80
  }

  override_data {
    target = data.aws_ami.lab
    values = {
      root_device_name      = "/dev/sda1"
      block_device_mappings = [{ device_name = "/dev/sda1", ebs = { volume_size = "100" }, no_device = "", virtual_name = "" }]
    }
  }

  expect_failures = [aws_instance.ai_lab]
}

run "ollama_context_length_out_of_range_is_rejected" {
  command = plan

  variables {
    ollama_context_length = 1024
  }

  expect_failures = [var.ollama_context_length]
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

# The ALB answers only Cloudflare when locked down (on 443 and on the optional port 80
# redirect), forwards only to Open WebUI inside the VPC, and offers TLS 1.2 or later.
run "alb_is_locked_down_and_forwards_only_to_open_webui" {
  command = apply

  plan_options {
    target = [aws_security_group.alb, aws_lb_listener.https, aws_lb_listener.http_redirect]
  }

  variables {
    enable_domain_access          = true
    enable_alb_http_redirect      = true
    enable_origin_lockdown        = true
    enable_cloudflare_access      = true
    cloudflare_account_id         = "0123456789abcdef0123456789abcdef"
    cloudflare_access_team_domain = "example.cloudflareaccess.com"
    acm_certificate_arn           = "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000"
  }

  assert {
    condition     = alltrue([for rule in aws_security_group.alb["domain"].ingress : !contains(rule.cidr_blocks, "0.0.0.0/0")])
    error_message = "With origin lockdown, neither port 443 nor the port 80 redirect may be open to the whole Internet."
  }

  assert {
    condition = alltrue([for rule in aws_security_group.alb["domain"].egress :
    rule.protocol == "tcp" && rule.from_port == 8080 && rule.to_port == 8080 && toset(rule.cidr_blocks) == toset(["172.31.0.0/16", "100.64.0.0/16"])])
    error_message = "The ALB may only send traffic to Open WebUI's port, to every IPv4 range of the VPC and nothing else."
  }

  assert {
    condition     = aws_lb_listener.https["domain"].ssl_policy == "ELBSecurityPolicy-TLS13-1-2-2021-06"
    error_message = "The HTTPS listener must use a TLS 1.2+ policy with strong ciphers."
  }
}

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

run "auto_stop_absolute_max_below_the_floor_is_rejected" {
  command = plan

  variables {
    auto_stop_absolute_max_minutes = 10
  }

  expect_failures = [var.auto_stop_absolute_max_minutes]
}

run "auto_stop_absolute_max_above_the_ceiling_is_rejected" {
  command = plan

  variables {
    auto_stop_absolute_max_minutes = 20161
  }

  expect_failures = [var.auto_stop_absolute_max_minutes]
}

run "auto_stop_absolute_max_must_be_whole_minutes" {
  command = plan

  variables {
    auto_stop_absolute_max_minutes = 60.5
  }

  expect_failures = [var.auto_stop_absolute_max_minutes]
}

run "auto_stop_max_resets_out_of_range_is_rejected" {
  command = plan

  variables {
    auto_stop_max_resets = 51
  }

  expect_failures = [var.auto_stop_max_resets]
}

run "auto_stop_absolute_max_alone_turns_auto_stop_on_and_reaches_every_reader" {
  command = apply

  variables {
    auto_stop_idle_minutes         = 0
    auto_stop_max_uptime_minutes   = 0
    auto_stop_absolute_max_minutes = 720
    auto_stop_max_resets           = 3
  }

  assert {
    condition     = jsondecode(aws_ssm_parameter.auto_stop.value).enabled == true && jsondecode(aws_ssm_parameter.auto_stop.value).absolute_max_minutes == 720 && jsondecode(aws_ssm_parameter.auto_stop.value).max_resets == 3
    error_message = "The auto-stop setting must carry the absolute limit and the reset count, and be on when only the absolute limit is set."
  }

  assert {
    condition     = aws_lambda_function.auto_stop_watchdog["auto_stop"].environment[0].variables["ABSOLUTE_MAX_MINUTES"] == "720"
    error_message = "The watchdog must be told the absolute limit."
  }

  assert {
    condition     = aws_ssm_parameter.auto_stop_reset_count.name == "/${var.project_name}/auto-stop/reset-count" && !strcontains(aws_iam_role_policy.auto_stop_agent.policy, aws_ssm_parameter.auto_stop_reset_count.arn)
    error_message = "The reset count parameter must exist, and the instance has no need to read it."
  }
}

run "auto_stop_defaults_keep_the_new_limits_off" {
  command = plan

  assert {
    condition     = jsondecode(aws_ssm_parameter.auto_stop.value).absolute_max_minutes == 0 && jsondecode(aws_ssm_parameter.auto_stop.value).max_resets == 0
    error_message = "Unless set, the absolute limit and the reset count must be off (0)."
  }
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

run "auto_stop_reset_parameter_is_created_and_scoped_to_the_readers" {
  command = apply

  variables {
    auto_stop_idle_minutes       = 60
    auto_stop_max_uptime_minutes = 120
  }

  assert {
    condition     = aws_ssm_parameter.auto_stop_reset.name == "/${var.project_name}/auto-stop/reset-at"
    error_message = "The timer reset parameter must be /<project>/auto-stop/reset-at."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.auto_stop_agent.policy, aws_ssm_parameter.auto_stop_reset.arn) && !strcontains(aws_iam_role_policy.auto_stop_agent.policy, "PutParameter")
    error_message = "The instance may read the reset parameter and must never write it."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.auto_stop_watchdog["auto_stop"].policy, aws_ssm_parameter.auto_stop_reset.arn) && !strcontains(aws_iam_role_policy.auto_stop_watchdog["auto_stop"].policy, "PutParameter")
    error_message = "The watchdog may read the reset parameter and must never write it."
  }

  assert {
    condition     = aws_lambda_function.auto_stop_watchdog["auto_stop"].environment[0].variables["RESET_PARAMETER"] == aws_ssm_parameter.auto_stop_reset.name
    error_message = "The watchdog must be told which parameter holds the timer reset."
  }

  assert {
    condition     = aws_lambda_function.auto_stop_watchdog["auto_stop"].environment[0].variables["POLICY_PARAMETER"] == aws_ssm_parameter.auto_stop_policy.name && strcontains(aws_iam_role_policy.auto_stop_watchdog["auto_stop"].policy, aws_ssm_parameter.auto_stop_policy.arn)
    error_message = "The watchdog must be told which parameter holds the timer policy and be allowed to read it."
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

run "vuln_mcp_off_grants_no_token_secret_to_the_instance" {
  command = plan

  assert {
    condition     = !strcontains(aws_iam_role_policy.open_webui_admin_password.policy, "vuln-mcp-token")
    error_message = "With the vulnerability MCP off, the instance role must not be able to read its token secret."
  }
}

run "vuln_mcp_on_lets_the_instance_read_only_the_token_secret" {
  command = plan

  variables {
    vuln_mcp_table_name       = "aiwebdemo-vuln-findings"
    vuln_mcp_token_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vuln-mcp-token-AbCdEf"
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.open_webui_admin_password.policy, "secret:vuln-mcp-token-AbCdEf")
    error_message = "With the vulnerability MCP on, the instance role must be able to read the token secret so the registration script can connect Open WebUI."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.open_webui_admin_password.policy, "dynamodb")
    error_message = "The instance role must not gain any DynamoDB access; only the Lambda reads the table."
  }
}

run "the_instance_role_can_read_only_the_tool_token_prefix_besides_its_own_secrets" {
  command = plan

  assert {
    condition     = strcontains(aws_iam_role_policy.open_webui_admin_password.policy, "secret:aiwebdemo/tool-tokens/*")
    error_message = "The instance role must be able to read the secrets the control panel offers as tool server tokens."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.open_webui_admin_password.policy, "secret:*")
    error_message = "The instance role must never be able to read every secret."
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

run "vuln_mcp_token_secret_arn_must_be_the_full_arn" {
  command = plan

  variables {
    vuln_mcp_table_name       = "aiwebdemo-vuln-findings"
    vuln_mcp_token_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vuln-mcp-token-AbCdE"
  }

  expect_failures = [var.vuln_mcp_token_secret_arn]
}

run "grafana_secret_arn_must_be_the_full_arn" {
  command = plan

  variables {
    grafana_credentials_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:grafana"
  }

  expect_failures = [var.grafana_credentials_secret_arn]
}

run "vuln_mcp_table_name_must_be_valid" {
  command = plan

  variables {
    vuln_mcp_table_name = "bad name!"
  }

  expect_failures = [var.vuln_mcp_table_name]
}

# ---- Grafana CloudWatch role and dashboard link ----

run "grafana_cloudwatch_role_is_created_when_both_values_are_set" {
  command = plan

  variables {
    grafana_cloudwatch_account_id  = "123456789012"
    grafana_cloudwatch_external_id = "abc123-external"
  }

  assert {
    condition     = length(aws_iam_role.grafana_cloudwatch) == 1
    error_message = "Setting the Grafana account ID and external ID must create the read-only role."
  }
}

run "grafana_cloudwatch_role_is_not_created_by_default" {
  command = plan

  assert {
    condition     = length(aws_iam_role.grafana_cloudwatch) == 0
    error_message = "No Grafana role may exist unless both values are set."
  }
}

run "grafana_cloudwatch_account_id_must_be_twelve_digits" {
  command = plan

  variables {
    grafana_cloudwatch_account_id = "1234"
  }

  expect_failures = [var.grafana_cloudwatch_account_id]
}

run "grafana_dashboard_url_must_be_https" {
  command = plan

  variables {
    grafana_dashboard_url = "http://example.grafana.net/d/ai-lab-overview"
  }

  expect_failures = [var.grafana_dashboard_url]
}

run "log_tools_are_off_by_default" {
  command = plan

  variables {
    vuln_mcp_table_name       = "aiwebdemo-vuln-findings"
    vuln_mcp_token_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vuln-mcp-token-AbCdEf"
  }

  assert {
    condition     = !contains(keys(aws_lambda_function.vuln_mcp["vuln_mcp"].environment[0].variables), "LOKI_URL")
    error_message = "Without the three log_mcp settings, the MCP Lambda must get no Loki settings."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.vuln_mcp["vuln_mcp"].policy, "loki")
    error_message = "Without the log tools, the Lambda role must not be able to read a Loki secret."
  }
}

run "log_tools_on_give_the_lambda_the_loki_settings_and_only_that_secret" {
  command = plan

  variables {
    vuln_mcp_table_name           = "aiwebdemo-vuln-findings"
    vuln_mcp_token_secret_arn     = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vuln-mcp-token-AbCdEf"
    log_mcp_loki_url              = "https://logs-prod-012.grafana.net"
    log_mcp_loki_user             = "123456"
    log_mcp_loki_token_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:loki-read-token-AbCdEf"
  }

  assert {
    condition     = aws_lambda_function.vuln_mcp["vuln_mcp"].environment[0].variables["LOKI_URL"] == "https://logs-prod-012.grafana.net"
    error_message = "The MCP Lambda must be given the Loki address."
  }

  assert {
    condition     = strcontains(aws_iam_role_policy.vuln_mcp["vuln_mcp"].policy, "secret:loki-read-token-AbCdEf")
    error_message = "The Lambda role must be able to read the Loki token secret."
  }

  assert {
    condition     = !strcontains(aws_iam_role_policy.open_webui_admin_password.policy, "loki")
    error_message = "The instance role must not be able to read the Loki token; only the Lambda reads it."
  }
}

run "log_tools_need_all_three_settings" {
  command = plan

  variables {
    vuln_mcp_table_name       = "aiwebdemo-vuln-findings"
    vuln_mcp_token_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vuln-mcp-token-AbCdEf"
    log_mcp_loki_url          = "https://logs-prod-012.grafana.net"
  }

  expect_failures = [check.log_mcp_settings_are_complete]
}

run "log_mcp_loki_url_must_be_https" {
  command = plan

  variables {
    log_mcp_loki_url = "http://logs-prod-012.grafana.net"
  }

  expect_failures = [var.log_mcp_loki_url]
}

run "log_mcp_loki_user_must_be_numeric" {
  command = plan

  variables {
    log_mcp_loki_user = "me@example.com"
  }

  expect_failures = [var.log_mcp_loki_user]
}

run "log_mcp_stream_selector_must_be_a_plain_matcher" {
  command = plan

  variables {
    log_mcp_stream_selector = "a=\"b\"} or {c=\"d\""
  }

  expect_failures = [var.log_mcp_stream_selector]
}
