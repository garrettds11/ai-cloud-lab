terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = "lab"
      ManagedBy   = "terraform"
    }
  }
}

provider "cloudflare" {}

locals {
  aws_cli_profile_arg  = var.aws_profile == null ? "" : " --profile ${var.aws_profile}"
  domain_resources     = var.enable_domain_access ? { domain = true } : {}
  route53_resources    = var.enable_domain_access && !var.enable_cloudflare_access ? { domain = true } : {}
  cloudflare_resources = var.enable_cloudflare_access ? { domain = true } : {}
  domain_certificate_arn = var.acm_certificate_arn != null ? var.acm_certificate_arn : (
    var.enable_domain_access ? data.aws_acm_certificate.domain[0].arn : null
  )
}

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
    for_each = concat(
      var.enable_ssh ? [{ kind = "ssh", cidr = var.allowed_ssh_cidr }] : [],
      var.enable_domain_access ? [{ kind = "alb", security_group_id = aws_security_group.alb["domain"].id }] : []
    )

    content {
      description     = ingress.value.kind == "ssh" ? "Optional SSH access for local tunneling only" : "Open WebUI from the public ALB only"
      from_port       = ingress.value.kind == "ssh" ? 22 : var.open_webui_host_port
      to_port         = ingress.value.kind == "ssh" ? 22 : var.open_webui_host_port
      protocol        = "tcp"
      cidr_blocks     = ingress.value.kind == "ssh" ? [ingress.value.cidr] : null
      security_groups = ingress.value.kind == "alb" ? [ingress.value.security_group_id] : null
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
    Name = "${var.project_name}-sg"
  }
}

resource "aws_security_group" "alb" {
  for_each    = local.domain_resources
  name_prefix = "${var.project_name}-alb-"
  description = "Public HTTPS access to Open WebUI through the application load balancer"
  vpc_id      = data.aws_vpc.default.id

  ingress {
    description = "HTTPS from the Internet"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP redirect to HTTPS"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow the ALB to reach its registered targets"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${var.project_name}-alb-sg"
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
    Name = "${var.project_name}-ssm-role"
  }
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.ssm.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_secretsmanager_secret" "open_webui_admin_password" {
  arn = var.open_webui_admin_password_secret_arn
}

resource "aws_iam_role_policy" "open_webui_admin_password" {
  name = "${var.project_name}-admin-password"
  role = aws_iam_role.ssm.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "secretsmanager:GetSecretValue"
      Resource = data.aws_secretsmanager_secret.open_webui_admin_password.arn
    }]
  })
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

  user_data = replace(templatefile("${path.module}/cloud-init.sh.tpl", {
    ollama_model                         = var.ollama_model
    open_webui_admin_email               = var.open_webui_admin_email
    open_webui_admin_name                = var.open_webui_admin_name
    open_webui_admin_password_secret_arn = data.aws_secretsmanager_secret.open_webui_admin_password.arn
    open_webui_demo_users_b64            = base64encode(jsonencode(var.open_webui_demo_users))
    open_webui_demo_password_b64         = var.open_webui_demo_user_password == null ? "" : base64encode(var.open_webui_demo_user_password)
    open_webui_container_image           = var.open_webui_container_image
    open_webui_container_name            = var.open_webui_container_name
    open_webui_host_port                 = var.open_webui_host_port
    open_webui_docker_volume             = var.open_webui_docker_volume
    open_webui_domain_access_enabled     = var.enable_domain_access ? "true" : "false"
    open_webui_ollama_base_url           = "http://127.0.0.1:11434"
    open_webui_url                       = var.enable_domain_access ? "https://${var.domain_name}" : "http://localhost:${var.open_webui_host_port}"
    aws_region                           = var.aws_region
  }), "\r\n", "\n")

  user_data_replace_on_change = true

  lifecycle {
    precondition {
      condition     = !var.enable_ssh || (var.ssh_key_name != null && var.allowed_ssh_cidr != null)
      error_message = "When enable_ssh is true, ssh_key_name and allowed_ssh_cidr must both be set."
    }

    precondition {
      condition     = var.open_webui_admin_password_secret_arn != null
      error_message = "open_webui_admin_password_secret_arn must reference a pre-created Secrets Manager secret before applying the lab."
    }

    precondition {
      condition = !var.enable_cloudflare_access || (
        var.enable_domain_access &&
        var.cloudflare_account_id != null &&
        var.cloudflare_api_token_secret_arn != null &&
        length(var.cloudflare_access_allowed_emails) > 0
      )
      error_message = "Cloudflare access requires enable_domain_access, cloudflare_account_id, cloudflare_api_token_secret_arn, and at least one allowed email."
    }

    precondition {
      condition     = length(var.open_webui_demo_users) == 0 || var.open_webui_demo_user_password != null
      error_message = "open_webui_demo_user_password must be set when demo users are enabled."
    }

  }

  tags = {
    Name        = var.project_name
    Application = "Open-WebUI-Ollama"
  }

  depends_on = [
    aws_iam_role_policy_attachment.ssm,
    aws_iam_role_policy.open_webui_admin_password
  ]
}

data "aws_route53_zone" "public" {
  count        = var.enable_domain_access && !var.enable_cloudflare_access ? 1 : 0
  name         = var.route53_zone_name
  private_zone = false
}

data "aws_acm_certificate" "domain" {
  count = var.enable_domain_access && var.acm_certificate_arn == null ? 1 : 0

  domain      = var.domain_name
  statuses    = ["ISSUED"]
  most_recent = true
}

resource "aws_lb" "domain" {
  for_each           = local.domain_resources
  name               = "${var.project_name}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb[each.key].id]
  subnets            = slice(sort(data.aws_subnets.default.ids), 0, 2)

  lifecycle {
    precondition {
      condition     = length(data.aws_subnets.default.ids) >= 2
      error_message = "Domain access requires at least two subnets in the default VPC for the ALB."
    }
  }

  tags = {
    Name = "${var.project_name}-alb"
  }
}

resource "aws_lb_target_group" "domain" {
  for_each = local.domain_resources
  name     = "${var.project_name}-tg"
  port     = var.open_webui_host_port
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id

  health_check {
    enabled  = true
    path     = "/"
    protocol = "HTTP"
    matcher  = "200-399"
  }

  tags = {
    Name = "${var.project_name}-tg"
  }
}

resource "aws_lb_target_group_attachment" "domain" {
  for_each         = local.domain_resources
  target_group_arn = aws_lb_target_group.domain[each.key].arn
  target_id        = aws_instance.ai_lab.id
  port             = var.open_webui_host_port
}

resource "aws_lb_listener" "http_redirect" {
  for_each          = local.domain_resources
  load_balancer_arn = aws_lb.domain[each.key].arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  for_each          = local.domain_resources
  load_balancer_arn = aws_lb.domain[each.key].arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = local.domain_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.domain[each.key].arn
  }
}

resource "aws_route53_record" "domain" {
  for_each = local.route53_resources
  zone_id  = data.aws_route53_zone.public[0].zone_id
  name     = var.domain_name
  type     = "A"

  alias {
    name                   = aws_lb.domain[each.key].dns_name
    zone_id                = aws_lb.domain[each.key].zone_id
    evaluate_target_health = true
  }
}

data "cloudflare_zones" "domain" {
  count     = var.enable_cloudflare_access ? 1 : 0
  name      = var.domain_name
  max_items = 1

  account = {
    id = var.cloudflare_account_id
  }
}

resource "cloudflare_dns_record" "domain" {
  for_each = local.cloudflare_resources
  zone_id  = data.cloudflare_zones.domain[0].result[0].id
  name     = var.domain_name
  type     = "CNAME"
  content  = aws_lb.domain[each.key].dns_name
  ttl      = 1
  proxied  = true
  comment  = "Managed by Terraform for the AI lab ALB"
}

resource "cloudflare_zero_trust_access_application" "domain" {
  for_each                  = local.cloudflare_resources
  account_id                = var.cloudflare_account_id
  name                      = "${var.project_name} Open WebUI"
  domain                    = var.domain_name
  type                      = "self_hosted"
  session_duration          = "8h"
  auto_redirect_to_identity = true

  policies = [{
    name       = "Allow approved Open WebUI users"
    decision   = "allow"
    precedence = 1
    include = [for email in var.cloudflare_access_allowed_emails : {
      email = { email = email }
    }]
  }]

  depends_on = [cloudflare_dns_record.domain]
}
