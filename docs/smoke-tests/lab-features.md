# Smoke tests: lab features

Tests for optional lab features. Run the one the change touched;
[the deploy runbook](../../terraform-smoke-test-plan.md#step-5-test-what-changed) says which.

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

Skip this if `vuln_mcp_table_name` is not set. The findings table (`aiwebdemo-vuln-findings`, created by `create_vuln_table.py` in the `sec-data` repository) and the token secret are built by hand, so do the first step before deploying.

**Once, before the first deploy with this feature:** create the token secret and put its **full** ARN (ending in a hyphen and six characters) in `vuln_mcp_token_secret_arn` in `terraform.tfvars.example`, then commit it. Skip this when the secret already exists (`create-secret` then fails with `ResourceExistsException`, which is harmless):

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token = [Convert]::ToBase64String($bytes)
aws secretsmanager create-secret --name vuln-mcp-token-aiwebdemo --secret-string $token --region us-east-1 --query ARN --output text
```

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

The lab instance connects Open WebUI to the server by itself at the end of bootstrap; there is no endpoint or token to copy. The connection is added when `vuln_mcp_table_name` is set and removed when it is not. It needs `open_webui_enable_local_login = true` (Terraform warns otherwise). Because this changes the instance's cloud-init, the apply that introduces it, and later turning the feature on or off, replaces the instance.

Linux (SSM shell on the lab instance):

```bash
grep -F '[register-vuln-mcp]' /var/log/ai-lab-bootstrap.log
sudo ai-lab-register-vuln-mcp --check
sudo ai-lab-register-vuln-mcp
sudo ai-lab-register-vuln-mcp
```

Expected, with the feature on:

- The bootstrap log shows `Open WebUI connected to the MCP server and listed 6 tools: find_hosts_by_vulnerability, get_data_dictionary, get_finding, get_host_findings, list_findings, summarize_findings` and `Connection registered.`
- `--check` ends with `Check only. Connection is already correct (feature true).`
- Each of the two plain runs ends with `Connection already correct; nothing to change.` (repeating adds no second connection).
- The log and the output contain no token or password.

Open WebUI (browser, signed in as the admin): **Admin Panel > Settings > Integrations > External Tool Servers** shows exactly one **Vulnerability Findings** connection (MCP, enabled), and its verify button succeeds.

With the feature off (`vuln_mcp_table_name = null`): `--check` ends with `Connection is already correct (feature false)` and **External Tool Servers** has no Vulnerability Findings entry.

If the bootstrap log has `WARNING: Open WebUI vulnerability MCP registration did not finish`, read the lines above it and use the troubleshooting table in `lambda/vuln_mcp/README.md`, then run `sudo ai-lab-register-vuln-mcp` again. Exit code 3 means local login is off and the connection must be added by hand (also in that README).

### Model-driven acceptance tests

Whether a model actually calls the tools is tested by hand in [docs/vuln-mcp-acceptance-tests.md](../vuln-mcp-acceptance-tests.md). They use a separate table loaded from the repository's test fixture so the answers are exact, and they require proof that the tool ran (chat tool-call entry plus a CloudWatch `tool_call` line), not just a plausible answer. All of them are pending manual execution.
