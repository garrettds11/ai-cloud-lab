resource "aws_s3_bucket" "reports" {
  #checkov:skip=CKV2_AWS_62:Controller checks the manifest and publishes SNS; bucket notifications have no additional consumer
  #checkov:skip=CKV_AWS_18:Single private lab report bucket; S3 server access logging would require a second destination bucket
  #checkov:skip=CKV_AWS_144:Single-region lab assessment data; versioning is enabled but cross-region replication is not required
  #checkov:skip=CKV_AWS_145:SSE-S3 encrypts reports at rest; per-request KMS encryption is not required for this lab
  bucket_prefix = "${var.name}-reports-"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }
  rule {
    id     = "assessment-retention"
    status = "Enabled"
    filter { prefix = "runs/" }
    expiration { days = 365 }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
  rule {
    id     = "old-script-versions"
    status = "Enabled"
    filter { prefix = "assets/" }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
  depends_on = [aws_s3_bucket_versioning.reports]
}

resource "aws_s3_bucket_policy" "reports" {
  bucket = aws_s3_bucket.reports.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyPlaintext"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.reports.arn, "${aws_s3_bucket.reports.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
  depends_on = [aws_s3_bucket_public_access_block.reports]
}

resource "aws_s3_object" "scanner_script" {
  for_each = {
    "run-scan.py"       = "run_scan.py"
    "gmp-scan.py"       = "gmp_scan.py"
    "assess-aws.py"     = "assess_aws.py"
    "managed-images.py" = "managed_images.py"
  }
  bucket       = aws_s3_bucket.reports.id
  key          = "assets/${each.key}"
  content      = file("${path.module}/${each.value}")
  etag         = filemd5("${path.module}/${each.value}")
  content_type = "text/plain"
}

data "archive_file" "image_builds" {
  type        = "zip"
  source_dir  = "${path.module}/image_builds"
  output_path = "${path.module}/.terraform/image-builds.zip"
}

resource "aws_s3_object" "image_builds" {
  bucket       = aws_s3_bucket.reports.id
  key          = "assets/image-builds.zip"
  source       = data.archive_file.image_builds.output_path
  etag         = data.archive_file.image_builds.output_md5
  content_type = "application/zip"
}

locals {
  # Use the same route inventory as the API stack, so additions cannot silently
  # disappear from the deployed-configuration assessment.
  api_routes = { for route in regexall("\"([A-Z]+ /[^\"]*)\"\\s*=\\s*\"(customer|admin)\"", file("${path.module}/../dashboards/api/terraform/api.tf")) : route[0] => var.control_panel_functions[route[1]].arn }
  assessment_config = jsonencode({
    functions          = var.control_panel_functions
    layer_version_arns = sort(tolist(var.control_panel_layer_version_arns))
    policy_arns        = sort(tolist(var.control_panel_policy_arns))
    api_id             = var.control_panel_api_id
    expected_routes    = local.api_routes
    jwt_issuer         = var.control_panel_jwt_issuer
    jwt_audiences      = sort(tolist(var.control_panel_jwt_audiences))
    allowed_runtimes   = sort(tolist(var.control_panel_allowed_runtimes))
    allowed_origins    = ["https://${var.control_panel_hostname}"]
    expected_stage     = "$default"
  })
}

resource "aws_s3_object" "assessment_config" {
  bucket       = aws_s3_bucket.reports.id
  key          = "assets/assessment.json"
  content      = local.assessment_config
  etag         = md5(local.assessment_config)
  content_type = "application/json"
}

resource "aws_dynamodb_table" "state" {
  #checkov:skip=CKV_AWS_119:AWS-owned encryption protects run IDs and timestamps; state contains no scan credentials or reports
  name         = "${var.name}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  attribute {
    name = "pk"
    type = "S"
  }
  point_in_time_recovery { enabled = true }
}

resource "aws_sqs_queue" "requests" {
  name                       = "${var.name}-requests"
  message_retention_seconds  = 1209600
  visibility_timeout_seconds = 360
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letters.arn
    maxReceiveCount     = 3
  })
}

resource "aws_sqs_queue" "dead_letters" {
  name                      = "${var.name}-dead-letters"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sns_topic" "results" {
  name              = "${var.name}-results"
  kms_master_key_id = "alias/aws/sns"
}
