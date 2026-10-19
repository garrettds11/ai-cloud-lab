module "track" {
  source                 = "../modules/track"
  track                  = "victorialogs"
  aws_region             = var.aws_region
  aws_profile            = var.aws_profile
  project_name           = var.project_name
  lab_project_name       = var.lab_project_name
  backend_instance_type  = var.backend_instance_type
  archive_retention_days = var.archive_retention_days
  tags                   = var.tags
}
