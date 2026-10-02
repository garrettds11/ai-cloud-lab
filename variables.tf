variable "aws_region" {
  description = "AWS region in which to create the AI lab."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Optional AWS CLI profile Terraform should use. Leave null to use environment, SSO, or instance-role credentials."
  type        = string
  default     = null
}

variable "project_name" {
  description = "Name applied to the lab resources."
  type        = string
  default     = "ollama-open-webui-lab"
}

variable "instance_type" {
  description = "EC2 instance type used by the lab."
  type        = string
  default     = "c7i.4xlarge"
}

variable "root_volume_size" {
  description = "Size of encrypted gp3 root volume in GiB."
  type        = number
  default     = 80

  validation {
    condition     = var.root_volume_size >= 40
    error_message = "The AI lab should have at least 40 GiB of storage."
  }
}

variable "ollama_model" {
  description = "Ollama model that will automatically be downloaded during bootstrap."
  type        = string
  default     = "llama3.2:3b"
}

variable "open_webui_admin_email" {
  description = "Email address for the initial Open WebUI local admin account."
  type        = string
  default     = "admin@example.local"
}

variable "open_webui_admin_name" {
  description = "Display name for the initial Open WebUI local admin account."
  type        = string
  default     = "Lab Admin"
}

variable "open_webui_admin_password_secret_arn" {
  description = "ARN of a pre-created Secrets Manager secret containing the Open WebUI admin password. Terraform does not create, update, or destroy this secret."
  type        = string
  default     = null

  validation {
    condition     = var.open_webui_admin_password_secret_arn == null || can(regex("^arn:[^:]+:secretsmanager:[^:]+:[0-9]{12}:secret:.+$", var.open_webui_admin_password_secret_arn))
    error_message = "open_webui_admin_password_secret_arn must be a valid Secrets Manager ARN."
  }
}

variable "open_webui_demo_users" {
  description = "Four local Open WebUI demo accounts created after the admin account. Each object contains an email and display name."
  type = list(object({
    email = string
    name  = string
  }))
  default = [
    {
      email = "demo1@example.local"
      name  = "Demo User 1"
    },
    {
      email = "demo2@example.local"
      name  = "Demo User 2"
    },
    {
      email = "demo3@example.local"
      name  = "Demo User 3"
    },
    {
      email = "demo4@example.local"
      name  = "Demo User 4"
    }
  ]

  validation {
    condition     = length(var.open_webui_demo_users) == 4
    error_message = "open_webui_demo_users must contain exactly four demo accounts for this lab scenario."
  }
}

variable "open_webui_demo_user_password" {
  description = "Temporary password assigned to the four local demo accounts. Users should change it from Profile after first login."
  type        = string
  default     = null
  sensitive   = true

  validation {
    condition     = var.open_webui_demo_user_password == null || length(var.open_webui_demo_user_password) >= 12
    error_message = "open_webui_demo_user_password must be at least 12 characters."
  }
}

variable "open_webui_container_image" {
  description = "Pinned Docker image used to run Open WebUI. Override deliberately when upgrading."
  type        = string
  default     = "ghcr.io/open-webui/open-webui:v0.11.4"
}

variable "open_webui_container_name" {
  description = "Name for the Open WebUI Docker container."
  type        = string
  default     = "open-webui"
}

variable "open_webui_docker_volume" {
  description = "Docker volume used to persist Open WebUI data."
  type        = string
  default     = "open-webui"
}

variable "open_webui_host_port" {
  description = "Host port for Open WebUI on the EC2 instance. This port is not exposed in the security group."
  type        = number
  default     = 8080

  validation {
    condition     = var.open_webui_host_port > 0 && var.open_webui_host_port < 65536
    error_message = "open_webui_host_port must be a valid TCP port."
  }
}

variable "enable_domain_access" {
  description = "Whether to publish Open WebUI through an internet-facing HTTPS Application Load Balancer."
  type        = bool
  default     = false
}

variable "domain_name" {
  description = "Public DNS name for Open WebUI, such as aiwebdemo.click."
  type        = string
  default     = "aiwebdemo.click"
}

variable "route53_zone_name" {
  description = "Public Route 53 hosted zone containing domain_name."
  type        = string
  default     = "aiwebdemo.click"
}

variable "acm_certificate_arn" {
  description = "Issued ACM certificate ARN for aiwebdemo.click in us-east-1. Override only when intentionally changing certificates."
  type        = string
  default     = "arn:aws:acm:us-east-1:394566733278:certificate/1163bb42-f265-4702-aad2-868c677ee07a"
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID used for Access resources. Required when Cloudflare integration is enabled."
  type        = string
  default     = null

  validation {
    condition     = var.cloudflare_account_id == null || can(regex("^[a-f0-9]{32}$", var.cloudflare_account_id))
    error_message = "cloudflare_account_id must be a 32-character hexadecimal Cloudflare account ID."
  }
}

variable "cloudflare_api_token_secret_arn" {
  description = "ARN of the pre-created Secrets Manager secret containing the Cloudflare API token. Terraform does not create or destroy this secret."
  type        = string
  default     = null

  validation {
    condition     = var.cloudflare_api_token_secret_arn == null || can(regex("^arn:[^:]+:secretsmanager:[^:]+:[0-9]{12}:secret:.+$", var.cloudflare_api_token_secret_arn))
    error_message = "cloudflare_api_token_secret_arn must be a valid Secrets Manager ARN."
  }
}

variable "cloudflare_access_allowed_emails" {
  description = "Email addresses allowed through Cloudflare Access for the public Open WebUI hostname."
  type        = set(string)
  default     = []

  validation {
    condition     = alltrue([for email in var.cloudflare_access_allowed_emails : can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", email))])
    error_message = "cloudflare_access_allowed_emails must contain valid email addresses."
  }
}

variable "enable_ssh" {
  description = "Whether to enable inbound SSH for tunneling. SSM remains available either way."
  type        = bool
  default     = false
}

variable "ssh_key_name" {
  description = "Existing EC2 key pair name to attach when enable_ssh is true."
  type        = string
  default     = null
}

variable "allowed_ssh_cidr" {
  description = "CIDR allowed to reach TCP/22 when enable_ssh is true. Do not use 0.0.0.0/0 for this lab."
  type        = string
  default     = null

  validation {
    condition     = var.allowed_ssh_cidr == null || (can(cidrhost(var.allowed_ssh_cidr, 0)) && var.allowed_ssh_cidr != "0.0.0.0/0")
    error_message = "allowed_ssh_cidr must be a valid CIDR and must not be 0.0.0.0/0."
  }
}
