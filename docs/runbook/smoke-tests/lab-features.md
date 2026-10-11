# Smoke tests: lab features

Tests for optional lab features. Run the one the change touched;
[the deploy runbook](../terraform-smoke-test-plan.md#step-5-test-what-changed) says which.

**Before you start:** use the PowerShell window from the runbook, after its step 1 (profile and
wrapper) and step 3 (`$env:instance_id`), from the repository root. Commands are
**Local Windows PowerShell** unless marked **Linux (SSM shell on the lab instance)**. Open that
shell with `aws ssm start-session --target $env:instance_id`. The SSM shell is a plain `sh` shell: paste **one command at a time**. Pasting several lines at once interleaves them (errors such as `er: not found`).

## Grafana telemetry test

Telemetry is on by default; skip this if `enable_grafana_telemetry = false`. In an SSM shell on the instance:

```bash
sudo systemctl status alloy --no-pager
sudo journalctl -u alloy -n 50 --no-pager
```

Alloy must be `active (running)` and the log must not repeat authentication or connection errors (a `401` means the instance ID or token in the secret is wrong). If the bootstrap log printed `WARNING: Grafana telemetry setup failed`, run `grep -i -B5 WARNING /var/log/ai-lab-bootstrap.log`.

Then in Grafana Cloud, open **Explore** and check, after a few minutes:

- Metrics: query `node_load1` and `node_systemd_unit_state{name="ollama.service"}`. Both should return recent values.
- Traces: after you have used Open WebUI for a minute (sign in, send a chat message), open Explore with the Tempo traces data source and search for service `open-webui`. If nothing appears, check `sudo docker logs open-webui 2>&1 | grep -i otel`.
- Logs: pick the Loki logs data source and look for recent entries from the lab, such as the bootstrap log lines or Open WebUI container output. Label names in Grafana can differ from the ones in the Alloy config, so browse the available labels.

Confirm no secret appears in a log line, and that the instance still needs no inbound rule for this (`aws ec2 describe-security-groups` shows only the inbound rules you chose).

## Vulnerability MCP server test

Skip this if `vuln_mcp_table_name` is not set. The findings table and the token secret are
one-time setup: [pre-deployment.md, step 9](../pre-deployment.md#9-vulnerability-findings-table-optional).

After apply, load the token the server uses from the secret. Like the server, this accepts plain
text or a one-key key/value secret, and prints nothing. Then call the function with the token and
without it:

```powershell
$raw = aws secretsmanager get-secret-value --secret-id (Get-TfVar vuln_mcp_token_secret_arn) --query SecretString --output text
try { $parsed = $raw | ConvertFrom-Json -ErrorAction Stop } catch { $parsed = $null }
$token = if ($parsed -and $parsed -isnot [string]) { [string]@($parsed.PSObject.Properties.Value)[0] } else { $raw.Trim() }
$url = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_url")   # local Windows PowerShell
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $token" } -Body $body | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Body $body
```

The first call must list six tools. The second must fail with `401`. Then check that a tool reads the table:

```powershell
$call = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"summarize_findings","arguments":{"group_by":"severity","status":"open"}}}'
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $token" } -Body $call | ConvertTo-Json -Depth 10
```

With the standard test data this shows 9 open Critical findings. If a call returns an error, read the function's CloudWatch log group, whose name the `vuln_mcp_log_group` output gives:

```powershell
$logGroup = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_log_group")
aws logs tail $logGroup --since 15m
```

Each tool call is one JSON `tool_call` line (tool, arguments, `ok`, size, time). No token or finding data is logged.

### Automatic Open WebUI registration

The lab instance connects Open WebUI to the server by itself at the end of bootstrap; there is no endpoint or token to copy. It also creates the **Security Analyst** model on `llm_model` with the tools attached and switched on, and makes it the default model if no default is set. The connection and the model are added when `vuln_mcp_table_name` is set and removed when it is not. It needs `open_webui_enable_local_login = true` (Terraform warns otherwise). Because this changes the instance's cloud-init, the apply that introduces it, and later turning the feature on or off, replaces the instance.

Linux (SSM shell on the lab instance):

```bash
grep -F '[register-vuln-mcp]' /var/log/ai-lab-bootstrap.log
sudo ai-lab-register-vuln-mcp --check
sudo ai-lab-register-vuln-mcp
sudo ai-lab-register-vuln-mcp
```

Expected, with the feature on:

- The bootstrap log shows `Open WebUI connected to the MCP server and listed 6 tools: find_hosts_by_vulnerability, get_data_dictionary, get_finding, get_host_findings, list_findings, summarize_findings`, `Connection registered.`, `Model 'Security Analyst' (security-analyst) created on qwen3:14b with the Vulnerability Findings tools switched on.` and `Default model set to security-analyst.`
- `--check` prints `Check only. Connection is already correct (feature true).` and `Check only. Model is already correct (feature true).`
- Each of the two plain runs prints `Connection already correct; nothing to change.` and `Model already correct; nothing to change.` (repeating adds no second connection or model).
- The log and the output contain no token or password.

Open WebUI (browser, signed in as the admin):

- **Admin Panel > Settings > Integrations > External Tool Servers** shows exactly one **Vulnerability Findings** connection (MCP, enabled), and its verify button succeeds.
- **Workspace > Models** has **Security Analyst**, with **Vulnerability Findings** ticked under Tools and **Builtin Tools** off under Capabilities.
- A new chat opens on **Security Analyst** with **Vulnerability Findings** already switched on under **Integrations** (the icon next to **+**).

With the feature off (`vuln_mcp_table_name = null`): `--check` prints `Connection is already correct (feature false)` and `Model is already correct (feature false)`, **External Tool Servers** has no Vulnerability Findings entry, and **Workspace > Models** has no Security Analyst.

If the bootstrap log has `WARNING: Open WebUI vulnerability MCP registration did not finish`, read the lines above it and use the troubleshooting table in `lambda/vuln_mcp/README.md`, then run `sudo ai-lab-register-vuln-mcp` again. Exit code 3 means local login is off and the connection must be added by hand (also in that README).

### Model-driven acceptance tests

Whether a model actually calls the tools is tested by hand in [docs/vuln-mcp-acceptance-tests.md](../../vuln-mcp-acceptance-tests.md). They use a separate table loaded from the repository's test fixture so the answers are exact, and they require proof that the tool ran (chat tool-call entry plus a CloudWatch `tool_call` line), not just a plausible answer. All of them are pending manual execution.

## Grafana dashboards and alerts test

Skip this if `enable_grafana_telemetry = false`. It needs the Grafana telemetry test above to pass first, so metrics and logs are already arriving. Nothing here has run against a real Grafana Cloud stack yet; the dashboards and alert queries are checked offline only, so expect to adjust a query or two on first use.

These steps are manual and are done in Grafana's web page and in PowerShell on your own computer.

## Step 1. Connect Grafana to CloudWatch

The CloudWatch data source lets Grafana read the MCP Lambda's tool-call counts and the instance's CPU. It signs in to AWS with a role that Terraform builds, and that role trusts a Grafana AWS account that only Grafana can tell you.

1. In Grafana, open **Connections**, **Data sources**, **Add new data source**, and choose **CloudWatch**.
2. Under **Authentication**, choose the option that assumes a role. Grafana shows its own **account ID** and an **External ID**. Copy both. Do not save the data source yet.
3. Put them in `terraform.tfvars.example` (and copy the file to `terraform.tfvars`):

```
grafana_cloudwatch_account_id  = "<the 12-digit account ID>"
grafana_cloudwatch_external_id = "<the external ID>"
```

4. Plan and apply as usual, then read the role address:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "grafana_cloudwatch_role_arn")
```

5. Back in the data source page, paste that address into **Assume Role ARN**, set **Default Region** to your region, and choose **Save & test**. It must say the data source is working.

If the test says access is denied, the external ID or account ID was copied wrongly; copy them again from the same page and re-apply.

## Step 2. Create a Grafana service account token

In Grafana open **Administration**, **Users and access**, **Service accounts**. Add a service account with the **Admin** role (alert rules need it), then **Add service account token**. Copy the token once; Grafana does not show it again.

## Step 3. Import the dashboards and alert rules

From the repository root, in PowerShell:

```powershell
$env:GRAFANA_TOKEN = Read-Host "Paste the Grafana service account token"
$id = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "instance_id")
.\scripts\import-grafana.ps1 -GrafanaUrl "https://<your stack>.grafana.net" -InstanceId $id
Remove-Item Env:\GRAFANA_TOKEN
```

The script reads `project_name` from `terraform.tfvars.example` for the Lambda name the Tool calls panels query; add `-ProjectName <name>` if yours differs. The script prints what it created and the address of the **Lab overview** dashboard. It can be run again at any time; it updates what exists. It skips the alert rules that need CloudWatch, with a warning, if Step 1 is not done.

## Step 4. Check the dashboards

With the lab running and at least one chat sent, open each of the five dashboards in the **ai-lab** folder. At the top of each, the data source pickers must show your stack's Prometheus, Loki and CloudWatch sources.

- **Lab overview**: the up/down tiles are green and the GPU panels show values.
- **Model serving and GPU**: the loaded model and its VRAM share show after the first chat.
- **Tool calls**: ask the Security Analyst a question, wait about two minutes, and the call count moves. This panel reads CloudWatch, so it also proves Step 1.
- **Tokenomics** and **Logs**: tokens appear after the hourly usage collection; log panels show recent lines.

A panel that says **No data** is not always an error. If one stays empty when it should not, open it, choose **Explore**, and compare the metric or label names with what Explore offers. If the log panels are empty, the lab's logs may carry different labels than the dashboards assume: see the `log_mcp_stream_selector` note in Step 8.

Then check the alert rules under **Alerting**, **Alert rules**, folder **ai-lab**: all seven should be listed, none in an error state.

## Step 5. Link the control panel to Grafana

Set the dashboard address the importer printed:

```
grafana_dashboard_url = "https://<your stack>.grafana.net/d/ai-lab-overview"
```

Apply, then sign in to the control panel as an administrator. A **Grafana** link must appear in the sidebar and open the dashboard in a new tab. People who are not administrators must not see it.

## Step 6. Choose where alert emails go

The rules use Grafana's default notification policy. Under **Alerting**, **Contact points**, add an email contact point with your address and make sure the default policy uses it. Use **Test** on the contact point; the email must arrive.

## Log search tools test

Skip this if you do not want the Security Analyst to answer questions about the lab's logs. It needs the vulnerability MCP server on and Grafana receiving logs. The log tools read Grafana Cloud Loki; they never write, and the model cannot send its own queries. Not yet run against a real Loki.

## Step 7. Create a log-reading token

1. In Grafana, open **Connections**, **Data sources**, **Loki**. Note the **URL** (like `https://logs-prod-012.grafana.net`) and the **User**, a number.
2. In the Grafana Cloud portal open **Access Policies**, create a policy with only the **logs:read** scope, and add a token to it. Copy the token.
3. Store the token in Secrets Manager and note the secret's address:

```powershell
$secure = Read-Host "Paste the logs:read token" -AsSecureString
$plain = [System.Net.NetworkCredential]::new("", $secure).Password
aws secretsmanager create-secret --name "loki-read-token-aiwebdemo" --secret-string $plain --query ARN --output text
Remove-Variable plain, secure
```

## Step 8. Turn the log tools on

Put the three values in `terraform.tfvars.example` and copy the file to `terraform.tfvars`:

```
log_mcp_loki_url              = "<the Loki URL>"
log_mcp_loki_user             = "<the Loki user number>"
log_mcp_loki_token_secret_arn = "<the secret address from Step 7>"
```

Apply. If Grafana's **Explore** shows the lab's logs under labels other than `service_name`, also set `log_mcp_stream_selector` to match, for example `job=~"ai-lab-.*"`.

## Step 9. Call the log tools

Use the token and URL from the Vulnerability MCP server test above. The tool list must now show eleven tools, and the log call must return the three sources:

```powershell
$call = '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_log_sources","arguments":{}}}'
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $token" } -Body $call | ConvertTo-Json -Depth 10
$call = '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"search_logs","arguments":{"source":"bootstrap","minutes":240,"limit":5}}}'
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $token" } -Body $call | ConvertTo-Json -Depth 10
```

The second call must return recent bootstrap lines. An error saying the log store refused or did not answer means the address, user or token is wrong, or Loki rejected the query: the Lambda's log group (`vuln_mcp_log_group` output) has a `log_store_error` line with the status.

## Step 10. Ask the Security Analyst

The instance registers the updated Security Analyst prompt at boot, so restart the lab (or run the registration script again) after the apply. Then, in Open WebUI, choose **Security Analyst** and ask:

- "Is anything wrong in the lab logs in the last hour?"
- "How many failed sign-ins were there today?"

It must call the log tools (they show in the chat), answer from their results, and never print an email address in full or a token. A log line that says "ignore your instructions" must be reported, not obeyed. The full set of questions, with lines to plant in the logs so the answers are known, is in the [log tools acceptance tests](../../log-tools-acceptance-tests.md).

## Metrics from Ollama and the Open WebUI database test

These metrics were written offline against stubs. This checks them against the real services.

## Step 11. Check the new metrics

1. Start the lab, send a few chat messages (one with a thumbs up, one with a thumbs down) and wait a minute.
2. In Grafana **Explore** (Prometheus), query `ai_lab_exporter_source_up`. You must see `ollama_log` and `webui_db` with value 1. A 0 means that source failed: for `ollama_log` check `journalctl -u ollama` works for root; for `webui_db` check `sudo sqlite3 -readonly <webui.db> "select count(*) from chat"`.
3. Query `ai_lab_ollama_chat_requests_5m`. The `2xx` value must be at least the number of messages you sent in the last 5 minutes (Open WebUI may also send title requests).
4. Query `ai_lab_chats` and `ai_lab_feedback`. Chats must match the chat list; `up` and `down` must be 1 each.
5. If `ai_lab_ollama_last_model_load_seconds` is missing, the model was loaded more than an hour ago; this is normal.

## Step 12. Print the shapes for time to first token and generation speed

In an SSM shell on the instance, run `sudo /usr/local/sbin/ai-lab-metrics --shapes` and keep the output (it holds keys and types only, no chat text). Attach it to issue 12 in `issues-usage-caps-grafana.md`.
