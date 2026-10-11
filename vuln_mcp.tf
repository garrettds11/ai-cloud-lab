# Vulnerability findings MCP server (lambda/vuln_mcp), so the lab's Open WebUI and Ollama
# can answer questions about the findings table. It is a Lambda behind a Function URL.
#
# Off by default. Set vuln_mcp_table_name (and vuln_mcp_token_secret_arn) to turn it on.
# The table and the token secret are built by hand ahead of Terraform; this file only
# reads the table and never creates or changes it.
#
# Access control: the Function URL has no AWS signature check, because Open WebUI cannot
# sign requests. The function itself requires a bearer token from the secret and refuses
# every request if the token is missing. The role can only Query and Scan that one table
# and its indexes, and read that one secret.

locals {
  vuln_mcp_resources = var.vuln_mcp_table_name != null ? { vuln_mcp = true } : {}

  # Log search tools (lambda/vuln_mcp/log_tools.py) are added only when all three Loki settings are given.
  log_mcp_enabled = length(local.vuln_mcp_resources) > 0 && var.log_mcp_loki_url != null && var.log_mcp_loki_user != null && var.log_mcp_loki_token_secret_arn != null

  log_mcp_statements = local.log_mcp_enabled ? [
    {
      Sid      = "ReadLogStoreToken"
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = var.log_mcp_loki_token_secret_arn
    },
  ] : []

  log_mcp_environment = local.log_mcp_enabled ? {
    LOKI_URL              = var.log_mcp_loki_url
    LOKI_USER             = var.log_mcp_loki_user
    LOKI_TOKEN_SECRET_ARN = var.log_mcp_loki_token_secret_arn
    LOKI_STREAM_SELECTOR  = var.log_mcp_stream_selector
  } : {}

  # The MCP endpoint (the Function URL already ends in a slash). The lab instance connects
  # Open WebUI to it at boot; see scripts/ai-lab-register-vuln-mcp.sh.
  vuln_mcp_url = length(local.vuln_mcp_resources) > 0 ? "${aws_lambda_function_url.vuln_mcp["vuln_mcp"].function_url}mcp" : null
}

# Registration needs the Open WebUI admin API, which needs local password sign-in.
check "vuln_mcp_registration_needs_local_login" {
  assert {
    condition     = var.vuln_mcp_table_name == null || var.open_webui_enable_local_login
    error_message = "The vulnerability MCP is on but open_webui_enable_local_login is false, so the instance cannot sign in to Open WebUI to register it. Add the connection by hand in Admin Settings > External Tools (see lambda/vuln_mcp/README.md), or turn local login on."
  }
}

# The three Loki settings only work together, and only with the MCP server.
check "log_mcp_settings_are_complete" {
  assert {
    condition = (
      (var.log_mcp_loki_url == null && var.log_mcp_loki_user == null && var.log_mcp_loki_token_secret_arn == null) ||
      local.log_mcp_enabled
    )
    error_message = "The log search tools need log_mcp_loki_url, log_mcp_loki_user and log_mcp_loki_token_secret_arn together, and vuln_mcp_table_name. Set all of them, or none."
  }
}

data "aws_caller_identity" "current" {}

data "archive_file" "vuln_mcp" {
  for_each = local.vuln_mcp_resources

  type        = "zip"
  source_dir  = "${path.module}/lambda/vuln_mcp"
  output_path = "${path.module}/.terraform/vuln_mcp.zip"

  # The local development server, the notes and Python's cache stay out of the package.
  excludes = ["__pycache__", "__pycache__/*", "local_server.py", "README.md"]
}

resource "aws_iam_role" "vuln_mcp" {
  for_each = local.vuln_mcp_resources

  name_prefix = "${var.project_name}-vuln-mcp-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project_name}-vuln-mcp-role"
  }
}

resource "aws_iam_role_policy_attachment" "vuln_mcp_logs" {
  for_each = local.vuln_mcp_resources

  role       = aws_iam_role.vuln_mcp[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "vuln_mcp" {
  for_each = local.vuln_mcp_resources

  name = "${var.project_name}-vuln-mcp"
  role = aws_iam_role.vuln_mcp[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Sid    = "ReadFindingsTable"
        Effect = "Allow"
        Action = ["dynamodb:Query", "dynamodb:Scan"]
        Resource = [
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.vuln_mcp_table_name}",
          "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.vuln_mcp_table_name}/index/*"
        ]
      },
      {
        Sid      = "ReadTokenSecret"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = var.vuln_mcp_token_secret_arn == null ? "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:not-configured" : var.vuln_mcp_token_secret_arn
      }
    ], local.log_mcp_statements)
  })
}

resource "aws_cloudwatch_log_group" "vuln_mcp" {
  #checkov:skip=CKV_AWS_338:Short retention is deliberate for a lab: the logs are operational and cost and privacy favour a short window
  #checkov:skip=CKV_AWS_158:Logs are encrypted at rest by CloudWatch; a customer managed key adds cost for no lab benefit
  for_each = local.vuln_mcp_resources

  name              = "/aws/lambda/${var.project_name}-vuln-mcp"
  retention_in_days = 14
}

# Counts for the Tool calls dashboard and the tool alerts, taken from the function's JSON log
# lines (one per tool call, and one per refused request). Metrics carry the tool's name only,
# never arguments or results. Namespace AILab/Tools is separate from the idle monitor's AILab.
locals {
  vuln_mcp_metric_filters = {
    calls        = { pattern = "{ $.event = \"tool_call\" }", name = "ToolCalls", value = "1", unit = "Count", by_tool = true }
    errors       = { pattern = "{ $.event = \"tool_call\" && $.ok = false }", name = "ToolErrors", value = "1", unit = "Count", by_tool = true }
    latency      = { pattern = "{ $.event = \"tool_call\" && $.ms = * }", name = "ToolLatencyMs", value = "$.ms", unit = "Milliseconds", by_tool = true }
    unauthorized = { pattern = "{ $.event = \"unauthorized\" }", name = "Unauthorized", value = "1", unit = "Count", by_tool = false }
  }
  vuln_mcp_metric_filter_resources = length(local.vuln_mcp_resources) > 0 ? local.vuln_mcp_metric_filters : {}
}

resource "aws_cloudwatch_log_metric_filter" "vuln_mcp" {
  for_each = local.vuln_mcp_metric_filter_resources

  name           = "${var.project_name}-vuln-mcp-${each.key}"
  log_group_name = aws_cloudwatch_log_group.vuln_mcp["vuln_mcp"].name
  pattern        = each.value.pattern

  metric_transformation {
    namespace  = "AILab/Tools"
    name       = each.value.name
    value      = each.value.value
    unit       = each.value.unit
    dimensions = each.value.by_tool ? { Tool = "$.tool" } : null
  }
}

resource "aws_lambda_function" "vuln_mcp" {
  #checkov:skip=CKV_AWS_50:X-Ray adds cost; requests are logged by the function
  #checkov:skip=CKV_AWS_272:Code is built from this repository by Terraform; code signing adds a signing profile for no lab benefit
  #checkov:skip=CKV_AWS_117:Reads one DynamoDB table and Secrets Manager only; a VPC would need NAT or endpoints for no gain
  #checkov:skip=CKV_AWS_116:Invoked synchronously through its function URL; a dead-letter queue applies only to async invokes
  #checkov:skip=CKV_AWS_115:Reserved concurrency fails on new accounts whose concurrency quota is 10
  #checkov:skip=CKV_AWS_173:Environment variables hold only names and ARNs; the bearer token is read from Secrets Manager at run time
  for_each = local.vuln_mcp_resources

  function_name    = "${var.project_name}-vuln-mcp"
  role             = aws_iam_role.vuln_mcp[each.key].arn
  runtime          = "python3.12"
  handler          = "mcp_handler.lambda_handler"
  filename         = data.archive_file.vuln_mcp[each.key].output_path
  source_code_hash = data.archive_file.vuln_mcp[each.key].output_base64sha256
  timeout          = 30
  memory_size      = 256

  environment {
    variables = merge({
      TABLE_NAME            = var.vuln_mcp_table_name
      AUTH_TOKEN_SECRET_ARN = coalesce(var.vuln_mcp_token_secret_arn, "")
    }, local.log_mcp_environment)
  }

  lifecycle {
    precondition {
      condition     = var.vuln_mcp_token_secret_arn != null
      error_message = "vuln_mcp_table_name is set but vuln_mcp_token_secret_arn is not. The server refuses every request without a token, so set vuln_mcp_token_secret_arn in terraform.tfvars."
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.vuln_mcp_logs,
    aws_iam_role_policy.vuln_mcp,
    aws_cloudwatch_log_group.vuln_mcp
  ]
}

# Public address. The bearer-token check happens inside the function.
resource "aws_lambda_function_url" "vuln_mcp" {
  #checkov:skip=CKV_AWS_258:Open WebUI cannot sign requests with SigV4, so the URL uses its own bearer token, checked in constant time by the function (lambda/vuln_mcp/README.md)
  for_each = local.vuln_mcp_resources

  function_name      = aws_lambda_function.vuln_mcp[each.key].function_name
  authorization_type = "NONE"
}

# A URL with authorization NONE needs both permissions to accept calls.
resource "aws_lambda_permission" "vuln_mcp_url" {
  #checkov:skip=CKV_AWS_301:Public by design so Open WebUI can call the function URL; every request needs the bearer token
  for_each = local.vuln_mcp_resources

  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.vuln_mcp[each.key].function_name
  principal              = "*"
  function_url_auth_type = "NONE"

  # URL creation also adds permissions; finish it before changing the policy.
  depends_on = [aws_lambda_function_url.vuln_mcp]
}

resource "aws_lambda_permission" "vuln_mcp_invoke" {
  #checkov:skip=CKV_AWS_301:Paired with the function URL permission; every request needs the bearer token
  for_each = local.vuln_mcp_resources

  statement_id             = "AllowInvokeViaFunctionUrl"
  action                   = "lambda:InvokeFunction"
  function_name            = aws_lambda_function.vuln_mcp[each.key].function_name
  principal                = "*"
  invoked_via_function_url = true

  # Lambda rejects simultaneous resource-policy updates on the same function.
  depends_on = [aws_lambda_permission.vuln_mcp_url]
}
