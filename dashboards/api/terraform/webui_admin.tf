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
  # "set-role" is run by the spend caps job only; the admin function's route does not offer it.
  webui_admin_actions = [
    "status",
    "usage",
    "set-role",
    "chat-test",
    "tool-servers",
    "settings",
    "export-config",
    "set-default-model",
    "set-model-params",
    "set-feature",
    "upsert-tool-server",
    "remove-tool-server",
    "import-skill",
    "apply-desired",
  ]
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
      sinceHour = {
        type           = "String"
        description    = "For the usage action: only hours at or after this epoch hour start. Empty means all recorded hours."
        default        = ""
        allowedPattern = "^([0-9]{1,10})?$"
      }
      userId = {
        type           = "String"
        description    = "For the set-role action: the Open WebUI user ID. Empty for other actions."
        default        = ""
        allowedPattern = "^([A-Za-z0-9-]{1,64})?$"
      }
      role = {
        type          = "String"
        description   = "For the set-role action: pending (blocked) or user (restored). Empty for other actions."
        default       = ""
        allowedValues = ["", "pending", "user"]
      }
      model = {
        type           = "String"
        description    = "For the chat-test action: the model name. Empty for other actions."
        default        = ""
        allowedPattern = "^([A-Za-z0-9._:/-]{1,100})?$"
      }
      payload = {
        type           = "String"
        description    = "For the write actions: the request as base64 text, already checked by the control panel's API. Empty for other actions."
        default        = ""
        allowedPattern = "^[A-Za-z0-9+/=]{0,4000}$"
      }
      toolTokenPrefix = {
        type           = "String"
        description    = "For upsert-tool-server and apply-desired: the Secrets Manager name prefix that tool tokens may be chosen from. Empty when none are offered."
        default        = ""
        allowedPattern = "^[A-Za-z0-9/_.-]{0,100}$"
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
        timeoutSeconds = "180"
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
# replacement (the instance's Open WebUI data does not, #13). The admin function writes it after
# the instance confirms a change; the spend caps job replays it after an instance start (items whose
# key starts with "_" are bookkeeping, never replayed).
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
