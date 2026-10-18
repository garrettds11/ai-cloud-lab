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
  default = "ai-cloud-lab-victorialogs"
}
variable "lab_project_name" {
  description = "project_name of the lab to collect from; the lab publishes its IDs in /<lab_project_name>/lab/settings."
  type        = string
  default     = "aiwebdemo"
}
variable "backend_instance_type" {
  type    = string
  default = "t3.large"
}
variable "archive_retention_days" {
  type    = number
  default = 30
}

variable "tags" {
  description = "Extra tags for every resource in this track. Keep the project tag your other lab resources carry."
  type        = map(string)
  default     = { project = "aiwebdemo" }
}
