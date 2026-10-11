# A read-only IAM role that Grafana Cloud assumes, so its CloudWatch data source can read the
# lab's Lambda logs (the vulnerability MCP's tool calls, the control panel API) and AWS metrics
# (Lambda, load balancer, EC2) without shipping them a second time.
#
# Off unless both grafana_cloudwatch_account_id and grafana_cloudwatch_external_id are set. Both
# come from Grafana, so this is a two-step setup: add the CloudWatch data source in Grafana (it
# shows the account ID and the external ID), set the two variables and apply, then paste this
# role's ARN (output grafana_cloudwatch_role_arn) into the data source. See
# docs/runbook/smoke-tests/lab-features.md, "Grafana dashboards and alerts test".
#
# The role can read metrics and run Logs Insights queries on this lab's Lambda log groups. It
# cannot write anything, read secrets, or read other log groups. The external ID in the trust
# policy stops other Grafana Cloud customers from assuming it.

locals {
  grafana_cloudwatch_resources = (
    var.grafana_cloudwatch_account_id != null && var.grafana_cloudwatch_external_id != null
  ) ? { grafana = true } : {}
}

check "grafana_cloudwatch_needs_both_values" {
  assert {
    condition     = (var.grafana_cloudwatch_account_id == null) == (var.grafana_cloudwatch_external_id == null)
    error_message = "Set both grafana_cloudwatch_account_id and grafana_cloudwatch_external_id, or neither. Grafana's CloudWatch data source page shows both."
  }
}

resource "aws_iam_role" "grafana_cloudwatch" {
  for_each = local.grafana_cloudwatch_resources

  name_prefix = "${var.project_name}-grafana-cw-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "arn:aws:iam::${var.grafana_cloudwatch_account_id}:root" }
      Action    = "sts:AssumeRole"
      Condition = { StringEquals = { "sts:ExternalId" = var.grafana_cloudwatch_external_id } }
    }]
  })

  tags = {
    Name = "${var.project_name}-grafana-cloudwatch-role"
  }
}

resource "aws_iam_role_policy" "grafana_cloudwatch" {
  for_each = local.grafana_cloudwatch_resources

  name = "${var.project_name}-grafana-cloudwatch-read"
  role = aws_iam_role.grafana_cloudwatch[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Metric reads have no resource-level control in IAM.
        Sid      = "ReadMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics", "cloudwatch:DescribeAlarms"]
        Resource = "*"
      },
      {
        # The metric picker lists instances and their tags; read only.
        Sid      = "ListInstancesAndTags"
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ec2:DescribeTags", "ec2:DescribeRegions", "tag:GetResources"]
        Resource = "*"
      },
      {
        Sid      = "ListLogGroups"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StopQuery"]
        Resource = "*"
      },
      {
        # Queries run only on this lab's Lambda log groups.
        Sid    = "QueryLabLambdaLogs"
        Effect = "Allow"
        Action = ["logs:StartQuery", "logs:GetLogGroupFields", "logs:GetLogEvents", "logs:FilterLogEvents"]
        Resource = [
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*",
          "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${var.project_name}-*:*"
        ]
      }
    ]
  })
}
