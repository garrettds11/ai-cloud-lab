# Open WebUI in the control panel

What the **Open WebUI** page of the control panel can read and change, what it never touches, and
how its changes survive a restart. All of it is built and tested offline only. Nothing has run
against a real Open WebUI v0.11.4 yet; the smoke tests in
`docs/runbook/smoke-tests/control-panel.md` ("Open WebUI settings test") are where that gets checked.

## How it works

Every action runs on the lab instance through one Systems Manager document
(`dashboards/api/terraform/webui_admin.tf`, script `dashboards/api/webui-admin.sh`). The script signs
in to Open WebUI as the lab's admin account, whose password it reads from Secrets Manager, asks or
changes one thing, and prints one line of JSON. The panel API starts it and the page reads the answer.
No Open WebUI password, API key or tool token ever reaches the browser or the API.

A change is reported as done only after the script reads the setting back from Open WebUI and it
matches. A wrong endpoint or an unexpected answer from a different Open WebUI version ends as a clear
failure, never as a false success.

## What the page can do

| Section | Action | Change? |
|---|---|---|
| Status, models in Ollama | `status` | Read only |
| Tool servers, Test connection | `tool-servers` | Read only. Asks Open WebUI to verify up to 5 enabled servers |
| Settings in effect | `settings` | Read only. A fixed list of settings, marked Global or User default |
| Export configuration | `export-config` | Read only. Downloads a JSON file |
| Default model | `set-default-model` | Yes |
| Model settings (temperature, context length) | `set-model-params` | Yes |
| Features: API keys, thumbs up and down, image generation, memory | `set-feature` | Yes, after a confirmation |
| Routes API keys may call | `set-feature` | Yes, after a confirmation |
| Add, change or remove a tool server | `upsert-tool-server`, `remove-tool-server` | Yes, after a confirmation |
| Import a skill (pasted text, up to 1800 characters) | `import-skill` | Yes, after a confirmation. Not saved for the next start |
| Reapply saved settings | `apply-desired` | Yes |
| Chat API test, Usage | `chat-test`, `usage` | Read only (see their own pages) |

Only administrators can use any of it, and the API checks again.

## Owned by Terraform, not by the page

The lab sets these at every start, so a change from the page would be overwritten:

- The model `security-analyst` and the tool server `vuln-findings` (and any server with the
  vuln-findings address). The page refuses changes to them.
- Everything else Terraform and the boot script set, such as sign-up, the default role for new users
  and the Open WebUI version. The page has no control for these; the settings list shows them.

Change those in Terraform.

## Never done from the page

Workspace Tools, Functions and Pipelines (they run code on the instance), the code interpreter
engine, importing a whole configuration, showing a secret, creating admin API keys, and Direct
Connections. Use Open WebUI's own Admin Settings for these. Skills with files (`scripts/`,
`references/`, a ZIP or a folder) are imported in Open WebUI itself (Workspace, Skills); the panel
only takes the text of a `SKILL.md`.

## Skills

Open WebUI v0.11.4 has Skills. The panel creates or replaces one skill by ID, with its name,
description and text, readable by all users, and can add it to the chosen models' skill lists. Open
WebUI stores the text and never runs it. Changing a skill that already exists keeps who may read it.
A skill is too large to keep with the saved settings, so it is not put back after a restart: import it
again, or import it in Open WebUI where it lives on the instance's data volume like the rest. If a
model cannot take the skill, the page says the skill was saved and names the models.

## Export scope

The file holds the settings list, the tool servers (name, type, address, path and sign-in kind) and
the models (numeric parameters only). It never holds passwords, API keys, tool server keys, headers or
OAuth settings, system prompts, users, chats, files or memories. If it is too large, models list their
IDs only.

## Tool servers and their keys

- The address must be HTTPS, with no user name, query or fragment. The host must resolve to a public
  address. A host on a private network is refused unless it is listed in the API stack variable
  `private_tool_hosts`.
- Open WebUI must pass its own connection check (the same as the Verify button) before the server is
  saved. A failed check saves nothing.
- A key is chosen from the secrets named `<project_name>/tool-tokens/<name>` in Secrets Manager. The
  page lists the names only. The instance role may read that prefix and nothing else. Create one
  (PowerShell):

  ```powershell
  aws secretsmanager create-secret --name "ai-cloud-lab/tool-tokens/my-server" --secret-string $token
  ```

  Replace `ai-cloud-lab` with the lab's `project_name`.

## Saved settings and what happens at a restart

A confirmed change is stored in the DynamoDB table `<name_prefix>-webui-desired-state` (a tool server
stores the key's secret name, never its value). The lab's Open WebUI data lives on the instance, so a
new instance starts from the Terraform defaults. About 150 seconds after each start, the spend job
(which already runs every 5 minutes) replays the saved settings once per start. It tries at most 3
times, and the page shows when they were last put back and how many failed. **Reapply saved
settings** does the same now.

The saved settings are replayed after the Terraform defaults, so a saved setting wins over the
default. To go back to a default, change it back from the page, or delete its row from the table.
Saved settings must fit in one command (about 2800 bytes); the page refuses a change that would not
fit and says which to remove.

## Version and API assumptions to check at the first deploy

The Open WebUI routes and field names below were written from the project's notes, not from a running
v0.11.4, and are guarded by read-back:

- The tool server list and verify routes under `/api/v1/configs/tool_servers`.
- The image settings route `/api/v1/images/config/update` and the `memory` permission key.
- The Skills routes under `/api/v1/skills/` and the model field `meta.skillIds`.
- The admin configuration key names listed in `webui-admin.sh` (`ADMIN_SETTING_KEYS`).

A setting this version does not have shows as "does not have that setting", and the settings list
names the ones that did not answer.
