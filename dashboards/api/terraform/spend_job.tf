# The spend caps job (../spend_job.py): every 5 minutes it reads the lab's usage, keeps the
# month's ledger, emails people at 50/80/98/100% of their cap, blocks them at 100% and stops
# the instance when the lab's cap is reached. See docs/monitoring-spec.md ("Spend caps").
#
# It has its own role, separate from the customer and admin functions, because it is the only
# thing that may stop the instance, disable a Cognito user and send email (through the SNS topic below). It is not behind the
# API: EventBridge starts it, and the admin function can start it after a cap change.

locals {
  spend_function_name = "${var.name_prefix}-spend"

  spend_email_statements = var.spend_emails_enabled ? [
    {
      Sid      = "EmailPeopleAboutTheirCaps"
      Effect   = "Allow"
      Action   = ["sns:Publish", "sns:Subscribe", "sns:ListSubscriptionsByTopic"]
      Resource = aws_sns_topic.spend[0].arn
    },
  ] : []

  spend_policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid      = "RunOnlyThePanelDocument"
        Effect   = "Allow"
        Action   = "ssm:SendCommand"
        Resource = "arn:${format(local.arn_base, "ssm")}:document/${aws_ssm_document.webui_admin.name}"
      },
      {
        Sid       = "OnlyOnManagedInstances"
        Effect    = "Allow"
        Action    = "ssm:SendCommand"
        Resource  = "arn:${format(local.arn_base, "ec2")}:instance/*"
        Condition = { StringEquals = { "ssm:resourceTag/control-panel" = "managed" } }
      },
      {
        Sid      = "ReadActionResults"
        Effect   = "Allow"
        Action   = "ssm:GetCommandInvocation"
        Resource = "*"
      },
      {
        Sid      = "ReadLabWiring"
        Effect   = "Allow"
        Action   = "ssm:GetParametersByPath"
        Resource = "${local.ssm_arn}${local.lab_prefix_param}"
      },
      {
        Sid      = "ReadLabState"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "elasticloadbalancing:DescribeTargetHealth"]
        Resource = "*"
      },
      {
        Sid       = "StopOnlyManagedInstances"
        Effect    = "Allow"
        Action    = "ec2:StopInstances"
        Resource  = "arn:${format(local.arn_base, "ec2")}:instance/*"
        Condition = { StringEquals = { "aws:ResourceTag/control-panel" = "managed" } }
      },
      {
        Sid      = "PeopleAndTheirCaps"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Scan", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.users.arn
      },
      {
        # After an instance start, the job puts the saved Open WebUI settings back and notes that it did.
        Sid      = "OpenWebuiDesiredState"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Scan"]
        Resource = aws_dynamodb_table.webui_desired_state.arn
      },
      {
        Sid      = "LedgerAndHistory"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
        Resource = aws_dynamodb_table.events.arn
      },
      {
        # The lab's pool ID is read at run time from the lab's parameters, so the pool is matched by
        # account and region only. The job can only disable and enable users, and read a user to see
        # whether the identity has a verified email address.
        Sid      = "BlockAndUnblockSignIn"
        Effect   = "Allow"
        Action   = ["cognito-idp:AdminDisableUser", "cognito-idp:AdminEnableUser", "cognito-idp:AdminGetUser"]
        Resource = "arn:${format(local.arn_base, "cognito-idp")}:userpool/*"
      },
    ], local.spend_email_statements)
  })
}

resource "aws_iam_role" "spend" {
  name               = "${var.name_prefix}-spend"
  description        = "Role of the spend caps job"
  assume_role_policy = local.lambda_assume_policy
}

resource "aws_iam_role_policy" "spend" {
  name   = "${var.name_prefix}-spend"
  role   = aws_iam_role.spend.id
  policy = local.spend_policy
}

resource "aws_cloudwatch_log_group" "spend" {
  #checkov:skip=CKV_AWS_338:Operational logs for a lab; a shorter retention is deliberate (cost and privacy)
  #checkov:skip=CKV_AWS_158:Encrypted at rest by CloudWatch; a customer managed key adds cost for no lab benefit

  name              = "/aws/lambda/${local.spend_function_name}"
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role_policy" "spend_logs" {
  name = "${var.name_prefix}-spend-logs"
  role = aws_iam_role.spend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.spend.arn}:*"
    }]
  })
}

resource "aws_lambda_function" "spend" {
  #checkov:skip=CKV_AWS_50:X-Ray adds cost; each run logs a JSON summary and its errors
  #checkov:skip=CKV_AWS_272:Code is built from this repository by Terraform; code signing adds a signing profile for no lab benefit
  #checkov:skip=CKV_AWS_117:Calls only AWS APIs; a VPC would need NAT or endpoints for no gain
  #checkov:skip=CKV_AWS_116:A failed run is retried by the next scheduled run five minutes later; a dead-letter queue adds nothing
  #checkov:skip=CKV_AWS_115:Reserved concurrency fails on new accounts whose concurrency quota is 10; the schedule starts one run at a time
  #checkov:skip=CKV_AWS_173:Environment variables hold names and email addresses, no secrets; Lambda encrypts them with an AWS managed key

  function_name    = local.spend_function_name
  description      = "Control panel spend caps: monthly ledger, cap emails, blocks at 100%, lab budget stop"
  role             = aws_iam_role.spend.arn
  handler          = "spend_job.lambda_handler"
  runtime          = "python3.12"
  timeout          = 170 # waits up to 100 s for the instance, then a few role changes
  memory_size      = 256
  filename         = data.archive_file.control_api.output_path
  source_code_hash = data.archive_file.control_api.output_base64sha256

  environment {
    variables = merge(local.common_environment, {
      WEBUI_DOCUMENT      = aws_ssm_document.webui_admin.name
      SPEND_TOPIC_ARN     = var.spend_emails_enabled ? aws_sns_topic.spend[0].arn : ""
      WEBUI_DESIRED_TABLE = aws_dynamodb_table.webui_desired_state.name
      TOOL_TOKEN_PREFIX   = local.tool_token_prefix
    })
  }

  depends_on = [
    aws_iam_role_policy.spend_logs,
    aws_cloudwatch_log_group.spend,
  ]
}

resource "aws_cloudwatch_event_rule" "spend" {
  name                = local.spend_function_name
  description         = "Runs the spend caps job every 5 minutes"
  schedule_expression = "rate(5 minutes)"
}

resource "aws_cloudwatch_event_target" "spend" {
  rule = aws_cloudwatch_event_rule.spend.name
  arn  = aws_lambda_function.spend.arn
}

resource "aws_lambda_permission" "spend_schedule" {
  statement_id  = "AllowSpendSchedule"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.spend.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.spend.arn
}

# Cap emails go through this topic. The job gives each person with a verified email address their own
# email subscription, filtered on a "recipient" message attribute so that person receives only their own
# messages. SNS emails each person a confirmation link once; no message is sent before they click it.
# The subscriptions are made by the job at run time, not by Terraform.
resource "aws_sns_topic" "spend" {
  count = var.spend_emails_enabled ? 1 : 0

  name = "${var.name_prefix}-spend-alerts"
  # Encrypt stored messages with the AWS managed SNS key. The job publishes with its own IAM role
  # through the SNS API, which that key's policy allows; no key permissions are needed.
  kms_master_key_id = "alias/aws/sns"
}
