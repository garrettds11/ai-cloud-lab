# Mocked AWS plan: no account access or resources are required.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
}
mock_provider "archive" {}

variables {
  aws_region                          = "us-east-1"
  lab_instance_id                     = "i-0123456789abcdef0"
  lab_private_ip                      = "172.31.1.10"
  lab_vpc_id                          = "vpc-0123456789abcdef0"
  lab_route_table_ids                 = ["rtb-0123456789abcdef0"]
  lab_security_group_id               = "sg-0123456789abcdef0"
  lab_events_table_name               = "control_panel_events"
  public_hostname                     = "lab.example.com"
  open_webui_image_digest             = "ghcr.io/open-webui/open-webui@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  ssh_public_key                      = "ssh-ed25519 AAAA scanner-only"
  ssh_private_key_secret_arn          = "arn:aws:secretsmanager:us-east-1:123456789012:secret:scanner-ssh-AbCdEf"
  greenbone_admin_password_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:greenbone-admin-AbCdEf"
  control_panel_api_id                = "api123"
  control_panel_jwt_issuer            = "https://issuer.example"
  control_panel_jwt_audiences         = ["client"]
  control_panel_functions = {
    customer = {
      arn      = "arn:aws:lambda:us-east-1:123456789012:function:panel-customer"
      role_arn = "arn:aws:iam::123456789012:role/panel-customer"
    }
    admin = {
      arn      = "arn:aws:lambda:us-east-1:123456789012:function:panel-admin"
      role_arn = "arn:aws:iam::123456789012:role/panel-admin"
    }
  }
}

run "assessment_reads_only_configured_targets" {
  command = plan
  assert {
    condition = jsondecode(aws_iam_role_policy.assessment.policy).Statement[0].Resource == [
      "arn:aws:lambda:us-east-1:123456789012:function:panel-admin",
      "arn:aws:lambda:us-east-1:123456789012:function:panel-customer"
    ] && !strcontains(aws_iam_role_policy.assessment.policy, "lambda:InvokeFunction") && !strcontains(aws_iam_role_policy.assessment.policy, "iam:Put")
    error_message = "Assessment must read only the configured functions and never invoke or change them."
  }
  assert {
    condition     = length(jsondecode(aws_iam_role_policy.assessment.policy).Statement) == 3
    error_message = "Absent layers/managed policies must not introduce wildcard read permissions."
  }
  assert {
    condition     = alltrue([for route, arn in jsondecode(local.assessment_config).expected_routes : arn == var.control_panel_functions[startswith(split(" ", route)[1], "/admin/") ? "admin" : "customer"].arn]) && length(local.api_routes) > 0
    error_message = "Route baseline must preserve the API customer/admin separation."
  }
}

run "layer_and_managed_policy_reads_are_explicit" {
  command = plan
  variables {
    control_panel_layer_version_arns = ["arn:aws:lambda:us-east-1:123456789012:layer:packages:3"]
    control_panel_policy_arns        = ["arn:aws:iam::123456789012:policy/panel-read"]
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.assessment.policy).Statement[3].Resource == ["arn:aws:lambda:us-east-1:123456789012:layer:packages:3"] && jsondecode(aws_iam_role_policy.assessment.policy).Statement[4].Resource == ["arn:aws:iam::123456789012:policy/panel-read"]
    error_message = "Only the supplied layer versions and managed policies may be downloaded/read."
  }
}

run "report_storage_and_control_panel_target" {
  command = plan
  assert {
    condition     = aws_s3_bucket_public_access_block.reports.block_public_policy && aws_s3_bucket_public_access_block.reports.block_public_acls && aws_instance.scanner.root_block_device[0].encrypted
    error_message = "Reports and scanner disk must remain protected."
  }
  assert {
    condition     = jsondecode(local.assessment_config).allowed_origins == ["https://cp.aiwebdemo.click"] && length(aws_security_group.scanner.ingress) == 0
    error_message = "The panel baseline must use its configured origin and the scanner must expose no inbound services."
  }
}
