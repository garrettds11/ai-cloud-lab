# Publishes what the add-on stacks need to know about this lab, so they read it instead of
# having IDs copied into their tfvars. Deploy order: control panel API, lab, then the add-ons
# (scanner, logging); destroy the add-ons before the lab.
#
# One SSM parameter, /<project_name>/lab/settings, holds JSON with IDs and names only. An
# add-on lists the /<project_name>/lab path first, so a missing lab is a warning there rather
# than a failed plan. The parameter is created and destroyed with the lab, and a rebuilt lab
# updates it in place; re-apply an add-on after the lab is rebuilt so it picks up the new IDs.

resource "aws_ssm_parameter" "addon_settings" {
  #checkov:skip=CKV2_AWS_34:The value is not secret (IDs and names); SecureString would only add a KMS dependency for every reader

  name        = "/${var.project_name}/lab/settings"
  description = "IDs and names of this lab for add-on stacks (logging, scanner)."
  type        = "String"
  value = jsonencode({
    instance_id               = aws_instance.ai_lab.id
    security_group_id         = aws_security_group.ai_lab.id
    iam_role_name             = aws_iam_role.ssm.name
    open_webui_container_name = var.open_webui_container_name
  })
}
