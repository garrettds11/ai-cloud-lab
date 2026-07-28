variable "aws_region" {
  description = "AWS region in which to create the AI lab."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "AWS CLI profile Terraform should use."
  type        = string
  default     = "CHANGEME"
}

variable "project_name" {
  description = "Name applied to the lab resources."
  type        = string
  default     = "pygpt-ollama-lab"
}

variable "instance_type" {
  description = "EC2 instance type used by the lab."
  type        = string
  default     = "t3.xlarge"
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
  description = "Ollama model that will automatically be downloaded."
  type        = string
  default     = "llama3.2:3b"
}
