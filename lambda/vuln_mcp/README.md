# Vulnerability findings MCP server

A read-only [Model Context Protocol](https://modelcontextprotocol.io) server that lets Open WebUI
(and its Ollama model) ask questions about the vulnerability findings in the DynamoDB table
`aiwebdemo-vuln-findings`. The table and its test data are created by the separate `sec-data`
repository (`create_vuln_table.py`). This repository only reads the table.

It runs as an AWS Lambda behind a Function URL, and locally on your computer for development.

## Tools

Each tool maps to one way of reading the table. Every list result reports `total_matching`,
`returned` and `truncated`, so the model can say "showing 20 of 63". By default only open and
reopened findings are included; pass `status` to change that.

| Tool | Answers | How it reads the table |
|---|---|---|
| `get_data_dictionary` | What fields, severities and statuses exist, and which hosts and vulnerabilities are in the data. Models call it first when unsure. | Cached projected scan |
| `get_host_findings` | "What is wrong with prod-app-01?" Worst first. | Query on the table key |
| `find_hosts_by_vulnerability` | "Which hosts have Log4Shell?" Accepts a CVE id or a name. | Query on `vuln-host-index` |
| `list_findings` | "Open Critical findings with a public exploit, newest first." Filters: `severity` or `min_severity`, `status`, `exploit_available`, `since`, `environment`. | Query on `severity-lastseen-index` |
| `get_finding` | Everything about one vulnerability on one host: description, CVSS vector, CWE, EPSS, references, scan details. | Query on the table key with a sort-key prefix |
| `summarize_findings` | "How many ... ?" Groups by `severity`, `status`, `environment`, `host`, `vulnerability` or `exploit_available`. `group_count` is the number of distinct groups, so grouping by host gives the number of hosts affected. | Scan, counted in code |

Host arguments accept the short name (`prod-app-01`). Vulnerability arguments accept a CVE id or part
of the name (`Log4Shell`). A wrong value returns a message that suggests the close matches.

`summarize_findings` and the dictionary read the whole table. That is right for a lab-sized table
(tens or hundreds of findings). A much larger table would need precomputed rollups. A read that would
exceed 50 pages fails with a message instead of returning a partial count.

## Files

| File | Role |
|---|---|
| `vuln_tools.py` | Tool definitions and logic. No MCP or HTTP code. The only code that talks to DynamoDB. |
| `mcp_protocol.py` | Minimal stateless MCP over HTTP (`initialize`, `ping`, `tools/list`, `tools/call`). No dependencies. |
| `mcp_handler.py` | Lambda Function URL entry point: bearer-token check, routing, request limits. |
| `local_server.py` | Runs the same handler on `http://127.0.0.1:8765/mcp` for development. |
| `../../tests/test_vuln_mcp.py` | Offline tests of the server. |
| `../../scripts/ai-lab-register-vuln-mcp.sh` | Runs on the lab instance. Connects Open WebUI to this server (see below). |
| `../../tests/test_vuln_mcp_registration.py` | Offline tests of that script, including a real MCP client against this handler. |
| `../../docs/vuln-mcp-acceptance-tests.md` | Manual tests to run in Open WebUI after deployment. |

The protocol layer is hand-written so the Lambda is a plain zip of these files, packaged by Terraform's
`archive_file` like `auto_stop_watchdog.py`, with no build step and nothing to install beyond the `boto3`
that Lambda already provides. Because the tools are separate from the protocol, it can be swapped for the
official MCP SDK later without changing them.

## Run the tests

From the repository root:

```powershell
python -m unittest discover -s tests
```

The registration tests run the instance script with `bash`, `curl` and `jq` (present on Linux, CI and the
lab instance; skipped on plain Windows). Two of them use the real MCP SDK that Open WebUI bundles, and skip
unless it is installed:

```powershell
python -m pip install mcp==1.27.2
```

Seven extra tests check the real DynamoDB queries, filters and indexes against a mock table. They run
only if moto is installed, and are skipped otherwise (CI does not need it):

```powershell
python -m pip install "moto[dynamodb]"
```

## Run it locally against the real table

Point at the account and region that hold the table, then start the server:

```powershell
$env:AWS_PROFILE = "garrett_gspear"
$env:AWS_DEFAULT_REGION = "us-east-1"
python .\lambda\vuln_mcp\local_server.py
```

Leave that window running. Every tool call is logged there as one JSON line. No token is needed locally
unless you set `$env:AUTH_TOKEN`. Stop it with Ctrl+C.

### Try it with the MCP Inspector

In a second window, from any directory (requires Node.js):

```powershell
npx @modelcontextprotocol/inspector
```

Choose transport **Streamable HTTP**, URL `http://127.0.0.1:8765/mcp`, click **Connect**, then open the
**Tools** tab.

### Or call it directly

```powershell
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"summarize_findings","arguments":{"group_by":"severity","status":"open"}}}'
Invoke-RestMethod -Uri http://127.0.0.1:8765/mcp -Method Post -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 10
```

Opening the address in a browser shows `{"error": "Use POST"}`. That is expected: browsers send GET and
the server only accepts POST.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `TABLE_NAME` | `aiwebdemo-vuln-findings` | DynamoDB table to read. |
| `AUTH_TOKEN` | none | Bearer token callers must send. For local use. |
| `AUTH_TOKEN_SECRET_ARN` | none | Secrets Manager secret holding the token. Used in AWS instead of `AUTH_TOKEN`. |
| `REQUIRE_AUTH` | off | Set to `1` to demand a token even outside Lambda. |

Inside Lambda the server fails closed: with no token configured, every request is refused.

## IAM the Lambda needs

Read-only access to this one table and its indexes:

```json
{
  "Effect": "Allow",
  "Action": ["dynamodb:Query", "dynamodb:Scan"],
  "Resource": [
    "arn:aws:dynamodb:us-east-1:<account-id>:table/aiwebdemo-vuln-findings",
    "arn:aws:dynamodb:us-east-1:<account-id>:table/aiwebdemo-vuln-findings/index/*"
  ]
}
```

plus read access to the token secret, and the standard Lambda logging permissions.

## Deploy with Terraform

`vuln_mcp.tf` in the repository root builds the Lambda, its read-only role, a log group and the
Function URL. It is off until `vuln_mcp_table_name` is set. The token secret is created by hand so the
token never appears in Terraform state:

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token = [Convert]::ToBase64String($bytes)
aws secretsmanager create-secret --name vuln-mcp-token-aiwebdemo --secret-string $token --profile garrett_gspear --region us-east-1 --query ARN --output text
```

Put the table name and the printed ARN in `terraform.tfvars` (`vuln_mcp_table_name`,
`vuln_mcp_token_secret_arn`), then plan and apply. The lab instance connects Open WebUI to the server
itself at boot (next section); nothing needs copying by hand.

## Connecting Open WebUI (automatic)

Nothing to configure. When `vuln_mcp_table_name` is set, the lab instance adds the server to Open WebUI as
one **MCP** tool connection named **Vulnerability Findings** (id `vuln-findings`, bearer auth, usable by every
signed-in user). When it is not set, the connection is absent.

**Compatibility.** The pinned Open WebUI image (`open-webui:v0.11.4`) bundles the MCP Python SDK 1.27.2 and
speaks MCP over Streamable HTTP, which is what this server speaks (JSON responses, no sessions, no stream).
No adapter is needed. This was checked offline by pointing that SDK's client at this handler: initialize,
the six tools, a tool call, and a `401` for a wrong token (`tests/test_vuln_mcp_registration.py`). Whether the
deployed Open WebUI and a model can use the tools is still to be tested by hand:
[`docs/vuln-mcp-acceptance-tests.md`](../../docs/vuln-mcp-acceptance-tests.md).

**How it works.** `cloud-init` installs `/usr/local/sbin/ai-lab-register-vuln-mcp` and a settings file
`/etc/ai-lab/vuln-mcp.env`, then runs the script once Open WebUI is up. The settings file holds the endpoint,
ARNs, names and flags; the Terraform outputs and the file never contain the token. The script:

1. waits for Open WebUI, then signs in as the admin (password read from Secrets Manager);
2. reads the token from the token secret and asks Open WebUI to connect and list the tools (the same check as
   the **Verify** button), retrying while the server or Open WebUI is not ready;
3. adds or updates the one managed connection through Open WebUI's admin API, or removes it when the feature is
   off. It writes only when something differs, replaces any hand-made connection to the same URL instead of
   duplicating it, and passes every other connection and setting through untouched.

A failure never fails the instance's bootstrap; it is logged, and you rerun the script (below).

**Secrets.** The token is read at run time with the instance role, held in the script's memory and a private
temporary directory, never printed, never on a command line, and never in Terraform state, outputs or user-data.
It does end up in Open WebUI's own database (that is how Open WebUI stores a bearer token for a connection, not
encrypted at rest), readable by Open WebUI admins and anyone who can read the instance's disk. It only grants
read access to the findings. The instance role gains read access to the token secret only when the feature is on.

**Local commands (Windows PowerShell, repository root):**

```powershell
$env:AWS_PROFILE = "garrett_gspear"; $env:AWS_DEFAULT_REGION = "us-east-1"
$url        = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_url")
$logGroup   = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_log_group")
$instanceId = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "instance_id")
aws ssm start-session --target $instanceId   # opens the Linux shell below
```

**Linux commands (SSM shell on the lab instance):**

```bash
sudo ai-lab-register-vuln-mcp --check      # report only: tools discovered, connection correct or not
sudo ai-lab-register-vuln-mcp              # make Open WebUI match; safe to repeat
grep -F '[register-vuln-mcp]' /var/log/ai-lab-bootstrap.log   # what happened at boot
cat /etc/ai-lab/vuln-mcp.env               # the settings it used (no secrets)
```

`ai-lab-status` also prints the check.

**Expected output** of `--check` on a healthy lab:

```text
[register-vuln-mcp] Open WebUI connected to the MCP server and listed 6 tools: find_hosts_by_vulnerability, get_data_dictionary, get_finding, get_host_findings, list_findings, summarize_findings
[register-vuln-mcp] Check only. Connection is already correct (feature true).
```

**Rotating the token.** Put the new value in the secret, then run `sudo ai-lab-register-vuln-mcp` on the
instance. The Lambda caches the token while a container is warm; change any function setting to recycle it if
calls fail with `401` for a while.

**Local login must be on.** The admin API needs password sign-in. With `open_webui_enable_local_login = false`
the script exits with code 3 (`Local login is off ...`), Terraform shows a warning, and the connection is not
changed. Add it by hand instead: **Admin Panel > Settings > Integrations > External Tool Servers > +**, type **MCP (Streamable HTTP)**,
URL = the `vuln_mcp_url` output, auth **Bearer** with the token, then **Verify** and **Save**. To copy the token
without displaying it (**Local Windows PowerShell**):
`aws secretsmanager get-secret-value --secret-id <vuln_mcp_token_secret_arn> --query SecretString --output text | Set-Clipboard`.

**Turning local login off later.** Two separate Open WebUI settings are involved (read from the Open WebUI
v0.11.4 source, not confirmed on a running instance):

| Setting | Where it can be changed | Effect on this script |
|---|---|---|
| Login form (`ENABLE_LOGIN_FORM`) | A saved setting. An admin can toggle it in **Admin Settings > General** (the authentication section), and it survives restarts. The container variable only gives the starting value. | None. It only hides the email/password form on the login page. The sign-in API still works, so the registration script keeps working and an existing connection stays. |
| Password sign-in (`ENABLE_PASSWORD_AUTH`) | Container environment variable only; there is no setting for it in the UI. In this lab that means `open_webui_enable_local_login = false`, which needs `enable_cognito = true` and replaces the instance. | The sign-in API refuses passwords, so the script exits 3 and cannot register or remove the connection. |

So hiding the form in the UI is a soft change that is easy to undo; it steers people to Cognito but does not
disable password sign-in through the API. A full lockdown needs the Terraform setting and a new instance, and
after it the connection is managed by hand (above). If you plan that, do it when the instance is being
replaced anyway.

**Changing this replaces the instance.** The instance's cloud-init now carries the registration step and the
feature's on/off setting, so the first apply of this change, and later turning the feature on or off,
replaces the instance (the Open WebUI data on its disk starts fresh). Changing only the table name or token
value does not.

**Troubleshooting**

| Symptom | Likely cause and fix |
|---|---|
| `Waiting for Open WebUI...` repeats, then `Gave up` | Open WebUI is slow or down. `sudo docker ps`, `sudo docker logs --tail 100 open-webui`; rerun the script when healthy. |
| `Open WebUI could not connect to the MCP server and list its tools` | The Function URL is unreachable from the instance (egress is 443/80 only) or the token differs from the secret's. From Windows run the `401` check in `terraform-smoke-test-plan.md`; check the Lambda log group. |
| `Open WebUI rejected the admin sign-in` | The admin secret no longer matches the admin password. Fix the secret or the account; the script does not retry this. |
| `Local login is off` / exit 3 | See "Local login must be on" and "Turning local login off later" above. |
| `Could not read ... secret` | The instance role cannot read it: confirm `vuln_mcp_token_secret_arn` was applied (the role is updated only when the feature is on). |
| Connection exists but a model never calls the tools | The tool must be switched on in the chat, and a small model may not call tools. See the acceptance tests. |
| Two Vulnerability entries | One was added by hand with a different URL. Delete it; the managed one has id `vuln-findings`. |

## Not done yet

- Running the acceptance tests against a deployment (they are written and pending manual execution).
- Model comparison (see GitHub issue #41).
- Per-user authorization.
