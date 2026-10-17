variable "aws_region" {
  type    = string
  default = "us-east-1"
}
variable "aws_profile" {
  type    = string
  default = null
}
variable "project_name" {
  type    = string
  default = "ai-cloud-lab-graylog"
}
variable "lab_instance_id" { type = string }
variable "lab_security_group_id" { type = string }
variable "lab_iam_role_name" { type = string }
variable "backend_instance_type" {
  type    = string
  default = "t3.2xlarge"
}
variable "archive_retention_days" {
  type    = number
  default = 30
}

variable "open_webui_container_name" {
  type    = string
  default = "open-webui"
}
