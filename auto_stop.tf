# Auto-stop, with two independent controls. Each is off when set to 0.
#
#   auto_stop_idle_minutes: idle shutdown. The instance stops after that many minutes
#   with no active Open WebUI users and no reply being generated. People who are active
#   are never stopped by this.
#
#   auto_stop_max_uptime_minutes: a hard time limit. The instance stops that many
#   minutes after it boots even if people are using it, after an email warning.
#
# The hard limit counts from boot, or from the last timer reset if that is later. The
# control panel's reset button writes the time to the SSM parameter below
# (aws_ssm_parameter.auto_stop_reset). Terraform creates that parameter but never
# overwrites its value, so a normal apply does not undo a reset. Both layers use
# max(boot or launch time, reset time), so a reset left over from an earlier run is
# ignored after the next start.
#
# auto_stop_absolute_max_minutes is a third limit, counted from boot only. No reset and no
# panel setting moves it, and neither is read when it is checked, so a bad value written to them
# cannot extend it. auto_stop_max_resets is how many times the Reset button may extend the hard
# limit in one run (the count is runtime state in the reset-count parameter below).
#
# Layer 1 runs on the instance (scripts/ai-lab-idle-check.sh, installed by cloud-init)
# and does both. Layer 2 is this independent watchdog (lambda/auto_stop_watchdog.py),
# run by EventBridge every few minutes. It enforces the hard limit a few minutes late if
# the instance has not stopped itself, stops an instance whose idle shutdown failed (or
# whose monitor is silent while the load balancer is quiet), and emails alerts.

locals {
  auto_stop_check_minutes  = 5
  auto_stop_parameter_name = "/${var.project_name}/auto-stop"
  # Runtime state, written by the control panel's reset button: epoch seconds, "0" = never.
  auto_stop_reset_parameter_name = "${local.auto_stop_parameter_name}/reset-at"
  # Runtime state, written by the control panel's timer policy page: JSON with idle_minutes and
  # max_uptime_minutes, each 0 or a value at or below the limits above. "{}" = no override.
  auto_stop_policy_parameter_name = "${local.auto_stop_parameter_name}/policy"
  # Runtime state, written by the control panel's reset button: "<launch epoch seconds>:<resets used>",
  # so a count from an earlier run is ignored after the next start.
  auto_stop_reset_count_parameter_name = "${local.auto_stop_parameter_name}/reset-count"
}

check "auto_stop_alert_email" {
  assert {
    condition     = !local.auto_stop_enabled || var.auto_stop_alert_email != null
    error_message = "Auto-stop is on but auto_stop_alert_email is not set, so the shutdown warning and not-reporting alerts have nowhere to go. Set auto_stop_alert_email in terraform.tfvars."
  }
}

# absolute_max_minutes is measured from boot only: no reset and no panel policy moves it (0 = off).
# max_resets is how many times the Reset button may extend the hard limit in one run (0 = no limit);
# the panel policy may only lower it.
# Settings read by the instance-side agent every minute. Keeping them here, not in
# user-data, means changing the toggle or the timeout updates in place and never
# replaces the instance.
resource "aws_ssm_parameter" "auto_stop" {
  #checkov:skip=CKV2_AWS_34:The value is not secret (an ID, URL, JSON rule or timestamp); SecureString would only add a KMS dependency for every reader
  name = local.auto_stop_parameter_name
  type = "String"
  value = jsonencode({
    enabled              = local.auto_stop_enabled
    idle_minutes         = var.auto_stop_idle_minutes
    max_uptime_minutes   = var.auto_stop_max_uptime_minutes
    absolute_max_minutes = var.auto_stop_absolute_max_minutes
    max_resets           = var.auto_stop_max_resets
  })
}

# When the hard-limit timer was last reset from the control panel (epoch seconds). Only the
# parameter's existence is managed here; its value is runtime state, so Terraform never
# reverts it. "0" means never reset.
resource "aws_ssm_parameter" "auto_stop_reset" {
  #checkov:skip=CKV2_AWS_34:The value is not secret (an ID, URL, JSON rule or timestamp); SecureString would only add a KMS dependency for every reader
  name        = local.auto_stop_reset_parameter_name
  description = "Epoch seconds of the last auto-stop timer reset from the control panel. Runtime state; Terraform does not overwrite it."
  type        = "String"
  value       = "0"

  lifecycle {
    ignore_changes = [value]
  }
}

# How many times the hard limit was reset during this run, from the control panel ("<launch epoch>:<count>").
# Only the parameter's existence is managed here; its value is runtime state.
resource "aws_ssm_parameter" "auto_stop_reset_count" {
  #checkov:skip=CKV2_AWS_34:The value is not secret (an ID, URL, JSON rule or timestamp); SecureString would only add a KMS dependency for every reader
  name        = local.auto_stop_reset_count_parameter_name
  description = "Timer resets used in the current run, as <launch epoch seconds>:<count>. Runtime state; Terraform does not overwrite it."
  type        = "String"
  value       = "0:0"

  lifecycle {
    ignore_changes = [value]
  }
}

# The timer policy set from the control panel. It can only make the limits above shorter, never
# longer or off; the instance monitor takes the lower of the two. Only the parameter's existence
# is managed here, its value is runtime state, so Terraform never reverts it.
resource "aws_ssm_parameter" "auto_stop_policy" {
  #checkov:skip=CKV2_AWS_34:The value is not secret (an ID, URL, JSON rule or timestamp); SecureString would only add a KMS dependency for every reader
  name        = local.auto_stop_policy_parameter_name
  description = "Timer policy set from the control panel (idle and session minutes, at or below the Terraform limits). Runtime state; Terraform does not overwrite it."
  type        = "String"
  value       = "{}"

  lifecycle {
    ignore_changes = [value]
  }
}

# Lets the agent read its settings and the last reset, and publish its idle state for the watchdog.
resource "aws_iam_role_policy" "auto_stop_agent" {
  name = "${var.project_name}-auto-stop-agent"
  role = aws_iam_role.ssm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = [aws_ssm_parameter.auto_stop.arn, aws_ssm_parameter.auto_stop_reset.arn, aws_ssm_parameter.auto_stop_policy.arn]
      },
      {
        Effect    = "Allow"
        Action    = "cloudwatch:PutMetricData"
        Resource  = "*"
        Condition = { StringEquals = { "cloudwatch:namespace" = "AILab" } }
      }
    ]
  })
}

resource "aws_sns_topic" "auto_stop" {
  for_each = local.auto_stop_resources

  name = "${var.project_name}-auto-stop-alerts"
  # Encrypt stored messages with the AWS managed SNS key. The watchdog publishes with its own
  # IAM role through the SNS API, which that key's policy allows; no key permissions are needed.
  kms_master_key_id = "alias/aws/sns"
}

# Email subscriptions stay "pending" until the recipient clicks the confirmation link.
resource "aws_sns_topic_subscription" "auto_stop_email" {
  for_each = var.auto_stop_alert_email != null ? local.auto_stop_resources : {}

  topic_arn = aws_sns_topic.auto_stop[each.key].arn
  protocol  = "email"
  endpoint  = var.auto_stop_alert_email
}

data "archive_file" "auto_stop_watchdog" {
  for_each = local.auto_stop_resources

  type        = "zip"
  source_file = "${path.module}/lambda/auto_stop_watchdog.py"
  output_path = "${path.module}/.terraform/auto_stop_watchdog.zip"
}

resource "aws_iam_role" "auto_stop_watchdog" {
  for_each = local.auto_stop_resources

  name_prefix = "${var.project_name}-watchdog-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project_name}-auto-stop-watchdog-role"
  }
}

resource "aws_iam_role_policy_attachment" "auto_stop_watchdog_logs" {
  for_each = local.auto_stop_resources

  role       = aws_iam_role.auto_stop_watchdog[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "auto_stop_watchdog" {
  for_each = local.auto_stop_resources

  name = "${var.project_name}-auto-stop-watchdog"
  role = aws_iam_role.auto_stop_watchdog[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ec2:DescribeInstances"
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "ec2:StopInstances"
        Resource = aws_instance.ai_lab.arn
      },
      {
        Effect   = "Allow"
        Action   = "cloudwatch:GetMetricStatistics"
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = [aws_ssm_parameter.auto_stop_reset.arn, aws_ssm_parameter.auto_stop_policy.arn]
      },
      {
        Effect   = "Allow"
        Action   = "sns:Publish"
        Resource = aws_sns_topic.auto_stop[each.key].arn
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "auto_stop_watchdog" {
  #checkov:skip=CKV_AWS_338:Short retention is deliberate for a lab: the logs are operational and cost and privacy favour 14 days
  #checkov:skip=CKV_AWS_158:Logs are encrypted at rest by CloudWatch; a customer managed key adds cost for no lab benefit
  for_each = local.auto_stop_resources

  name              = "/aws/lambda/${var.project_name}-auto-stop-watchdog"
  retention_in_days = 14
}

resource "aws_lambda_function" "auto_stop_watchdog" {
  #checkov:skip=CKV_AWS_50:X-Ray adds cost and nothing for a five-minute scheduled check; results are logged as JSON
  #checkov:skip=CKV_AWS_272:Code is built from this repository by Terraform; code signing adds a signing profile for no lab benefit
  #checkov:skip=CKV_AWS_117:The watchdog calls only AWS APIs; putting it in a VPC would need NAT or endpoints for no gain
  #checkov:skip=CKV_AWS_116:EventBridge retries failed runs and the next run is five minutes later; a failed run has nothing to replay
  #checkov:skip=CKV_AWS_115:Reserved concurrency fails on new accounts whose concurrency quota is 10; EventBridge invokes it once per five minutes
  #checkov:skip=CKV_AWS_173:Environment variables hold only names and ARNs, no secrets; Lambda encrypts them at rest with an AWS managed key
  for_each = local.auto_stop_resources

  function_name    = "${var.project_name}-auto-stop-watchdog"
  role             = aws_iam_role.auto_stop_watchdog[each.key].arn
  runtime          = "python3.12"
  handler          = "auto_stop_watchdog.lambda_handler"
  filename         = data.archive_file.auto_stop_watchdog[each.key].output_path
  source_code_hash = data.archive_file.auto_stop_watchdog[each.key].output_base64sha256
  timeout          = 30

  environment {
    variables = {
      INSTANCE_ID          = aws_instance.ai_lab.id
      IDLE_MINUTES         = tostring(var.auto_stop_idle_minutes)
      MAX_UPTIME_MINUTES   = tostring(var.auto_stop_max_uptime_minutes)
      ABSOLUTE_MAX_MINUTES = tostring(var.auto_stop_absolute_max_minutes)
      CHECK_MINUTES        = tostring(local.auto_stop_check_minutes)
      SNS_TOPIC_ARN        = aws_sns_topic.auto_stop[each.key].arn
      ALB_DIMENSION        = var.enable_domain_access ? aws_lb.domain["domain"].arn_suffix : ""
      RESET_PARAMETER      = aws_ssm_parameter.auto_stop_reset.name
      POLICY_PARAMETER     = aws_ssm_parameter.auto_stop_policy.name
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.auto_stop_watchdog_logs,
    aws_iam_role_policy.auto_stop_watchdog,
    aws_cloudwatch_log_group.auto_stop_watchdog
  ]
}

resource "aws_cloudwatch_event_rule" "auto_stop_watchdog" {
  for_each = local.auto_stop_resources

  name                = "${var.project_name}-auto-stop-watchdog"
  description         = "Runs the AI lab auto-stop watchdog"
  schedule_expression = "rate(${local.auto_stop_check_minutes} minutes)"
}

resource "aws_cloudwatch_event_target" "auto_stop_watchdog" {
  for_each = local.auto_stop_resources

  rule = aws_cloudwatch_event_rule.auto_stop_watchdog[each.key].name
  arn  = aws_lambda_function.auto_stop_watchdog[each.key].arn
}

resource "aws_lambda_permission" "auto_stop_watchdog" {
  for_each = local.auto_stop_resources

  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_stop_watchdog[each.key].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.auto_stop_watchdog[each.key].arn
}
