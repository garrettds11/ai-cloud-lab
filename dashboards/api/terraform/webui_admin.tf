# How the control panel reaches Open WebUI (issue #55, phase 0).
#
# Open WebUI listens only on the lab instance. The admin function never connects to it and
# never holds an Open WebUI credential. Instead it asks SSM to run this one document on the
# managed instance. The document accepts only the action names listed in
# local.webui_admin_actions (SSM refuses anything else before it runs), and its script is
# ../webui-admin.sh, kept in this stack so what can run is reviewed like any other code.
#
# Never point the panel at AWS-RunShellScript or any document that takes free-form commands:
# that would give root on the instance to anyone who can call the admin route.

locals {
  # Add an action here, in ../webui-admin.sh and in WEBUI_ACTIONS in ../handler.py together.
  webui_admin_actions = ["status"]
}

resource "aws_ssm_document" "webui_admin" {
  name            = "${var.name_prefix}-webui-admin"
  document_type   = "Command"
  document_format = "JSON"
  target_type     = "/AWS::EC2::Instance"

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Control panel: one named Open WebUI admin action on the lab instance. Prints one JSON line."
    parameters = {
      action = {
        type          = "String"
        description   = "The action to run."
        allowedValues = local.webui_admin_actions
      }
      expectedVersion = {
        type           = "String"
        description    = "The Open WebUI version the lab pins, or empty when the image is not pinned to a version."
        default        = ""
        allowedPattern = "^([0-9]{1,4}\\.[0-9]{1,4}\\.[0-9]{1,4})?$"
      }
    }
    mainSteps = [{
      action       = "aws:runShellScript"
      name         = "webuiAdmin"
      precondition = { StringEquals = ["platformType", "Linux"] }
      inputs = {
        timeoutSeconds = "60"
        # Run under bash whatever shell SSM uses; the quoted delimiter stops any expansion
        # before bash reads the script.
        runCommand = concat(
          ["bash -s <<'AI_LAB_WEBUI_ADMIN_EOF'"],
          split("\n", trimspace(replace(file("${path.module}/../webui-admin.sh"), "\r\n", "\n"))),
          ["AI_LAB_WEBUI_ADMIN_EOF"],
        )
      }
    }]
  })
}

# Settings the panel will own in Open WebUI, kept outside the instance so they survive its
# replacement (the instance's Open WebUI data does not, #13). Created now so phase 2 of #55
# only adds the writers and the reapply at boot; nothing reads or writes it yet.
resource "aws_dynamodb_table" "webui_desired_state" {
  #checkov:skip=CKV_AWS_119:Encrypted at rest with the AWS owned key; a customer managed key adds cost and a key policy to keep for no lab benefit
  name                        = "${var.name_prefix}-webui-desired-state"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "setting"
  deletion_protection_enabled = true

  attribute {
    name = "setting"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}
