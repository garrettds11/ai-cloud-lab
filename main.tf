terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }

    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
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

# This password is used for the Ubuntu remote desktop account.
# It is suitable for a disposable lab, but it will be stored in Terraform state.
resource "random_password" "desktop_password" {
  length           = 24
  special          = true
  override_special = "!#$%&*+-=?@"
}

resource "aws_security_group" "ai_lab" {
  name_prefix = "${var.project_name}-"
  description = "AI lab - outbound only; administration through SSM"
  vpc_id      = data.aws_vpc.default.id

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

  subnet_id = sort(data.aws_subnets.default.ids)[0]

  vpc_security_group_ids = [
    aws_security_group.ai_lab.id
  ]

  iam_instance_profile = aws_iam_instance_profile.ssm.name

  # Needed during bootstrap for package, PyGPT, Ollama, and model downloads.
  # The security group still has no inbound rules.
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
    desktop_password = random_password.desktop_password.result
    ollama_model     = var.ollama_model
  })

  user_data_replace_on_change = true

  tags = {
    Name        = var.project_name
    Project     = var.project_name
    Environment = "lab"
    Application = "PyGPT-Ollama"
  }

  depends_on = [
    aws_iam_role_policy_attachment.ssm
  ]
}
