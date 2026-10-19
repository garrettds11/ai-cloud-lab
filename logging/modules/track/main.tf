provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
  default_tags {
    tags = merge({ Environment = "lab", ManagedBy = "terraform", Stack = "logging-${var.track}" }, var.tags)
  }
}

data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}
data "aws_vpc" "default" { default = true }
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

# The lab publishes its IDs in /<lab_project_name>/lab/settings (addon_settings.tf at the
# repository root). Listing the path first makes a missing lab a warning, not a failed plan:
# the backend and archive still deploy, and the collector install, its permissions and the
# intake rule are added on the first apply after the lab exists.
locals {
  lab_settings_name = "/${var.lab_project_name}/lab/settings"
}
data "aws_ssm_parameters_by_path" "lab" {
  path            = "/${var.lab_project_name}/lab"
  with_decryption = false
}
data "aws_ssm_parameter" "lab" {
  count           = contains(data.aws_ssm_parameters_by_path.lab.names, local.lab_settings_name) ? 1 : 0
  name            = local.lab_settings_name
  with_decryption = false
}

locals {
  # IDs and names only, so the plain (insecure_value) form is used; the sensitive form could
  # not drive count and for_each.
  lab       = try(jsondecode(data.aws_ssm_parameter.lab[0].insecure_value), null)
  lab_set   = local.lab == null ? {} : { lab = local.lab }
  subnet_id = sort(data.aws_subnets.default.ids)[0]
  ui_port   = 9428 # VictoriaLogs serves its API and built-in vmui UI on this port
  vector_config = templatefile("${path.module}/vector.toml.tftpl", {
    track          = var.track
    backend_ip     = aws_instance.backend.private_ip
    bucket         = aws_s3_bucket.archive.bucket
    aws_region     = var.aws_region
    container_name = try(local.lab.open_webui_container_name, "open-webui")
  })
  # vector collects the logs; mcp is the read-only Lab Logs MCP server for Open WebUI.
  selected_packages = toset(["vector", "mcp"])
  mcp_port          = 8766
}

check "lab_settings_found" {
  assert {
    condition     = local.lab != null
    error_message = "No lab found at ${local.lab_settings_name}. The backend and archive deploy now; apply this track again after the lab is deployed (with project_name = \"${var.lab_project_name}\") to install the collector on it."
  }
}

check "track_name_matches_root" {
  assert {
    condition     = basename(path.cwd) == var.track
    error_message = "Run Terraform from logging/${var.track}; do not select a different track from this root."
  }
}

#trivy:ignore:AWS-0089 Short-lived test archive; access logging needs a second bucket and the track is destroyed after each run
resource "aws_s3_bucket" "archive" {
  #checkov:skip=CKV_AWS_18:Short-lived test archive; access logging needs a second bucket and the track is destroyed after each run
  #checkov:skip=CKV_AWS_144:Single-region test archive deleted at destroy; cross-region replication adds cost for no benefit
  #checkov:skip=CKV_AWS_145:Encrypted with S3-managed keys (SSE-S3); a customer managed key adds cost for no lab benefit
  #checkov:skip=CKV2_AWS_62:Nothing consumes events from this bucket
  bucket_prefix = "${var.project_name}-raw-"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "archive" {
  bucket                  = aws_s3_bucket.archive.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
#trivy:ignore:AWS-0132 Encrypted with S3-managed keys (SSE-S3); a customer managed key adds cost for no lab benefit
resource "aws_s3_bucket_server_side_encryption_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_s3_bucket_versioning" "archive" {
  bucket = aws_s3_bucket.archive.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_policy" "archive" {
  bucket = aws_s3_bucket.archive.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource  = [aws_s3_bucket.archive.arn, "${aws_s3_bucket.archive.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
resource "aws_s3_bucket_lifecycle_configuration" "archive" {
  bucket = aws_s3_bucket.archive.id
  rule {
    id     = "expire-raw-events"
    status = "Enabled"
    filter {}
    expiration { days = var.archive_retention_days }
    noncurrent_version_expiration { noncurrent_days = var.archive_retention_days }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
  depends_on = [aws_s3_bucket_versioning.archive]
}

resource "aws_iam_role_policy" "lab_archive" {
  for_each = local.lab_set
  name     = "${var.project_name}-raw-archive"
  role     = each.value.iam_role_name
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource = "${aws_s3_bucket.archive.arn}/raw/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = aws_s3_bucket.archive.arn
      },
      {
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = [aws_ssm_parameter.vector_config.arn, aws_ssm_parameter.mcp_config.arn]
      }
    ]
  })
}

resource "aws_ssm_parameter" "vector_config" {
  #checkov:skip=CKV2_AWS_34:Holds the Vector configuration only (bucket name, backend private IP); no secret
  name  = "/logging-bakeoff/${var.track}/vector-config"
  type  = "String"
  value = local.vector_config
}

resource "aws_ssm_parameter" "mcp_config" {
  #checkov:skip=CKV2_AWS_34:Holds the Lab Logs MCP server settings only (backend private address, local port); no secret
  name = "/logging-bakeoff/${var.track}/mcp-config"
  type = "String"
  value = jsonencode({
    backend_url = "https://${aws_instance.backend.private_ip}"
    port        = local.mcp_port
  })
}

resource "aws_iam_role" "backend" {
  name_prefix        = "${var.project_name}-backend-"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "backend_ssm" {
  role       = aws_iam_role.backend.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "backend" { role = aws_iam_role.backend.name }

resource "aws_security_group" "backend" {
  name_prefix = "${var.project_name}-backend-"
  description = "${var.track} backend; intake only from the root lab host"
  vpc_id      = data.aws_vpc.default.id
  dynamic "ingress" {
    for_each = local.lab_set
    content {
      description     = "${var.track} collector intake and Lab Logs MCP queries from root lab host"
      from_port       = 443
      to_port         = 443
      protocol        = "tcp"
      security_groups = [ingress.value.security_group_id]
    }
  }
  egress {
    description = "HTTPS for package and container downloads"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    #trivy:ignore:AWS-0104 Outbound package and image downloads from public mirrors and registries; no inbound Internet rule
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "HTTP for Ubuntu package mirrors"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    #trivy:ignore:AWS-0104 Outbound package and image downloads from public mirrors and registries; no inbound Internet rule
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "backend" {
  #checkov:skip=CKV_AWS_88:Public IP is used only for outbound package and image downloads in the default VPC; no inbound rule allows the Internet and the UI is reached through SSM
  #checkov:skip=CKV_AWS_135:Current instance types (the default t3) are EBS-optimized by default; forcing the flag would break older types
  #checkov:skip=CKV_AWS_126:Detailed (1-minute) EC2 monitoring costs extra for a short-lived test backend
  ami                         = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type               = var.backend_instance_type
  subnet_id                   = local.subnet_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.backend.id]
  iam_instance_profile        = aws_iam_instance_profile.backend.name
  user_data = templatefile("${path.module}/backend-bootstrap.sh.tftpl", {
    backend     = var.track
    compose_b64 = base64gzip(templatefile("${path.module}/compose.yaml.tftpl", { backend = var.track }))
    gateway_b64 = base64gzip(file("${path.module}/gateway.py"))
  })
  user_data_replace_on_change = true
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = 80
    encrypted             = true
    delete_on_termination = true
  }
  tags       = { Name = "${var.project_name}-${var.track}" }
  depends_on = [aws_iam_role_policy_attachment.backend_ssm]
}

#trivy:ignore:AWS-0089 Holds only the Distributor package built from this repository; deleted at destroy
#trivy:ignore:AWS-0090 Terraform rewrites the package object on each apply; the repository is the history
resource "aws_s3_bucket" "packages" {
  #checkov:skip=CKV_AWS_18:Holds only the Distributor package built from this repository; deleted at destroy
  #checkov:skip=CKV_AWS_21:Terraform rewrites the package object on each apply; the repository is the history
  #checkov:skip=CKV_AWS_144:Single-region package bucket deleted at destroy; cross-region replication adds cost for no benefit
  #checkov:skip=CKV_AWS_145:Encrypted with S3-managed keys (SSE-S3); a customer managed key adds cost for no lab benefit
  #checkov:skip=CKV2_AWS_61:Objects are replaced on every apply and deleted with the track; no lifecycle rules needed
  #checkov:skip=CKV2_AWS_62:Nothing consumes events from this bucket
  bucket_prefix = "${var.project_name}-packages-"
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "packages" {
  bucket                  = aws_s3_bucket.packages.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
#trivy:ignore:AWS-0132 Encrypted with S3-managed keys (SSE-S3); a customer managed key adds cost for no lab benefit
resource "aws_s3_bucket_server_side_encryption_configuration" "packages" {
  bucket = aws_s3_bucket.packages.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_object" "agent" {
  for_each    = local.selected_packages
  bucket      = aws_s3_bucket.packages.id
  key         = "${each.key}/${var.package_version}/package.zip"
  source      = "${path.module}/packages/${var.track}-${each.key}.zip"
  source_hash = filesha256("${path.module}/packages/${var.track}-${each.key}.zip")
}

resource "aws_ssm_document" "agent" {
  for_each      = local.selected_packages
  name          = "${var.project_name}-${each.key}-agent"
  document_type = "Package"
  version_name  = var.package_version
  content = jsonencode({
    schemaVersion = "2.0",
    version       = var.package_version,
    publisher     = "ai-cloud-lab",
    packages      = { ubuntu = { _any = { x86_64 = { file = "package.zip" } } } },
    files         = { "package.zip" = { checksums = { sha256 = filesha256("${path.module}/packages/${var.track}-${each.key}.zip") } } }
  })
  attachments_source {
    key    = "SourceUrl"
    values = ["s3://${aws_s3_bucket.packages.bucket}/${each.key}/${var.package_version}/"]
  }
  depends_on = [aws_s3_object.agent]
}

resource "aws_ssm_association" "agent" {
  for_each         = local.lab == null ? toset([]) : local.selected_packages
  name             = "AWS-ConfigureAWSPackage"
  association_name = "${var.project_name}-${each.key}-install"
  # additionalArguments carries a hash of the package's settings. A settings change (such as a
  # replaced backend's new IP) updates the association, which reinstalls the package so it reads
  # the new settings. The install scripts ignore the value.
  parameters = {
    action              = "Install"
    installationType    = "Uninstall and reinstall"
    name                = aws_ssm_document.agent[each.key].name
    version             = var.package_version
    additionalArguments = jsonencode({ SSM_settings_sha256 = sha256(each.key == "vector" ? aws_ssm_parameter.vector_config.value : aws_ssm_parameter.mcp_config.value) })
  }
  targets {
    key    = "InstanceIds"
    values = [local.lab.instance_id]
  }
  depends_on = [aws_iam_role_policy.lab_archive]
}
