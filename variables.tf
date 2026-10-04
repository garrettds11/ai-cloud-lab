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
  default     = "aiwebdemo"
}

variable "tags" {
  description = "Tags applied to every AWS resource. Setting this in terraform.tfvars replaces these defaults entirely, so list every tag you want, including any project or owner tag your organization uses. Each resource's Name tag is derived from project_name."
  type        = map(string)
  default = {
    Environment = "lab"
    ManagedBy   = "terraform"
    Application = "Open-WebUI"
  }
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
  description = "Demo users, ten by default. They are Cognito users when enable_cognito is true and local Open WebUI accounts otherwise. Each object contains an email and display name."
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
    },
    {
      email = "demo5@example.local"
      name  = "Demo User 5"
    },
    {
      email = "demo6@example.local"
      name  = "Demo User 6"
    },
    {
      email = "demo7@example.local"
      name  = "Demo User 7"
    },
    {
      email = "demo8@example.local"
      name  = "Demo User 8"
    },
    {
      email = "demo9@example.local"
      name  = "Demo User 9"
    },
    {
      email = "demo10@example.local"
      name  = "Demo User 10"
    }
  ]

  validation {
    condition     = length(var.open_webui_demo_users) >= 1 && length(var.open_webui_demo_users) <= 25
    error_message = "open_webui_demo_users must contain between 1 and 25 demo accounts."
  }
}

variable "open_webui_demo_user_password_secret_arn" {
  description = "ARN of a pre-created Secrets Manager secret containing the temporary password assigned to the demo accounts."
  type        = string
  default     = null

  validation {
    condition     = var.open_webui_demo_user_password_secret_arn == null || can(regex("^arn:[^:]+:secretsmanager:[^:]+:[0-9]{12}:secret:.+$", var.open_webui_demo_user_password_secret_arn))
    error_message = "open_webui_demo_user_password_secret_arn must be a valid Secrets Manager ARN."
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

variable "enable_cloudflare_access" {
  description = "Whether to manage the public domain record and Cloudflare Access policy for Open WebUI. Requires enable_domain_access."
  type        = bool
  default     = false
}

variable "enable_alb_http_redirect" {
  description = "Whether the ALB also listens on public port 80 and redirects to HTTPS. Disabled by default because the first HTTP request is unencrypted; enabling it is a usability tradeoff, not a security control."
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

variable "cloudflare_access_app_name" {
  description = "Name of the Cloudflare Access application. Visitors see it on the Access login page (Log in to <name>)."
  type        = string
  default     = "AI Web Demo"

  validation {
    condition     = length(trimspace(var.cloudflare_access_app_name)) > 0
    error_message = "cloudflare_access_app_name must not be empty."
  }
}

variable "cloudflare_access_session_duration" {
  description = "How long a Cloudflare Access login lasts before the user must authenticate again, such as 4h or 30m."
  type        = string
  default     = "4h"

  validation {
    condition     = can(regex("^[0-9]+(ms|s|m|h)$", var.cloudflare_access_session_duration))
    error_message = "cloudflare_access_session_duration must look like 30m or 4h."
  }
}

variable "enable_cognito" {
  description = "Whether to create an Amazon Cognito user pool for the lab users and use it as the sign-in for both Cloudflare Access and Open WebUI. Requires enable_domain_access, and cloudflare_access_team_domain when enable_cloudflare_access is true."
  type        = bool
  default     = false
}

variable "cognito_domain_prefix" {
  description = "Globally unique prefix for the Cognito hosted sign-in domain (<prefix>.auth.<region>.amazoncognito.com). Required when enable_cognito is true."
  type        = string
  default     = null

  validation {
    condition     = var.cognito_domain_prefix == null || can(regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$", var.cognito_domain_prefix))
    error_message = "cognito_domain_prefix must be lowercase letters, numbers, and hyphens, and must not start or end with a hyphen."
  }

  validation {
    condition     = var.cognito_domain_prefix == null || !can(regex("aws|amazon|cognito", var.cognito_domain_prefix))
    error_message = "cognito_domain_prefix must not contain the reserved words aws, amazon, or cognito."
  }
}

variable "cognito_extra_users" {
  description = "Additional Cognito users beyond open_webui_demo_users, such as administrators. Each object contains an email and display name."
  type = list(object({
    email = string
    name  = string
  }))
  default = []
}

variable "cognito_operator_emails" {
  description = "Emails of the Cognito users placed in the operators group. Operators may start the instances an administrator has granted them from the control panel dashboard; everyone else sees no instances there. Each email must belong to a Cognito user: the administrator, a demo user in open_webui_demo_users, or a user in cognito_extra_users."
  type        = list(string)
  default     = []
}

variable "cognito_user_manager_emails" {
  description = "Emails of the Cognito users placed in the user_mgrs group. User managers see the User management page in the control panel dashboard, where they grant or revoke each user's access to instances. Each email must belong to a Cognito user, as for cognito_operator_emails."
  type        = list(string)
  default     = []
}

variable "cloudflare_access_team_domain" {
  description = "Your Cloudflare Zero Trust team domain, such as example-team.cloudflareaccess.com (shown on the Access login page). Used for the Cognito callback URL when enable_cognito and enable_cloudflare_access are both true."
  type        = string
  default     = null

  validation {
    condition     = var.cloudflare_access_team_domain == null || can(regex("^[a-z0-9-]+\\.cloudflareaccess\\.com$", var.cloudflare_access_team_domain))
    error_message = "cloudflare_access_team_domain must look like example-team.cloudflareaccess.com."
  }
}

variable "extra_egress_cidrs" {
  description = "Additional destination CIDRs the instance may reach on any port, on top of the default outbound HTTPS (443) and HTTP (80). Leave empty unless the lab must reach a private service."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.extra_egress_cidrs : can(cidrhost(cidr, 0))])
    error_message = "extra_egress_cidrs must contain valid CIDR ranges."
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

variable "security_banner_text" {
  description = "Security and acceptable use notice shown as a banner at the top of Open WebUI after sign-in. Set to an empty string to show no banner. Edit it in terraform.tfvars; changing it replaces the EC2 instance on the next apply."
  type        = string
  default     = "This system is for authorized users only. Activity may be monitored and logged. By using this lab you agree to use it only for approved purposes: do not enter confidential, regulated, or personal data, do not create illegal, harmful, or abusive content, and do not try to bypass security controls. Misuse may result in loss of access."
}

variable "open_webui_enable_local_login" {
  description = "Whether Open WebUI accepts email and password sign-in for its local accounts (the admin account and the demo users). Set to false to allow sign-in only through Cognito. Requires enable_cognito = true. When false, the demo users are not created as local accounts, so everyone must exist as a Cognito user (see cognito_extra_users). Changing it replaces the EC2 instance on the next apply."
  type        = bool
  default     = true
}

variable "open_webui_default_user_role" {
  description = "Role given to people who first sign in to Open WebUI through Cognito and have no local account: user (can use the lab immediately) or pending (an administrator must activate each person in Admin Panel > Users). The Cognito user pool only allows administrator-created users, so user is the default."
  type        = string
  default     = "user"

  validation {
    condition     = contains(["user", "pending"], var.open_webui_default_user_role)
    error_message = "open_webui_default_user_role must be user or pending."
  }
}

variable "enable_origin_lockdown" {
  description = "When true, the ALB accepts HTTPS only from Cloudflare's published IP ranges (plus origin_lockdown_extra_cidrs), so visitors cannot bypass Cloudflare Access and the WAF by connecting to the ALB directly. Requires enable_cloudflare_access. Verify the site works through Cloudflare before turning it on."
  type        = bool
  default     = false
}

variable "cloudflare_ipv4_cidrs" {
  description = "Cloudflare's published IPv4 ranges (https://www.cloudflare.com/ips-v4), used when enable_origin_lockdown is true. Update this list when Cloudflare publishes changes."
  type        = list(string)
  default = [
    "173.245.48.0/20",
    "103.21.244.0/22",
    "103.22.200.0/22",
    "103.31.4.0/22",
    "141.101.64.0/18",
    "108.162.192.0/18",
    "190.93.240.0/20",
    "188.114.96.0/20",
    "197.234.240.0/22",
    "198.41.128.0/17",
    "162.158.0.0/15",
    "104.16.0.0/13",
    "104.24.0.0/14",
    "172.64.0.0/13",
    "131.0.72.0/22",
  ]

  validation {
    condition     = alltrue([for cidr in var.cloudflare_ipv4_cidrs : can(cidrhost(cidr, 0))])
    error_message = "cloudflare_ipv4_cidrs must contain valid IPv4 CIDR ranges."
  }
}

variable "cloudflare_ipv6_cidrs" {
  description = "Cloudflare's published IPv6 ranges (https://www.cloudflare.com/ips-v6), used when enable_origin_lockdown is true. The ALB is IPv4-only today, so these ranges are included for completeness."
  type        = list(string)
  default = [
    "2400:cb00::/32",
    "2606:4700::/32",
    "2803:f800::/32",
    "2405:b500::/32",
    "2405:8100::/32",
    "2a06:98c0::/29",
    "2c0f:f248::/32",
  ]

  validation {
    condition     = alltrue([for cidr in var.cloudflare_ipv6_cidrs : can(cidrhost(cidr, 0))])
    error_message = "cloudflare_ipv6_cidrs must contain valid IPv6 CIDR ranges."
  }
}

variable "origin_lockdown_extra_cidrs" {
  description = "Extra IPv4 CIDRs allowed to reach the ALB when enable_origin_lockdown is true, for example your own address as a /32 during recovery. Leave empty for normal use."
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for cidr in var.origin_lockdown_extra_cidrs : can(cidrhost(cidr, 0)) && cidr != "0.0.0.0/0"])
    error_message = "origin_lockdown_extra_cidrs must be valid CIDRs and must not be 0.0.0.0/0."
  }
}

variable "auto_stop_idle_minutes" {
  description = "Idle shutdown. 0 turns it off. 1 to 1440 stops the instance after this many minutes with no active Open WebUI users and no reply being generated; people who are active are never stopped by this. The idle clock starts when first-boot setup finishes and after every restart. Independent of auto_stop_max_uptime_minutes."
  type        = number
  default     = 60

  validation {
    condition     = var.auto_stop_idle_minutes >= 0 && var.auto_stop_idle_minutes <= 1440 && floor(var.auto_stop_idle_minutes) == var.auto_stop_idle_minutes
    error_message = "auto_stop_idle_minutes must be a whole number from 0 (idle shutdown off) through 1440."
  }
}

variable "auto_stop_max_uptime_minutes" {
  description = "Hard time limit. 0 (default) means no limit. 15 or more stops the instance this many minutes after it boots even if people are still using it, after an email warning shortly before. Right for a demo, for example 90. Independent of auto_stop_idle_minutes."
  type        = number
  default     = 0

  validation {
    condition     = var.auto_stop_max_uptime_minutes == 0 || (var.auto_stop_max_uptime_minutes >= 15 && var.auto_stop_max_uptime_minutes <= 10080 && floor(var.auto_stop_max_uptime_minutes) == var.auto_stop_max_uptime_minutes)
    error_message = "auto_stop_max_uptime_minutes must be 0 (no limit) or a whole number from 15 through 10080."
  }
}

variable "auto_stop_alert_email" {
  description = "Email address for auto-stop alerts (instance running too long, or the idle monitor not reporting). AWS sends a confirmation link that must be clicked once. Leave null only if you accept having no alerts."
  type        = string
  default     = null

  validation {
    condition     = var.auto_stop_alert_email == null || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.auto_stop_alert_email))
    error_message = "auto_stop_alert_email must be a valid email address."
  }
}

variable "enable_grafana_telemetry" {
  description = "Send host metrics, logs and Open WebUI traces to Grafana Cloud over OTLP/HTTP using Grafana Alloy on the instance. On by default, so grafana_otlp_endpoint, grafana_otlp_instance_id and grafana_credentials_secret_arn must be set; set this to false to run without telemetry. Needs outbound TCP 443 (already allowed). Changing it replaces the instance. Telemetry adds to Grafana Cloud data usage; see grafana-telemetry.md."
  type        = bool
  default     = true
}

variable "grafana_otlp_endpoint" {
  description = "Grafana Cloud OTLP endpoint, for example https://otlp-gateway-prod-us-east-3.grafana.net/otlp (no trailing slash). Not a secret. Required when enable_grafana_telemetry is true."
  type        = string
  default     = null

  validation {
    condition     = var.grafana_otlp_endpoint == null || can(regex("^https://[^\\s/]+(/[^\\s]*[^\\s/])?$", var.grafana_otlp_endpoint))
    error_message = "grafana_otlp_endpoint must be an https:// URL without a trailing slash."
  }
}

variable "grafana_otlp_instance_id" {
  description = "Grafana Cloud OTLP instance ID (a number, shown next to the OTLP endpoint). It is the sign-in name, not a secret, so it is set here; the token is in grafana_credentials_secret_arn. Required when enable_grafana_telemetry is true."
  type        = string
  default     = null

  validation {
    condition     = var.grafana_otlp_instance_id == null || can(regex("^[0-9]+$", var.grafana_otlp_instance_id))
    error_message = "grafana_otlp_instance_id must be the numeric Grafana Cloud instance ID."
  }
}

variable "grafana_credentials_secret_arn" {
  description = "ARN of a pre-created Secrets Manager secret that holds only the Grafana Cloud access policy token, either as plain text or as a one-key key/value secret. Required when enable_grafana_telemetry is true. The instance reads it at boot, so the token is never in Terraform state or user-data."
  type        = string
  default     = null

  validation {
    condition     = var.grafana_credentials_secret_arn == null || can(regex("^arn:[^:]+:secretsmanager:[^:]+:[0-9]{12}:secret:.+$", var.grafana_credentials_secret_arn))
    error_message = "grafana_credentials_secret_arn must be a valid Secrets Manager ARN."
  }
}
