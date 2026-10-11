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
  description = "EC2 instance type used by the lab. NVIDIA GPU families (g4dn, g5, g6, g6e, gr6, p4d, p5, p6) boot AWS's Deep Learning Base GPU AMI with the driver preinstalled, and Ollama uses the GPU; others boot plain Ubuntu 24.04. GPU types need an EC2 'Running On-Demand G and VT instances' quota of at least their vCPU count."
  type        = string
  default     = "c7i.4xlarge"
}

variable "instance_hourly_cost_usd" {
  description = "What the instance costs per running hour, in US dollars. The usage and cost page, the spend caps and the Grafana tokenomics dashboard use it to price each session. Use the on-demand list price for the instance type in your region (about 0.8048 for g6.xlarge in us-east-1), or your effective rate if you have a savings plan. null means unknown: usage is still recorded but nothing is priced."
  type        = number
  default     = null

  validation {
    condition     = var.instance_hourly_cost_usd == null || (var.instance_hourly_cost_usd >= 0 && var.instance_hourly_cost_usd <= 1000)
    error_message = "instance_hourly_cost_usd must be null or a number from 0 through 1000."
  }
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

variable "llm_provider" {
  description = "LLM runtime/provider used to load the configured model."
  type        = string
  default     = "ollama"

  validation {
    condition     = contains(["ollama"], var.llm_provider)
    error_message = "llm_provider must currently be ollama."
  }
}

variable "ollama_context_length" {
  description = "Context window Ollama gives each model, in tokens (OLLAMA_CONTEXT_LENGTH). Tool definitions and the system prompt share it with the chat, so tool calling needs room: Ollama's own default of 4096 can crowd tools out. Larger values use more GPU or system memory. Changing it replaces the instance."
  type        = number
  default     = 16384

  validation {
    condition     = var.ollama_context_length >= 2048 && var.ollama_context_length <= 131072
    error_message = "ollama_context_length must be between 2048 and 131072."
  }
}

variable "llm_model" {
  description = "SLM/LLM model that will automatically be downloaded during bootstrap."
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

variable "auto_stop_absolute_max_minutes" {
  description = "Absolute time limit. 0 (default) means none. 15 or more stops the instance this many minutes after it boots, whatever the control panel does: a timer reset or the panel's timer policy cannot extend it or switch it off. The instance monitor and the watchdog both enforce it. Use it to cap the total length of one run when operators may reset the hard limit (auto_stop_max_uptime_minutes)."
  type        = number
  default     = 0

  validation {
    condition     = var.auto_stop_absolute_max_minutes == 0 || (var.auto_stop_absolute_max_minutes >= 15 && var.auto_stop_absolute_max_minutes <= 20160 && floor(var.auto_stop_absolute_max_minutes) == var.auto_stop_absolute_max_minutes)
    error_message = "auto_stop_absolute_max_minutes must be 0 (no limit) or a whole number from 15 through 20160."
  }
}

variable "auto_stop_max_resets" {
  description = "How many times the control panel's Reset button may extend the hard limit during one run. 0 (default) means no limit on the count. The panel's timer policy may set a lower number, never a higher one."
  type        = number
  default     = 0

  validation {
    condition     = var.auto_stop_max_resets >= 0 && var.auto_stop_max_resets <= 50 && floor(var.auto_stop_max_resets) == var.auto_stop_max_resets
    error_message = "auto_stop_max_resets must be a whole number from 0 (no limit) through 50."
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

  # IAM matches the ARN exactly, so a shortened one gives the readers no access at all.
  validation {
    condition     = var.grafana_credentials_secret_arn == null || can(regex("-[A-Za-z0-9]{6}$", var.grafana_credentials_secret_arn))
    error_message = "grafana_credentials_secret_arn must be the secret's full ARN, ending in a hyphen and the six random characters Secrets Manager adds to the name. Copy it with: aws secretsmanager describe-secret --secret-id <secret name> --query ARN --output text"
  }
}

variable "grafana_cloudwatch_account_id" {
  description = "The AWS account ID Grafana Cloud assumes roles from, shown in the instructions box on the Settings tab of the CloudWatch data source in Grafana (Connections, Data sources, Add, CloudWatch, Assume Role ARN). Together with grafana_cloudwatch_external_id it creates a read-only IAM role that Grafana uses to read the lab's Lambda logs and AWS metrics. Leave both null to create no role."
  type        = string
  default     = null

  validation {
    condition     = var.grafana_cloudwatch_account_id == null || can(regex("^[0-9]{12}$", var.grafana_cloudwatch_account_id))
    error_message = "grafana_cloudwatch_account_id must be a 12-digit AWS account ID."
  }
}

variable "grafana_cloudwatch_external_id" {
  description = "The external ID that Grafana Cloud shows next to the account ID on the CloudWatch data source's Settings tab. It is unique to your Grafana stack and stops other Grafana customers from using the role. Not a secret, but required with grafana_cloudwatch_account_id."
  type        = string
  default     = null

  validation {
    condition     = var.grafana_cloudwatch_external_id == null || can(regex("^[A-Za-z0-9+=,.@:/_-]{2,1224}$", var.grafana_cloudwatch_external_id))
    error_message = "grafana_cloudwatch_external_id may contain only letters, digits and + = , . @ : / _ - (2 to 1224 characters)."
  }
}

variable "grafana_dashboard_url" {
  description = "Address of the Lab overview dashboard in Grafana, for example https://yourstack.grafana.net/d/ai-lab-overview. The control panel shows a Grafana link in its sidebar when this is set. Terraform only copies it into config.js."
  type        = string
  default     = null

  validation {
    condition     = var.grafana_dashboard_url == null || can(regex("^https://[a-z0-9.-]+(/[^\\s]*)?$", var.grafana_dashboard_url))
    error_message = "grafana_dashboard_url must be an https:// address."
  }
}

variable "control_panel_url" {
  description = "Public address of the control panel dashboard, for example https://cp.aiwebdemo.click. Terraform uses it only as the sign-in and sign-out address of the panel's Cognito app client. It does not create or change the panel's hosting or DNS, which are built by hand. Leave null to skip the panel's app client and config.js. Requires enable_cognito = true."
  type        = string
  default     = null

  validation {
    condition     = var.control_panel_url == null || can(regex("^https://[a-z0-9.-]+$", var.control_panel_url))
    error_message = "control_panel_url must start with https:// and contain only a host name, with no trailing slash or path."
  }
}

variable "control_panel_api_from_ssm" {
  description = "Read the control panel API's address, IDs and table names from the SSM parameter /<project_name>/control-panel-api/settings, which the API stack (dashboards/api/terraform) publishes. Then control_panel_api_url, control_panel_api_id, control_panel_authorizer_id, control_panel_holding_pool_id, control_panel_users_table and control_panel_entitlements_table can stay null; any of them that is set wins over the parameter. If the parameter does not exist, plan shows a warning and the lab is built without the API."
  type        = bool
  default     = false
}

variable "control_panel_api_url" {
  description = "Address of the control panel's Control API. Terraform copies it into the config.js it publishes to the panel's bucket. Leave null until the API exists, or when control_panel_api_from_ssm is true."
  type        = string
  default     = null
}

variable "control_panel_users_table" {
  description = "Name of the control panel's DynamoDB users table (built by hand, key attribute email). When set, Terraform adds the demo users and their roles to it so the demo works straight away: every demo user as operators (may launch), plus user_mgrs (may change grants) for the odd demo users, and admin (may also change roles) for the administrator account. Leave null to add nothing. Terraform never changes any other row."
  type        = string
  default     = null
}

variable "control_panel_entitlements_table" {
  description = "Name of the control panel's DynamoDB grants table (built by hand, keys userId and instanceId). When set, Terraform grants the lab instance to every demo user who has the operators role, so the demo works straight away. Leave null to grant nothing; grants are then made in the panel. Terraform adds only these rows and removes them on destroy, and a later apply puts them back as seeded."
  type        = string
  default     = null
}

variable "control_panel_api_id" {
  description = "ID of the control panel's HTTP API in API Gateway. Not needed when control_panel_api_from_ssm is true. Together with control_panel_authorizer_id and control_panel_holding_pool_id, it lets apply point the API's sign-in authorizer at this lab's Cognito pool, and point it back at the holding pool on destroy. Leave null to leave the authorizer alone."
  type        = string
  default     = null
}

variable "control_panel_authorizer_id" {
  description = "ID of the JWT authorizer on the control panel's API (built by hand). Terraform only updates its issuer and audience, never creates or deletes it."
  type        = string
  default     = null
}

variable "control_panel_holding_pool_id" {
  description = "ID of the empty Cognito user pool that holds the authorizer's place when no lab is deployed (no users, no app clients, so it can never issue a usable token). Terraform points the authorizer back at it on destroy."
  type        = string
  default     = null
}

variable "control_panel_bucket" {
  description = "Name of the S3 bucket that serves the control panel pages (built by hand). When set, apply writes config.js, the panel's sign-in settings, into it, and destroy removes that one object. Terraform never writes any other page. Leave null to skip publishing config.js."
  type        = string
  default     = null
}

variable "control_panel_distribution_id" {
  description = "ID of the CloudFront distribution in front of the control panel's bucket (built by hand). When set together with control_panel_bucket, apply clears /config.js from its cache after each change. Leave null to skip."
  type        = string
  default     = null
}

variable "vuln_mcp_table_name" {
  description = "Name of the DynamoDB table of vulnerability findings (built by hand, for example aiwebdemo-vuln-findings). When set, Terraform deploys the read-only MCP server in lambda/vuln_mcp with a Function URL, so Open WebUI can query the table. Terraform only reads the table and never creates or changes it. Leave null to deploy nothing. Requires vuln_mcp_token_secret_arn."
  type        = string
  default     = null

  validation {
    condition     = var.vuln_mcp_table_name == null || can(regex("^[A-Za-z0-9_.-]{3,255}$", var.vuln_mcp_table_name))
    error_message = "vuln_mcp_table_name must be a valid DynamoDB table name (3 to 255 letters, digits, underscores, hyphens or dots)."
  }
}

variable "vuln_mcp_token_secret_arn" {
  description = "ARN of a pre-created Secrets Manager secret that holds only the bearer token callers must send to the MCP server, either as plain text or as a one-key key/value secret. Required when vuln_mcp_table_name is set. The function reads it at run time, so the token is never in Terraform state."
  type        = string
  default     = null

  validation {
    condition     = var.vuln_mcp_token_secret_arn == null || can(regex("^arn:[^:]+:secretsmanager:[^:]+:[0-9]{12}:secret:.+$", var.vuln_mcp_token_secret_arn))
    error_message = "vuln_mcp_token_secret_arn must be a valid Secrets Manager ARN."
  }

  # IAM matches the ARN exactly, so a shortened one gives the readers no access at all.
  validation {
    condition     = var.vuln_mcp_token_secret_arn == null || can(regex("-[A-Za-z0-9]{6}$", var.vuln_mcp_token_secret_arn))
    error_message = "vuln_mcp_token_secret_arn must be the secret's full ARN, ending in a hyphen and the six random characters Secrets Manager adds to the name. Copy it with: aws secretsmanager describe-secret --secret-id <secret name> --query ARN --output text"
  }
}

variable "log_mcp_loki_url" {
  description = "Base address of the Grafana Cloud Loki endpoint the log search tools read (for example https://logs-prod-012.grafana.net; copy it from the Loki data source's Settings tab in Grafana). Together with log_mcp_loki_user and log_mcp_loki_token_secret_arn it adds read-only log tools (search, count, error summary, sign-in events) to the vulnerability MCP server, so the Security Analyst can answer questions about the lab's logs. Needs vuln_mcp_table_name. Leave null to add no log tools."
  type        = string
  default     = null

  validation {
    condition     = var.log_mcp_loki_url == null || can(regex("^https://[A-Za-z0-9.-]+(:[0-9]+)?$", var.log_mcp_loki_url))
    error_message = "log_mcp_loki_url must be an https address with no path, for example https://logs-prod-012.grafana.net."
  }
}

variable "log_mcp_loki_user" {
  description = "The numeric Loki user (instance ID) shown on the Loki data source's Settings tab in Grafana. It is the basic-auth user name for log_mcp_loki_url. Required with log_mcp_loki_url."
  type        = string
  default     = null

  validation {
    condition     = var.log_mcp_loki_user == null || can(regex("^[0-9]{1,12}$", var.log_mcp_loki_user))
    error_message = "log_mcp_loki_user must be the numeric Loki user ID (digits only)."
  }
}

variable "log_mcp_loki_token_secret_arn" {
  description = "ARN of a pre-created Secrets Manager secret that holds only a Grafana Cloud access policy token with the logs:read scope, either as plain text or as a one-key key/value secret. Required with log_mcp_loki_url. The function reads it at run time, so the token is never in Terraform state."
  type        = string
  default     = null

  validation {
    condition     = var.log_mcp_loki_token_secret_arn == null || can(regex("^arn:[^:]+:secretsmanager:[^:]+:[0-9]{12}:secret:.+-[A-Za-z0-9]{6}$", var.log_mcp_loki_token_secret_arn))
    error_message = "log_mcp_loki_token_secret_arn must be the secret's full ARN, ending in a hyphen and the six random characters Secrets Manager adds to the name."
  }
}

variable "log_mcp_stream_selector" {
  description = "The Loki stream selector (without braces) that picks the lab's logs. The default matches every stream that has a service_name, which is how logs arrive over OpenTelemetry. If Grafana's Explore shows the lab's logs under other labels, put those here, for example job=~\"ai-lab-.*\"."
  type        = string
  default     = "service_name=~\".+\""

  validation {
    condition     = can(regex("^[A-Za-z0-9_]+(=~|=|!=|!~)\"[^\"\\\\{}\n]*\"(,\\s*[A-Za-z0-9_]+(=~|=|!=|!~)\"[^\"\\\\{}\n]*\")*$", var.log_mcp_stream_selector))
    error_message = "log_mcp_stream_selector must be one or more label matchers like service_name=~\".+\", separated by commas."
  }
}
