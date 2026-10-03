# Idle-aware auto-stop.
#
# Layer 1 runs on the instance (scripts/ai-lab-idle-check.sh, installed by
# cloud-init): it powers the instance off only after auto_stop_idle_minutes with
# no active Open WebUI users and no reply being generated.
#
# Layer 2 is this independent watchdog (lambda/auto_stop_watchdog.py), run by
# EventBridge every few minutes. It stops the instance only when the instance-side
# agent already reports it idle (shutdown failed) or when the agent is silent and
# the load balancer shows no traffic. It emails an alert when the instance runs
# longer than auto_stop_max_uptime_hours or when the agent stops reporting. It never
# stops an instance that has active users.

locals {
  auto_stop_check_minutes  = 5
  auto_stop_parameter_name = "/${var.project_name}/auto-stop"
}

check "auto_stop_alert_email" {
  assert {
    condition     = !var.enable_auto_stop || var.auto_stop_alert_email != null
    error_message = "enable_auto_stop is on but auto_stop_alert_email is not set, so long-running and not-reporting alerts have nowhere to go. Set auto_stop_alert_email in terraform.tfvars."
  }
}

# Settings read by the instance-side agent every minute. Keeping them here, not in
# user-data, means changing the toggle or the timeout updates in place and never
# replaces the instance.
resource "aws_ssm_parameter" "auto_stop" {
  name  = local.auto_stop_parameter_name
  type  = "String"
  value = jsonencode({ enabled = var.enable_auto_stop, idle_minutes = var.auto_stop_idle_minutes })
}

# Lets the agent read its settings and publish its idle state for the watchdog.
resource "aws_iam_role_policy" "auto_stop_agent" {
  name = "${var.project_name}-auto-stop-agent"
  role = aws_iam_role.ssm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = aws_ssm_parameter.auto_stop.arn
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
}

# Email subscriptions stay "pending" until the recipient clicks the confirmation link.
resource "aws_sns_topic_subscription" "auto_stop_email" {
  for_each = var.enable_auto_stop && var.auto_stop_alert_email != null ? local.auto_stop_resources : {}

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
        Action   = "sns:Publish"
        Resource = aws_sns_topic.auto_stop[each.key].arn
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "auto_stop_watchdog" {
  for_each = local.auto_stop_resources

  name              = "/aws/lambda/${var.project_name}-auto-stop-watchdog"
  retention_in_days = 14
}

resource "aws_lambda_function" "auto_stop_watchdog" {
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
      INSTANCE_ID      = aws_instance.ai_lab.id
      IDLE_MINUTES     = tostring(var.auto_stop_idle_minutes)
      MAX_UPTIME_HOURS = tostring(var.auto_stop_max_uptime_hours)
      CHECK_MINUTES    = tostring(local.auto_stop_check_minutes)
      SNS_TOPIC_ARN    = aws_sns_topic.auto_stop[each.key].arn
      ALB_DIMENSION    = var.enable_domain_access ? aws_lb.domain["domain"].arn_suffix : ""
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
