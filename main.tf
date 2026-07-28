terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile
}

data "aws_caller_identity" "current" {}

# Canonical publishes current Ubuntu AMI IDs through AWS Systems Manager Parameter Store.
data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

# Keep the first lab version simple by using the account's default VPC.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "ai_lab" {
  name_prefix = "${var.project_name}-"
  description = "AI lab - private access through SSM and optional SSH tunnel"
  vpc_id      = data.aws_vpc.default.id

  dynamic "ingress" {
    for_each = var.enable_ssh ? [var.allowed_ssh_cidr] : []

    content {
      description = "Optional SSH access for local tunneling only"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
    }
  }

  # Open WebUI 8080 and Ollama 11434 are intentionally not exposed.
  # Use SSM port forwarding or the optional SSH tunnel for private access.
  egress {
    description = "Allow Internet access for package and model downloads"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name        = "${var.project_name}-sg"
    Project     = var.project_name
    Environment = "lab"
  }
}

resource "aws_iam_role" "ssm" {
  name_prefix = "${var.project_name}-ssm-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"

    Statement = [
      {
        Effect = "Allow"

        Principal = {
          Service = "ec2.amazonaws.com"
        }

        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Project = var.project_name
  }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "ssm" {
  name_prefix = "${var.project_name}-"
  role        = aws_iam_role.ssm.name
}

resource "aws_instance" "ai_lab" {
  ami           = data.aws_ssm_parameter.ubuntu_ami.value
  instance_type = var.instance_type
  key_name      = var.enable_ssh ? var.ssh_key_name : null

  subnet_id = sort(data.aws_subnets.default.ids)[0]

  vpc_security_group_ids = [
    aws_security_group.ai_lab.id
  ]

  iam_instance_profile = aws_iam_instance_profile.ssm.name

  # Needed during bootstrap for packages, Docker image pulls, Ollama, and model downloads.
  # Open WebUI and Ollama are not exposed by security group ingress.
  associate_public_ip_address = true

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.root_volume_size
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/cloud-init.sh.tpl", {
    ollama_model               = var.ollama_model
    open_webui_admin_email     = var.open_webui_admin_email
    open_webui_admin_name      = var.open_webui_admin_name
    open_webui_admin_password  = var.open_webui_admin_password
    open_webui_container_image = var.open_webui_container_image
    open_webui_container_name  = var.open_webui_container_name
    open_webui_host_port       = var.open_webui_host_port
    open_webui_docker_volume   = var.open_webui_docker_volume
    open_webui_ollama_base_url = "http://127.0.0.1:11434"
    open_webui_url             = "http://localhost:${var.open_webui_host_port}"
  })

  user_data_replace_on_change = true

  lifecycle {
    precondition {
      condition     = !var.enable_ssh || (var.ssh_key_name != null && var.allowed_ssh_cidr != null)
      error_message = "When enable_ssh is true, ssh_key_name and allowed_ssh_cidr must both be set."
    }
  }

  tags = {
    Name        = var.project_name
    Project     = var.project_name
    Environment = "lab"
    Application = "Open-WebUI-Ollama"
  }

  depends_on = [
    aws_iam_role_policy_attachment.ssm
  ]
}
