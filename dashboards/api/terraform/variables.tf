variable "aws_region" {
  description = "Region for the API, its functions and tables. Use the lab's region: the functions read the lab's SSM parameters and start its instance there."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile Terraform uses. Null uses the default credential chain (AWS_PROFILE, environment variables, SSO)."
  type        = string
  default     = null
}

variable "name_prefix" {
  description = "Prefix for the functions, roles, API, log groups and holding pool this stack creates. The default differs from the hand-built names (ai-lab-control-*), so both can exist side by side during the cutover."
  type        = string
  default     = "aiwebdemo-control"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,40}$", var.name_prefix))
    error_message = "name_prefix must be 3 to 41 lowercase letters, digits or hyphens, starting with a letter."
  }
}

variable "lab_project_name" {
  description = "The lab's project_name (from the root terraform.tfvars). The functions read the lab's parameters under /<lab_project_name>/control-panel and /<lab_project_name>/auto-stop."
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z0-9_.-]{1,64}$", var.lab_project_name))
    error_message = "lab_project_name must be the lab's project_name: letters, digits, '.', '_' or '-'."
  }
}

variable "panel_origin" {
  description = "The control panel's address, the only browser origin the API accepts (CORS), for example https://cp.aiwebdemo.click."
  type        = string

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+$", var.panel_origin))
    error_message = "panel_origin must be https://<host> with no path or trailing slash."
  }
}

variable "bootstrap_admins" {
  description = "Email addresses that are always panel administrators, whatever the users table says, so the panel can never be locked out. Usually one address."
  type        = list(string)

  validation {
    condition     = length(var.bootstrap_admins) > 0 && alltrue([for e in var.bootstrap_admins : can(regex("^[^@\\s,]+@[^@\\s,]+\\.[^@\\s,]+$", e))])
    error_message = "bootstrap_admins must hold at least one email address, without commas."
  }
}

variable "users_table_name" {
  description = "DynamoDB table of panel users and their roles (key email)."
  type        = string
  default     = "panel_users"
}

variable "entitlements_table_name" {
  description = "DynamoDB table of instance grants (keys userId and instanceId)."
  type        = string
  default     = "instance_entitlements"
}

variable "events_table_name" {
  description = "DynamoDB table of sign-ins, log lines and change history (keys pk and sk, TTL on expiresAt)."
  type        = string
  default     = "control_panel_events"
}

variable "adopt_existing_tables" {
  description = "True to import the three tables that were built by hand (with the names above) instead of creating them. Their data is kept. Set it for the first apply on an account that already has the panel, then leave it; it does nothing once the tables are in this stack's state."
  type        = bool
  default     = false
}

variable "event_ttl_days" {
  description = "How long sign-ins and log lines are kept before DynamoDB expires them."
  type        = number
  default     = 90

  validation {
    condition     = var.event_ttl_days >= 1 && var.event_ttl_days <= 3650
    error_message = "event_ttl_days must be between 1 and 3650."
  }
}

variable "log_retention_days" {
  description = "Retention for the functions' logs and the API access log."
  type        = number
  default     = 30
}

variable "throttle_rate_limit" {
  description = "Steady requests per second the API accepts across all callers. A lab panel needs very few."
  type        = number
  default     = 10
}

variable "throttle_burst_limit" {
  description = "Short burst of requests the API accepts above the steady rate."
  type        = number
  default     = 20
}
