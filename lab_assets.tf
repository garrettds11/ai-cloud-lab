# Helper scripts the instance downloads at boot instead of carrying them inside its user data:
# EC2 refuses user data over 16 KB after compression, and the embedded scripts had used up the
# room. The bucket is private, encrypted, readable only by the lab instance's role, and destroyed
# with the lab. Bootstrap refuses a file whose SHA-256 differs from the value Terraform recorded,
# and a changed script still changes the user data (through that hash), so the instance is
# replaced as before.

locals {
  lab_assets = {
    "ai-lab-idle-check"        = "scripts/ai-lab-idle-check.sh"
    "ai-lab-register-vuln-mcp" = "scripts/ai-lab-register-vuln-mcp.sh"
    "alloy-config.alloy"       = "scripts/alloy-config.alloy"
    "ai-lab-session-log"       = "scripts/ai-lab-session-log"
    "ai-lab-usage"             = "scripts/ai-lab-usage"
    "ai-lab-set-role"          = "scripts/ai-lab-set-role"
    "ai-lab-chat-test"         = "scripts/ai-lab-chat-test"
    "ai-lab-metrics"           = "scripts/ai-lab-metrics"
    "ai-lab-webui-lib.sh"      = "scripts/ai-lab-webui-lib.sh"
  }
  lab_asset_content = { for name, source in local.lab_assets : name => replace(file("${path.module}/${source}"), "\r\n", "\n") }
  lab_asset_sha256  = { for name, content in local.lab_asset_content : name => sha256(content) }
}

resource "aws_s3_bucket" "lab_assets" {
  #checkov:skip=CKV_AWS_18:Holds only the lab's own boot scripts, written by Terraform; access logging adds a second bucket for no benefit
  #checkov:skip=CKV_AWS_21:Terraform rewrites every object on each apply; the repository is the history
  #checkov:skip=CKV_AWS_144:Boot scripts for a single-region lab; cross-region replication adds cost for no benefit
  #checkov:skip=CKV_AWS_145:Encrypted with S3-managed keys (SSE-S3); a customer managed key adds cost for no lab benefit
  #checkov:skip=CKV2_AWS_61:Objects are replaced on every apply and deleted with the lab; no lifecycle rules needed
  #checkov:skip=CKV2_AWS_62:Nothing consumes events from this bucket
  bucket_prefix = "${var.project_name}-lab-assets-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "lab_assets" {
  bucket                  = aws_s3_bucket.lab_assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lab_assets" {
  bucket = aws_s3_bucket.lab_assets.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lab_assets" {
  bucket = aws_s3_bucket.lab_assets.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_policy" "lab_assets" {
  bucket = aws_s3_bucket.lab_assets.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "TlsOnly"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.lab_assets.arn, "${aws_s3_bucket.lab_assets.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })

  depends_on = [aws_s3_bucket_public_access_block.lab_assets]
}

resource "aws_s3_object" "lab_asset" {
  for_each = local.lab_asset_content

  bucket       = aws_s3_bucket.lab_assets.id
  key          = "scripts/${each.key}"
  content      = each.value
  content_type = "text/plain"
  etag         = md5(each.value)

  depends_on = [aws_s3_bucket_server_side_encryption_configuration.lab_assets]
}

# The instance may read the scripts and nothing else in the bucket.
resource "aws_iam_role_policy" "lab_assets" {
  name = "${var.project_name}-lab-assets"
  role = aws_iam_role.ssm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "s3:GetObject"
      Resource = "${aws_s3_bucket.lab_assets.arn}/scripts/*"
    }]
  })
}
