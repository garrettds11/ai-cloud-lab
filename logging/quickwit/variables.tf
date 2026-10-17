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
  default = "ai-cloud-lab-quickwit"
}
variable "lab_instance_id" { type = string }
variable "lab_security_group_id" { type = string }
variable "lab_iam_role_name" { type = string }
variable "backend_instance_type" {
  type    = string
  default = "t3.large"
}
variable "archive_retention_days" {
  type    = number
  default = 30
}

variable "open_webui_container_name" {
  type    = string
  default = "open-webui"
}

variable "tags" {
  description = "Extra tags for every resource in this track. Keep the project tag your other lab resources carry."
  type        = map(string)
  default     = { project = "aiwebdemo" }
}
