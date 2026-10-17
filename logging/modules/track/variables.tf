variable "track" { type = string }
variable "aws_region" { type = string }
variable "aws_profile" {
  type    = string
  default = null
}
variable "project_name" { type = string }
variable "lab_instance_id" { type = string }
variable "lab_security_group_id" { type = string }
variable "lab_iam_role_name" { type = string }
variable "open_webui_container_name" {
  type    = string
  default = "open-webui"
}
variable "backend_instance_type" { type = string }
variable "archive_retention_days" {
  type    = number
  default = 30
}
variable "package_version" {
  type    = string
  default = "1.0.0"
}
variable "tags" {
  description = "Extra tags for every resource in this track, such as the project tag. Merged over Environment, ManagedBy and Stack."
  type        = map(string)
  default     = {}
}
