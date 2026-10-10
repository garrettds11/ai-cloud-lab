# The control panel API as its own Terraform stack.
#
# The lab (the repository root) is applied and destroyed often. The panel's API must outlive
# it, so it has its own configuration and its own state, applied once and then only when the
# API changes. See README.md in this folder for the first-time setup and the cutover from the
# hand-built API.

terraform {
  # 1.7 or later: import blocks with for_each (adopting the existing tables is optional).
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  default_tags {
    tags = {
      Project   = var.name_prefix
      ManagedBy = "terraform"
      Stack     = "control-api"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
