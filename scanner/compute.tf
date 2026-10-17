resource "aws_iam_role" "scanner" {
  name_prefix = "${var.name}-ec2-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.scanner.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "scanner" {
  name = "${var.name}-scan"
  role = aws_iam_role.scanner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = [var.ssh_private_key_secret_arn, var.greenbone_admin_password_secret_arn]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${aws_s3_bucket.reports.arn}/assets/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = ["${aws_s3_bucket.reports.arn}/runs/*"]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "scanner" {
  name_prefix = "${var.name}-"
  role        = aws_iam_role.scanner.name
}

resource "aws_iam_role_policy" "assessment" {
  name = "${var.name}-assessment"
  role = aws_iam_role.scanner.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      {
        Effect   = "Allow"
        Action   = ["lambda:GetFunction", "lambda:GetPolicy", "lambda:GetRuntimeManagementConfig", "lambda:ListFunctionUrlConfigs"]
        Resource = [for function in values(var.control_panel_functions) : function.arn]
      },
      {
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:ListRolePolicies", "iam:GetRolePolicy", "iam:ListAttachedRolePolicies"]
        Resource = distinct([for function in values(var.control_panel_functions) : function.role_arn])
      },
      {
        Effect   = "Allow"
        Action   = ["apigateway:GET"]
        Resource = ["arn:aws:apigateway:${var.aws_region}::/apis/${var.control_panel_api_id}", "arn:aws:apigateway:${var.aws_region}::/apis/${var.control_panel_api_id}/*"]
      }
      ], length(var.control_panel_layer_version_arns) > 0 ? [{
        Effect   = "Allow"
        Action   = ["lambda:GetLayerVersion"]
        Resource = sort(tolist(var.control_panel_layer_version_arns))
        }] : [], length(var.control_panel_policy_arns) > 0 ? [{
        Effect   = "Allow"
        Action   = ["iam:GetPolicy", "iam:GetPolicyVersion"]
        Resource = sort(tolist(var.control_panel_policy_arns))
    }] : [])
  })
}

resource "aws_instance" "scanner" {
  #checkov:skip=CKV_AWS_126:Normally stopped single scanner; detailed metrics are not used for scaling or scan orchestration
  #checkov:skip=CKV_AWS_88:Outbound feed downloads and public scans use a public IPv4 to avoid an always-on NAT gateway; ingress is denied
  ami                                  = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type                        = var.scanner_instance_type
  ebs_optimized                        = true
  subnet_id                            = aws_subnet.scanner.id
  vpc_security_group_ids               = [aws_security_group.scanner.id]
  associate_public_ip_address          = true
  iam_instance_profile                 = aws_iam_instance_profile.scanner.name
  instance_initiated_shutdown_behavior = "stop"

  user_data = templatefile("${path.module}/bootstrap.sh.tpl", {
    region                   = var.aws_region
    bucket                   = aws_s3_bucket.reports.bucket
    script_sha256            = filesha256("${path.module}/run_scan.py")
    gmp_script_sha256        = filesha256("${path.module}/gmp_scan.py")
    assessment_sha256        = filesha256("${path.module}/assess_aws.py")
    assessment_config_sha256 = sha256(local.assessment_config)
    managed_images_sha256    = filesha256("${path.module}/managed_images.py")
    image_builds_sha256      = data.archive_file.image_builds.output_sha256
    hostname                 = var.public_hostname
    control_panel_hostname   = var.control_panel_hostname
    lab_ip                   = var.lab_private_ip
    image_digest             = var.open_webui_image_digest
    ssh_secret_arn           = var.ssh_private_key_secret_arn
    greenbone_secret_arn     = var.greenbone_admin_password_secret_arn
    timeout_hours            = var.scan_timeout_hours
  })
  user_data_replace_on_change = true

  root_block_device {
    volume_size = var.scanner_volume_gib
    volume_type = "gp3"
    encrypted   = true
  }
  metadata_options {
    http_tokens                 = "required"
    http_endpoint               = "enabled"
    http_put_response_hop_limit = 1
  }
  tags       = { Name = var.name, Purpose = "vulnerability-scanner" }
  depends_on = [aws_s3_object.scanner_script, aws_s3_object.assessment_config, aws_s3_object.image_builds, aws_iam_role_policy.scanner, aws_iam_role_policy.assessment, aws_iam_role_policy_attachment.ssm]

  lifecycle {
    precondition {
      condition = alltrue([for function in values(var.control_panel_functions) :
        split(":", function.arn)[3] == var.aws_region &&
        split(":", function.arn)[4] == data.aws_caller_identity.current.account_id &&
        split(":", function.role_arn)[4] == data.aws_caller_identity.current.account_id
      ])
      error_message = "The control-panel functions and execution roles must be in this stack's account and region."
    }
    precondition {
      condition     = var.lab_vpc_id != aws_vpc.scanner.id
      error_message = "The scanner must be in its dedicated VPC."
    }
  }
}

data "archive_file" "orchestrator" {
  type        = "zip"
  source_file = "${path.module}/orchestrator.py"
  output_path = "${path.module}/orchestrator.zip"
}

resource "aws_iam_role" "orchestrator" {
  name_prefix = "${var.name}-lambda-"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}

resource "aws_iam_role_policy_attachment" "orchestrator_logs" {
  role       = aws_iam_role.orchestrator.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "orchestrator" {
  name = "${var.name}-control"
  role = aws_iam_role.orchestrator.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["ec2:DescribeInstances", "ssm:DescribeInstanceInformation", "ssm:GetCommandInvocation"]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ec2:StartInstances", "ec2:StopInstances"]
        Resource = [aws_instance.scanner.arn, "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${var.lab_instance_id}"]
      },
      {
        Effect = "Allow"
        Action = ["ssm:SendCommand"]
        Resource = [
          "arn:aws:ssm:${var.aws_region}::document/AWS-RunShellScript",
          aws_instance.scanner.arn,
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/${var.lab_instance_id}"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${aws_s3_bucket.reports.arn}/runs/*"
      },
      {
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.results.arn
      },
      {
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = "arn:aws:dynamodb:${var.aws_region}:${data.aws_caller_identity.current.account_id}:table/${var.lab_events_table_name}"
      }
    ]
  })
}

resource "aws_cloudwatch_log_group" "orchestrator" {
  #checkov:skip=CKV_AWS_158:CloudWatch encrypts operational metadata at rest; a customer key adds a recurring lab cost
  #checkov:skip=CKV_AWS_338:Thirty-day operational retention; assessment evidence is retained separately in S3
  name              = "/aws/lambda/${var.name}-orchestrator"
  retention_in_days = 30
}

resource "aws_lambda_function" "orchestrator" {
  #checkov:skip=CKV_AWS_272:Terraform packages repository source and tracks its digest; no separate signing-profile workflow for this lab
  #checkov:skip=CKV_AWS_173:Environment contains resource identifiers and an SSH public key only; Lambda encrypts it with its AWS managed key
  #checkov:skip=CKV_AWS_117:Controller calls AWS control-plane APIs only; VPC placement would require NAT or endpoints
  #checkov:skip=CKV_AWS_50:Short controller ticks use state and command IDs for diagnostics; X-Ray is not used
  function_name                  = "${var.name}-orchestrator"
  filename                       = data.archive_file.orchestrator.output_path
  source_code_hash               = data.archive_file.orchestrator.output_base64sha256
  role                           = aws_iam_role.orchestrator.arn
  handler                        = "orchestrator.lambda_handler"
  runtime                        = "python3.12"
  timeout                        = 60
  reserved_concurrent_executions = 1
  dead_letter_config {
    target_arn = aws_sqs_queue.dead_letters.arn
  }
  environment {
    variables = {
      STATE_TABLE           = aws_dynamodb_table.state.name
      EVENTS_TABLE          = var.lab_events_table_name
      LAB_INSTANCE_ID       = var.lab_instance_id
      SCANNER_INSTANCE_ID   = aws_instance.scanner.id
      REPORT_BUCKET         = aws_s3_bucket.reports.bucket
      RESULTS_TOPIC_ARN     = aws_sns_topic.results.arn
      SCAN_INTERVAL_SECONDS = tostring(var.scan_interval_hours * 3600)
      SCAN_TIMEOUT_SECONDS  = tostring(var.scan_timeout_hours * 3600)
      SSH_PUBLIC_KEY        = var.ssh_public_key
      IMAGE_DIGEST          = var.open_webui_image_digest
      CONTAINER_NAME        = var.open_webui_container_name
    }
  }
  depends_on = [aws_iam_role_policy.orchestrator, aws_iam_role_policy.request_queue, aws_iam_role_policy_attachment.orchestrator_logs, aws_cloudwatch_log_group.orchestrator]
}

resource "aws_cloudwatch_event_rule" "tick" {
  name                = "${var.name}-tick"
  schedule_expression = "rate(5 minutes)"
}
resource "aws_cloudwatch_event_target" "tick" {
  rule      = aws_cloudwatch_event_rule.tick.name
  target_id = "Orchestrator"
  arn       = aws_lambda_function.orchestrator.arn
}
resource "aws_lambda_permission" "tick" {
  statement_id  = "AllowEventBridgeTick"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.orchestrator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.tick.arn
}
resource "aws_lambda_event_source_mapping" "requests" {
  event_source_arn = aws_sqs_queue.requests.arn
  function_name    = aws_lambda_function.orchestrator.arn
  batch_size       = 1
}
resource "aws_iam_role_policy" "request_queue" {
  name = "${var.name}-queue"
  role = aws_iam_role.orchestrator.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.requests.arn
      },
      {
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.dead_letters.arn
      }
    ]
  })
}
