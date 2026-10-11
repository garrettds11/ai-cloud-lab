# The two functions share one zip (handler.py, its helper modules and the entry points) and differ only in
# their entry point and their role. Each role allows exactly what its function's routes need;
# the split holds even if the code is wrong. These policies are the ones dashboards/api/README.md
# documented for the hand-built roles, plus target-health reads for the admin function.

locals {
  account   = data.aws_caller_identity.current.account_id
  partition = data.aws_partition.current.partition
  arn_base  = "${local.partition}:%s:${var.aws_region}:${local.account}"

  lab_prefix_param  = "/${var.lab_project_name}/control-panel"
  auto_stop_param   = "/${var.lab_project_name}/auto-stop"
  reset_param       = "${local.auto_stop_param}/reset-at"
  policy_param      = "${local.auto_stop_param}/policy"
  reset_count_param = "${local.auto_stop_param}/reset-count"

  ssm_arn = "arn:${format(local.arn_base, "ssm")}:parameter"

  functions = {
    customer = { handler = "customer.lambda_handler", description = "Control panel API: sign-in, own instances, start, timer reset, own logins and logs" }
    admin    = { handler = "admin.lambda_handler", description = "Control panel API: users, roles, grants, change history, everyone's activity and Open WebUI admin actions. Cannot start instances" }
  }

  # Tool tokens for Open WebUI tool servers are chosen only from secrets whose name starts with this.
  # The lab's instance role (the root stack) may read the same prefix and nothing else besides its own secrets.
  tool_token_prefix = "${var.lab_project_name}/tool-tokens/"

  common_environment = {
    BOOTSTRAP_ADMINS     = join(",", var.bootstrap_admins)
    LAB_PARAMETER_PREFIX = local.lab_prefix_param
    USERS_TABLE          = aws_dynamodb_table.users.name
    ENTITLEMENTS_TABLE   = aws_dynamodb_table.entitlements.name
    EVENTS_TABLE         = aws_dynamodb_table.events.name
    EVENT_TTL_DAYS       = tostring(var.event_ttl_days)
  }

  environment = {
    customer = merge(local.common_environment, {
      AUTO_STOP_PARAMETER = local.auto_stop_param
      RESET_PARAMETER     = local.reset_param
    })
    admin = merge(local.common_environment, {
      WEBUI_DOCUMENT      = aws_ssm_document.webui_admin.name
      SPEND_FUNCTION      = "${var.name_prefix}-spend"
      AUTO_STOP_PARAMETER = local.auto_stop_param
      WEBUI_DESIRED_TABLE = aws_dynamodb_table.webui_desired_state.name
      TOOL_TOKEN_PREFIX   = local.tool_token_prefix
      PRIVATE_TOOL_HOSTS  = join(",", var.private_tool_hosts)
    })
  }
}

data "archive_file" "control_api" {
  type        = "zip"
  output_path = "${path.module}/.build/control-api.zip"

  source {
    content  = file("${path.module}/../handler.py")
    filename = "handler.py"
  }

  source {
    content  = file("${path.module}/../spend_rules.py")
    filename = "spend_rules.py"
  }

  source {
    content  = file("${path.module}/../spend_job.py")
    filename = "spend_job.py"
  }

  source {
    content  = file("${path.module}/../customer.py")
    filename = "customer.py"
  }

  source {
    content  = file("${path.module}/../admin.py")
    filename = "admin.py"
  }
}

locals {
  lambda_assume_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })

  customer_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "StartOnlyManagedInstances"
        Effect    = "Allow"
        Action    = "ec2:StartInstances"
        Resource  = "arn:${format(local.arn_base, "ec2")}:instance/*"
        Condition = { StringEquals = { "aws:ResourceTag/control-panel" = "managed" } }
      },
      {
        # These read APIs do not support resource-level permissions.
        Sid    = "ReadLabState"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceStatus",
          "elasticloadbalancing:DescribeTargetHealth",
          "cloudwatch:GetMetricStatistics",
          "cloudtrail:LookupEvents",
        ]
        Resource = "*"
      },
      {
        Sid      = "ReadAutoStopRuleAndReset"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = ["${local.ssm_arn}${local.auto_stop_param}", "${local.ssm_arn}${local.reset_param}", "${local.ssm_arn}${local.policy_param}", "${local.ssm_arn}${local.reset_count_param}"]
      },
      {
        # The only SSM writes: the reset time and the count of resets used. The auto-stop rule itself stays the lab's.
        Sid      = "WriteTimerResetOnly"
        Effect   = "Allow"
        Action   = "ssm:PutParameter"
        Resource = ["${local.ssm_arn}${local.reset_param}", "${local.ssm_arn}${local.reset_count_param}"]
      },
      {
        Sid      = "ReadLabWiring"
        Effect   = "Allow"
        Action   = "ssm:GetParametersByPath"
        Resource = "${local.ssm_arn}${local.lab_prefix_param}"
      },
      {
        Sid      = "ReadOwnRoles"
        Effect   = "Allow"
        Action   = "dynamodb:GetItem"
        Resource = aws_dynamodb_table.users.arn
      },
      {
        # A new row on first sign-in and the name and last-seen updates, never the roles attribute.
        Sid      = "RecordSignInWithoutRoles"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.users.arn
        Condition = {
          "ForAllValues:StringEquals" = {
            "dynamodb:Attributes" = ["email", "name", "sub", "source", "firstSeenAt", "lastSeenAt"]
          }
        }
      },
      {
        Sid      = "ReadGrants"
        Effect   = "Allow"
        Action   = "dynamodb:Query"
        Resource = aws_dynamodb_table.entitlements.arn
      },
      {
        # GetItem reads the spend caps state (to refuse a start when the lab's budget is reached
        # and to show the person's own cap); the function cannot change it.
        Sid      = "OwnEvents"
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.events.arn
      },
    ]
  })

  # No EC2 start and no SSM parameter write. Besides the panel's own tables, the admin function
  # can only run the panel's Open WebUI document, and only on instances tagged control-panel=managed.
  admin_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
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
        # GetCommandInvocation has no resource-level permissions. The function reads a result
        # only after checking that the command ran the panel's document.
        Sid      = "ReadActionResults"
        Effect   = "Allow"
        Action   = "ssm:GetCommandInvocation"
        Resource = "*"
      },
      {
        # DescribeTargetHealth is not in the hand-built admin role; without it every instance
        # read logged an AccessDenied warning (the code treats target health as optional).
        Sid      = "ReadLabState"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "elasticloadbalancing:DescribeTargetHealth", "cloudtrail:LookupEvents"]
        Resource = "*"
      },
      {
        Sid      = "ReadAutoStopRuleAndPolicy"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = ["${local.ssm_arn}${local.auto_stop_param}", "${local.ssm_arn}${local.policy_param}", "${local.ssm_arn}${local.reset_count_param}"]
      },
      {
        # The only SSM write the admin function makes: the timer policy. The lab's own setting stays the ceiling.
        Sid      = "WriteTimerPolicyOnly"
        Effect   = "Allow"
        Action   = "ssm:PutParameter"
        Resource = "${local.ssm_arn}${local.policy_param}"
      },
      {
        Sid      = "ReadLabWiring"
        Effect   = "Allow"
        Action   = "ssm:GetParametersByPath"
        Resource = "${local.ssm_arn}${local.lab_prefix_param}"
      },
      {
        Sid      = "UsersAndRoles"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:Scan", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.users.arn
      },
      {
        Sid      = "Grants"
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.entitlements.arn
      },
      {
        Sid      = "EventsAndHistory"
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:Scan", "dynamodb:GetItem"]
        Resource = aws_dynamodb_table.events.arn
      },
      {
        # The settings the panel keeps for Open WebUI, put back after an instance is replaced.
        Sid      = "OpenWebuiDesiredState"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Scan"]
        Resource = aws_dynamodb_table.webui_desired_state.arn
      },
      {
        # Lists secret names so a tool server's token can be chosen; the function filters to the tool token
        # prefix and returns names only. ListSecrets cannot be limited to a resource, and never returns a value.
        Sid      = "ListToolTokenNames"
        Effect   = "Allow"
        Action   = "secretsmanager:ListSecrets"
        Resource = "*"
      },
      {
        # After a cap change the admin function asks the spend caps job to run now.
        Sid      = "StartTheSpendJob"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = "arn:${format(local.arn_base, "lambda")}:function:${var.name_prefix}-spend"
      },
    ]
  })
}

resource "aws_iam_role" "function" {
  for_each = local.functions

  name               = "${var.name_prefix}-${each.key}"
  description        = "Role of the ${each.key} control panel function"
  assume_role_policy = local.lambda_assume_policy
}

resource "aws_iam_role_policy" "function" {
  for_each = local.functions

  name   = "${var.name_prefix}-${each.key}"
  role   = aws_iam_role.function[each.key].id
  policy = each.key == "customer" ? local.customer_policy : local.admin_policy
}

resource "aws_iam_role_policy" "logs" {
  for_each = local.functions

  name = "${var.name_prefix}-${each.key}-logs"
  role = aws_iam_role.function[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "${aws_cloudwatch_log_group.function[each.key].arn}:*"
    }]
  })
}

resource "aws_cloudwatch_log_group" "function" {
  for_each = local.functions
  #checkov:skip=CKV_AWS_338:Operational logs for a lab; a shorter retention is deliberate (cost and privacy)
  #checkov:skip=CKV_AWS_158:Encrypted at rest by CloudWatch; a customer managed key adds cost for no lab benefit

  name              = "/aws/lambda/${var.name_prefix}-${each.key}"
  retention_in_days = var.log_retention_days
}

resource "aws_lambda_function" "function" {
  for_each = local.functions
  #checkov:skip=CKV_AWS_50:X-Ray adds cost; each call logs its errors as JSON
  #checkov:skip=CKV_AWS_272:Code is built from this repository by Terraform; code signing adds a signing profile for no lab benefit
  #checkov:skip=CKV_AWS_117:Calls only AWS APIs; a VPC would need NAT or endpoints for no gain
  #checkov:skip=CKV_AWS_116:Invoked synchronously by API Gateway; a dead-letter queue applies only to async invokes
  #checkov:skip=CKV_AWS_115:Reserved concurrency fails on new accounts whose concurrency quota is 10; the API stage throttles instead
  #checkov:skip=CKV_AWS_173:Environment variables hold names and email addresses, no secrets; Lambda encrypts them with an AWS managed key

  function_name    = "${var.name_prefix}-${each.key}"
  description      = each.value.description
  role             = aws_iam_role.function[each.key].arn
  handler          = each.value.handler
  runtime          = "python3.12"
  timeout          = 15
  memory_size      = 256
  filename         = data.archive_file.control_api.output_path
  source_code_hash = data.archive_file.control_api.output_base64sha256

  environment {
    variables = local.environment[each.key]
  }

  depends_on = [
    aws_iam_role_policy.logs,
    aws_cloudwatch_log_group.function,
  ]
}
