provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
  default_tags { tags = { Environment = "lab", ManagedBy = "terraform", Stack = "logging-${var.track}" } }
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

locals {
  subnet_id = sort(data.aws_subnets.default.ids)[0]
  ports     = var.track == "wazuh" ? [1514, 1515] : [443]
  ui_port   = var.track == "wazuh" ? 5601 : (var.track == "graylog" ? 9000 : 8080)
  vector_config = templatefile("${path.module}/vector.toml.tftpl", {
    track          = var.track
    backend_ip     = aws_instance.backend.private_ip
    bucket         = aws_s3_bucket.archive.bucket
    aws_region     = var.aws_region
    container_name = var.open_webui_container_name
  })
  selected_packages = var.track == "wazuh" ? toset(["vector", "wazuh"]) : toset(["vector"])
}

check "track_name_matches_root" {
  assert {
    condition     = basename(path.cwd) == var.track
    error_message = "Run Terraform from logging/${var.track}; do not select a different track from this root."
  }
}

resource "aws_s3_bucket" "archive" {
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
  }
  depends_on = [aws_s3_bucket_versioning.archive]
}

resource "aws_iam_role_policy" "lab_archive" {
  name = "${var.project_name}-raw-archive"
  role = var.lab_iam_role_name
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
        Resource = concat([aws_ssm_parameter.vector_config.arn], var.track == "wazuh" ? [aws_ssm_parameter.wazuh_manager[0].arn] : [])
      }
    ]
  })
}

resource "aws_ssm_parameter" "vector_config" {
  name  = "/logging-bakeoff/${var.track}/vector-config"
  type  = "String"
  value = local.vector_config
}

resource "aws_ssm_parameter" "wazuh_manager" {
  count = var.track == "wazuh" ? 1 : 0
  name  = "/logging-bakeoff/${var.track}/wazuh-manager-ip"
  type  = "String"
  value = aws_instance.backend.private_ip
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
    for_each = local.ports
    content {
      description     = "${var.track} collector intake from root lab host"
      from_port       = ingress.value
      to_port         = ingress.value
      protocol        = "tcp"
      security_groups = [var.lab_security_group_id]
    }
  }
  egress {
    description = "HTTPS for package and container downloads"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    description = "HTTP for Ubuntu package mirrors"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_instance" "backend" {
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
    volume_size           = var.track == "wazuh" ? 120 : 80
    encrypted             = true
    delete_on_termination = true
  }
  tags       = { Name = "${var.project_name}-${var.track}" }
  depends_on = [aws_iam_role_policy_attachment.backend_ssm]
}

resource "aws_s3_bucket" "packages" {
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
  for_each         = local.selected_packages
  name             = "AWS-ConfigureAWSPackage"
  association_name = "${var.project_name}-${each.key}-install"
  parameters = {
    action           = "Install"
    installationType = "Uninstall and reinstall"
    name             = aws_ssm_document.agent[each.key].name
    version          = var.package_version
  }
  targets {
    key    = "InstanceIds"
    values = [var.lab_instance_id]
  }
  depends_on = [aws_iam_role_policy.lab_archive]
}
