variable "track" { type = string }
variable "aws_region" { type = string }
variable "aws_profile" {
  type    = string
  default = null
}
variable "project_name" { type = string }
variable "lab_project_name" {
  description = "project_name of the lab this track collects from. The lab publishes its IDs in /<lab_project_name>/lab/settings."
  type        = string
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
