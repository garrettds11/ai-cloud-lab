module "track" {
  source                    = "../modules/track"
  track                     = "quickwit"
  aws_region                = var.aws_region
  aws_profile               = var.aws_profile
  project_name              = var.project_name
  lab_instance_id           = var.lab_instance_id
  lab_security_group_id     = var.lab_security_group_id
  lab_iam_role_name         = var.lab_iam_role_name
  backend_instance_type     = var.backend_instance_type
  open_webui_container_name = var.open_webui_container_name
  archive_retention_days    = var.archive_retention_days
  tags                      = var.tags
}
