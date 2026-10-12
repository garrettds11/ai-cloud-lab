variable "aws_region" { type = string }
variable "aws_profile" {
  type    = string
  default = null
}
variable "name" {
  type    = string
  default = "ai-lab-scanner"
}
variable "lab_instance_id" {
  type = string
  validation {
    condition     = can(regex("^i-[0-9a-f]{8,17}$", var.lab_instance_id))
    error_message = "Supply the lab EC2 instance ID."
  }
}
variable "lab_private_ip" {
  type = string
  validation {
    condition     = can(cidrhost("${var.lab_private_ip}/32", 0)) && can(regex("^[0-9.]+$", var.lab_private_ip))
    error_message = "Supply the lab private IPv4 address."
  }
}
variable "lab_vpc_id" {
  type = string
  validation {
    condition     = can(regex("^vpc-[0-9a-f]{8,17}$", var.lab_vpc_id))
    error_message = "Supply the lab VPC ID."
  }
}
variable "lab_route_table_ids" {
  type = set(string)
  validation {
    condition     = length(var.lab_route_table_ids) > 0 && alltrue([for id in var.lab_route_table_ids : can(regex("^rtb-[0-9a-f]{8,17}$", id))])
    error_message = "Supply the route tables used by the lab subnet."
  }
}
variable "lab_security_group_id" {
  type = string
  validation {
    condition     = can(regex("^sg-[0-9a-f]{8,17}$", var.lab_security_group_id))
    error_message = "Supply the lab security group ID."
  }
}
variable "lab_events_table_name" {
  description = "Control-panel events table. Scan-initiated starts are charged to the scanner identity."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]{3,255}$", var.lab_events_table_name))
    error_message = "Supply a DynamoDB table name."
  }
}
variable "public_hostname" {
  description = "Public application hostname, without scheme or path. Greenbone reaches it through the Internet."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9.-]{1,251}[A-Za-z0-9]$", var.public_hostname))
    error_message = "Use a DNS hostname without a scheme or path."
  }
}
variable "control_panel_hostname" {
  description = "Additional public Greenbone target for the control panel."
  type        = string
  default     = "cp.aiwebdemo.click"
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9.-]{1,251}[A-Za-z0-9]$", var.control_panel_hostname))
    error_message = "Use a DNS hostname without a scheme or path."
  }
}
variable "control_panel_functions" {
  description = "Explicit deployed ZIP functions to assess, including customer and admin; add spend if deployed."
  type        = map(object({ arn = string, role_arn = string }))
  validation {
    condition = contains(keys(var.control_panel_functions), "customer") && contains(keys(var.control_panel_functions), "admin") && alltrue([
      for name, function in var.control_panel_functions :
      can(regex("^[a-z][a-z0-9_-]*$", name)) &&
      can(regex("^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:function:[A-Za-z0-9_-]+$", function.arn)) &&
      can(regex("^arn:aws:iam::[0-9]{12}:role/[A-Za-z0-9/+=,.@_-]+$", function.role_arn))
    ])
    error_message = "Configure customer and admin with unqualified Lambda ARNs and execution-role ARNs."
  }
}
variable "control_panel_layer_version_arns" {
  description = "Exact layer versions authorized for download; a new unlisted layer makes coverage incomplete."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for arn in var.control_panel_layer_version_arns : can(regex("^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:layer:[A-Za-z0-9_-]+:[0-9]+$", arn))])
    error_message = "Supply versioned Lambda layer ARNs."
  }
}
variable "control_panel_policy_arns" {
  description = "Managed policies/permissions boundaries authorized for inspection. Inline role policies need no entries."
  type        = set(string)
  default     = []
  validation {
    condition     = alltrue([for arn in var.control_panel_policy_arns : can(regex("^arn:aws:iam::(aws|[0-9]{12}):policy/[A-Za-z0-9/+=,.@_-]+$", arn))])
    error_message = "Supply IAM policy ARNs."
  }
}
variable "control_panel_api_id" {
  type = string
  validation {
    condition     = can(regex("^[a-z0-9]+$", var.control_panel_api_id))
    error_message = "Supply the deployed HTTP API ID."
  }
}
variable "control_panel_jwt_issuer" {
  description = "Expected live JWT issuer (the active lab Cognito pool, not the holding pool)."
  type        = string
  validation {
    condition     = can(regex("^https://[^'\n\r ]+$", var.control_panel_jwt_issuer))
    error_message = "Supply an HTTPS issuer."
  }
}
variable "control_panel_jwt_audiences" {
  type = set(string)
  validation {
    condition     = length(var.control_panel_jwt_audiences) > 0 && alltrue([for audience in var.control_panel_jwt_audiences : length(audience) > 0])
    error_message = "Supply at least one expected JWT audience."
  }
}
variable "control_panel_allowed_runtimes" {
  description = "Reviewed runtime baseline; update deliberately when migrating functions."
  type        = set(string)
  default     = ["python3.12"]
  validation {
    condition     = length(var.control_panel_allowed_runtimes) > 0
    error_message = "Supply a nonempty reviewed runtime baseline."
  }
}
variable "open_webui_image_digest" {
  description = "Exact deployed Open WebUI OCI image digest, e.g. ghcr.io/open-webui/open-webui@sha256:..."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9./_-]+(?:/[a-z0-9._-]+)*@sha256:[a-f0-9]{64}$", var.open_webui_image_digest))
    error_message = "Pin the deployed image by sha256 digest."
  }
}
variable "open_webui_container_name" {
  type    = string
  default = "open-webui"
  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$", var.open_webui_container_name))
    error_message = "Supply a Docker container name."
  }
}
variable "ssh_public_key" {
  description = "Public half of the dedicated scanner SSH key. The private half stays in Secrets Manager."
  type        = string
  validation {
    condition     = can(regex("^ssh-(ed25519|rsa) [A-Za-z0-9+/=]+(?: .*)?$", var.ssh_public_key))
    error_message = "Supply an OpenSSH public key."
  }
}
variable "ssh_private_key_secret_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$", var.ssh_private_key_secret_arn))
    error_message = "Supply a full Secrets Manager secret ARN."
  }
}
variable "greenbone_admin_password_secret_arn" {
  type = string
  validation {
    condition     = can(regex("^arn:aws:secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@-]+$", var.greenbone_admin_password_secret_arn))
    error_message = "Supply a full Secrets Manager secret ARN."
  }
}
variable "scanner_vpc_cidr" {
  type    = string
  default = "10.82.0.0/24"
}
variable "scanner_instance_type" {
  type    = string
  default = "m6i.xlarge"
}
variable "scanner_volume_gib" {
  type    = number
  default = 100
}
variable "scan_interval_hours" {
  type    = number
  default = 168
}
variable "scan_timeout_hours" {
  type    = number
  default = 12
}

check "scan_bounds" {
  assert {
    condition     = var.scan_interval_hours >= 24 && var.scan_timeout_hours >= 2 && var.scan_timeout_hours <= 24 && var.scanner_volume_gib >= 60
    error_message = "Use at least a daily interval, a 2-24 hour timeout and at least 60 GiB for Greenbone feeds."
  }
}
