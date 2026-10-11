# Vulnerability MCP: manual acceptance tests for Open WebUI

**Status: nothing in this document has been run.** Every test below is
**PENDING MANUAL EXECUTION**. The offline automated tests (registration script, MCP server,
this document's expected values) pass in CI; they do not show that a deployed Open WebUI and a
real model can use the tools. These tests do.

Commands are labelled **Local Windows PowerShell** (your PC, repository root) or **Linux (SSM
shell on the lab instance)**. Do not paste one kind into the other.

## What a pass means

A test passes only when all three hold:

1. **Open WebUI executed an MCP tool call.** The chat shows a tool-call entry for the expected tool
   with the expected arguments (see [How to verify the tool was called](#how-to-verify-the-tool-was-called)).
2. **The server logged that call.** CloudWatch has a `tool_call` line for the same tool and
   arguments, written after you asked the question.
3. **The answer matches the tool result.** The model's answer states the values returned by the
   tool, which are the expected values below.

**A plausible answer alone does not pass.** A small model can guess "5 Critical findings" from
nothing, and a guess can even be right. If the tool was not called, mark the test **Fail (no tool
call)**, whatever the answer says.

The expected values are computed from the repository's test fixture (`ITEMS` in
`tests/test_vuln_mcp.py`) by running the real tool code. `tests/test_vuln_acceptance_doc.py`
re-checks them on every CI run, so they cannot drift from the fixture without a failing test.

## Prerequisites

### P1. A deployment with the vulnerability MCP on

`vuln_mcp_table_name` and `vuln_mcp_token_secret_arn` are set, applied, and the lab instance has
finished bootstrapping. Reaching Open WebUI is described in [the deploy runbook](runbook/terraform-smoke-test-plan.md#step-4-check-the-deployment)
(your domain, or the SSM port forward to `http://localhost:8080` in
[SSM-only test](runbook/smoke-tests/access-and-network.md#ssm-only-test)).

> Deploying the change that adds automatic registration edits the instance's cloud-init, so
> Terraform **replaces the instance** on the next apply. The Open WebUI database (accounts, chats)
> lives on the instance's disk and starts empty on the new instance.

### P2. The acceptance dataset (a separate table)

The questions have exact answers only for the fixture data. Your normal table (the `sec-data`
test data) has different counts, so **load the fixture into its own table** and point the MCP
server at it for the duration of the tests. Nothing here writes to your real table.

**Local Windows PowerShell** (these files are in this repository's `docs` folder):

```powershell
$env:AWS_PROFILE = "garrett_gspear"; $env:AWS_DEFAULT_REGION = "us-east-1"
aws dynamodb create-table --cli-input-json file://docs/vuln-acceptance-table.json --query TableDescription.TableStatus --output text
aws dynamodb wait table-exists --table-name aiwebdemo-vuln-acceptance
aws dynamodb batch-write-item --request-items file://docs/vuln-acceptance-fixture.json
aws dynamodb scan --table-name aiwebdemo-vuln-acceptance --select COUNT --query Count --output text
```

The last command must print `12`. Wait until the two indexes are `ACTIVE`:

```powershell
aws dynamodb describe-table --table-name aiwebdemo-vuln-acceptance --query "Table.GlobalSecondaryIndexes[].[IndexName,IndexStatus]" --output text
```

Then set `vuln_mcp_table_name = "aiwebdemo-vuln-acceptance"` in `terraform.tfvars` and apply. The
table name is not part of the instance's cloud-init, so the plan must show **only** the Lambda
function (environment), its role policy and nothing on `aws_instance.ai_lab`. If the plan wants
to replace the instance, stop and check what else changed. Restoring your normal table afterwards
is the same edit in reverse (see [Clean up](#clean-up)).

### P3. A model that can call tools

Open WebUI sends the tool definitions with the chat request ("native" function calling, the
default in the pinned Open WebUI). The lab's default model is **Security Analyst**, created at boot
on the lab's base model (`qwen3:14b` in the example) with these tools attached, Open WebUI's built-in
tools off, and a system prompt that tells it to use the tools. Smaller base models may not call
tools reliably. A test that fails because the model never asked for the tool is still a
**Fail (no tool call)**; record the model name and try a larger one before concluding the
integration is broken. The setup checks below separate integration faults from model faults.

### P4. Evidence you will collect (set up once per PowerShell window)

**Local Windows PowerShell:**

```powershell
$env:AWS_PROFILE = "garrett_gspear"; $env:AWS_DEFAULT_REGION = "us-east-1"
$logGroup   = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_log_group")
$instanceId = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "instance_id")
$logGroup; $instanceId
```

Both must print a value. If `vuln_mcp_log_group` prints nothing, the vulnerability MCP is not
deployed and these tests do not apply.

## How to verify the tool was called

Use at least the first two for every question.

1. **In the chat (Open WebUI).** Under the model's reply, expand the tool-call entry (a collapsed
   block labelled with the tool, such as `vuln-findings_summarize_findings`; the label wording
   varies by Open WebUI version). It shows the arguments sent and the JSON the server returned.
   Compare both with the question's "Expected tool call".
2. **On the server (CloudWatch).** Note the time just before you ask, then list what the server
   logged after it. This is independent of the model and of what Open WebUI displays.

   **Local Windows PowerShell:**

   ```powershell
   $start = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()   # run this, then ask the question in Open WebUI
   # ...after the answer appears (allow ~10 seconds for log delivery):
   $events = aws logs filter-log-events --log-group-name $logGroup --start-time $start --filter-pattern tool_call --output json | ConvertFrom-Json
   $events.events | ForEach-Object { $_.message | ConvertFrom-Json } |
     Select-Object tool, ok, ms, @{ n = "arguments"; e = { $_.arguments | ConvertTo-Json -Compress } } | Format-Table -AutoSize -Wrap
   ```

   You want a row whose `tool` and `arguments` match the question and `ok` is `True` (a row with
   `ok` `False` is a call the server rejected; the answer to a negative question is expected to be
   one). **No row means no call reached the server**, even if the chat displayed an answer. Logs
   never contain the token or the findings themselves, only the tool name, arguments, size and time.
3. **Optional, on the instance (Open WebUI side).** If a call shows in the chat but not in
   CloudWatch, see what Open WebUI logged.

   **Linux (SSM shell on the lab instance):**

   ```bash
   sudo docker logs --since 10m open-webui 2>&1 | grep -i -E "mcp|tool" | tail -30
   ```

**Argument tolerance.** Models phrase arguments differently. Names for the same thing are fine
("Log4Shell" or `CVE-2021-44228`; a host as `prod-app-01` or its full name; omitted defaults).
A different tool, a different `status` or `severity` than the one the question asks for, or an
answer that does not match the returned data, is a failure.

## Two kinds of failure

Most failures are one of two kinds, and each looks different. Name the kind in the Results notes.

| | Registration failure | Function failure |
|---|---|---|
| What it means | The lab did not put the connection into Open WebUI at boot. | The connection exists (registered or added by hand), but asking a question does not produce a correct tool call. |
| What you see | **No** Vulnerability Findings entry in **Admin Panel > Settings > Integrations > External Tool Servers**, **and** none in the chat's **Integrations** menu (the icon next to **+** in the message box). Nothing is hidden: if it is missing from both, it was never registered, and no tool call can happen. | The entry is there and switched on in the chat, but one of: **Verify** fails; the chat shows a tool-call entry with an error; the model answers without any tool-call entry; or CloudWatch has no `tool_call` row, or a row with `ok` `False` that the question did not expect. |
| Where to look | S1 output and the boot log: `grep -F '[register-vuln-mcp]' /var/log/ai-lab-bootstrap.log`. Seen so far: `Could not read the MCP token secret` (the instance role cannot read the token; check that `vuln_mcp_token_secret_arn` is the full ARN) and `Local login is off`. | The tool-call entry in the chat, the CloudWatch rows ([How to verify the tool was called](#how-to-verify-the-tool-was-called)) and the Open WebUI log on the instance. |
| How to tell the cause | Read the S1 line. A secret, sign-in or login message is the registration itself: fix it, and meanwhile test the server with a connection added by hand (S3). `Open WebUI could not connect to the MCP server` means the server is the cause: the registration never saves a connection it cannot verify, so treat it as a function failure and skip S3, which would fail the same way. | No `tool_call` row and an error in the chat: the connection (address or token) or the server. A row with `ok` `True` but a wrong answer: the model. No tool-call entry at all: the model did not call the tool; try a larger model before blaming the server (P3). |

A connection added by hand separates the two: if the questions pass with it, the server and the
model work and only the registration is broken.

## Part 1: setup checks

Run these before the questions. Only a **missing** connection is a registration failure, and only
then add it by hand (S3) so the questions still test the server and the model. A connection that
exists but fails Verify, or an S1 message that it `could not connect to the MCP server`, is a
function failure: record it as one and fix the address, the token or the server instead.

### S1. The connection exists (no model involved)

PENDING MANUAL EXECUTION

**Linux (SSM shell on the lab instance):**

```bash
sudo ai-lab-register-vuln-mcp --check
```

Expected: `Open WebUI connected to the MCP server and listed 6 tools` followed by the tool names
(`find_hosts_by_vulnerability`, `get_data_dictionary`, `get_finding`, `get_host_findings`,
`list_findings`, `summarize_findings`), then `Connection is already correct` (or a note that it
would change, which means bootstrap did not finish registering; run the command without
`--check` and read its output). **Pass:** six tools discovered and the connection reported correct.
**Fail:** any error, fewer tools, or `local login is disabled` (see the README: register by hand).

<!-- setup-check -->

### S2. The connection is visible in Open WebUI

PENDING MANUAL EXECUTION

Sign in to Open WebUI as the admin and open **Admin Panel > Settings > Integrations > External Tool Servers**.
**Pass:** exactly one connection named **Vulnerability Findings**, type MCP, enabled, and its
verify (plug) button reports success. Then sign in as a non-admin demo user, start a chat and open
the **Integrations** menu (the icon next to **+** in the message box): **Pass** if **Vulnerability
Findings** is listed there. **Fail** if there are two entries or it shows only for admins.

**Registration failure:** no entry on the settings page and none in the chat's Integrations menu
(only built-in tools such as Code Interpreter). Record S1 and S2 as Fail with the boot log line
from S1, then continue with S3, unless that line says it could not connect to the MCP server (see
[Two kinds of failure](#two-kinds-of-failure)).

**Function failure:** the entry is there but its Verify fails. Record S2 as Fail with the kind
`function`, skip S3, and check the address, the token and the Lambda's log group.

<!-- setup-check -->

### S3. Add the connection by hand (only after a registration failure)

PENDING MANUAL EXECUTION

Only when S2 found **no** entry and the S1 line is not `could not connect to the MCP server`.
It tests the server and the model without the lab's registration, so a registration failure does
not hide whether the tool itself works.

1. Print the address, and copy the token to the clipboard without showing it (**Local Windows
   PowerShell**, repository root):

   ```powershell
   Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_url")
   $tokenArn = (Select-String -Path .\terraform.tfvars -Pattern '^\s*vuln_mcp_token_secret_arn\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
   aws secretsmanager get-secret-value --secret-id $tokenArn --query SecretString --output text | Set-Clipboard
   ```

   If the secret is a key/value secret, paste only the token value, not the JSON.
2. As the admin, open **Admin Panel > Settings > Integrations > External Tool Servers** and click **+**.
3. Type **MCP (Streamable HTTP)**; URL: the address from step 1; authentication **Bearer**; key:
   paste the token. Name it **Vulnerability Findings**.
4. Set access so all users can use it, click **Verify**, then **Save**.

**Pass:** Verify succeeds and lists the six tools, and the entry is in a demo user's chat
**Integrations** menu. **Fail:** Verify fails. That is a function failure in the connection or the
server, not the registration: check the address, the token and the Lambda's log group.

Run the questions with this connection and write `by hand` in the Results table's Connection
column. Those results count for the server and the model, never for S1 and S2. When the
registration is fixed, delete this entry and run `sudo ai-lab-register-vuln-mcp` on the instance;
the managed entry replaces any MCP entry with the same address (A2).

<!-- setup-check -->

### S4. The tool is switched on for the chat

PENDING MANUAL EXECUTION

Start a **new** chat on **Security Analyst** (the default model) and open the **Integrations** menu
in the message box (the icon next to **+**). **Pass:** **Vulnerability Findings** is already switched
on without you touching it. **Fail:** it is listed but off, which means the model was not created or
lost its tools; check the `[register-vuln-mcp]` lines in the bootstrap log. A tool that is
registered but not switched on in the chat is never offered to the model, which looks like "the
model ignores the tool". If you test another model, switch the tool on by hand. Use this chat (or
new ones set the same way) for every question.

<!-- setup-check -->

## Part 2: questions

Start each question in a **new chat** with the tool switched on, and note the time first (step 2
above). The "Expected answer" gives the facts the reply must state. Host names may appear short
(`prod-app-01`) or with the `.corp.example.local` suffix.

The `<!-- acceptance: ... -->` line under each heading is machine-readable; CI runs it against the
fixture. Ignore it when testing by hand.

### Q1. Count of open Critical findings

PENDING MANUAL EXECUTION

**Ask:** "How many open Critical findings are in the vulnerability table?"

<!-- acceptance: {"id": "Q1", "tool": "summarize_findings", "arguments": {"group_by": "severity", "status": "open"}, "expect": [["groups.0.value", "Critical"], ["groups.0.count", 5], ["total_findings", 8]]} -->

- **Dataset:** acceptance table (12 findings: 8 open, 2 reopened, 2 fixed).
- **Expected tool call:** `summarize_findings` with `group_by` = `severity` and `status` = `open`
  (the model may add `min_severity` = `Critical`; then only the Critical group comes back).
- **Expected answer:** **5** open Critical findings.
- **Pass:** chat tool-call entry and a CloudWatch `tool_call` line for `summarize_findings`, `ok`
  true; the answer says 5.
- **Fail:** no tool call; a different tool that cannot count (`list_findings` with a hand count is
  tolerated only if the count is right and the call is logged); the answer is not 5. A common
  wrong answer is 9, the count in the standard `sec-data` table: that means the Lambda is still
  reading the wrong table (P2).

### Q2. Hosts with Log4Shell

PENDING MANUAL EXECUTION

**Ask:** "Which hosts are affected by Log4Shell?"

<!-- acceptance: {"id": "Q2", "tool": "find_hosts_by_vulnerability", "arguments": {"vulnerability_id": "Log4Shell"}, "expect": [["vulnerability_id", "CVE-2021-44228"], ["total_matching", 2], ["hosts.0.host", "prod-app-01"], ["hosts.1.host", "prod-app-02"]]} -->

- **Expected tool call:** `find_hosts_by_vulnerability` with `vulnerability_id` = `Log4Shell` or
  `CVE-2021-44228`.
- **Expected answer:** two hosts, **prod-app-01** and **prod-app-02**, both Critical, CVSS 10.0.
  `prod-app-03` also had it but it is fixed, so it is not an affected host.
- **Pass:** tool call logged; the answer names exactly those two hosts.
- **Fail:** no tool call; a third host listed (for example `prod-app-03` as affected); a host that
  does not exist in the table.

### Q3. Active findings on one host

PENDING MANUAL EXECUTION

**Ask:** "What vulnerabilities are currently open on prod-app-01?"

<!-- acceptance: {"id": "Q3", "tool": "get_host_findings", "arguments": {"host": "prod-app-01"}, "expect": [["host", "prod-app-01.corp.example.local"], ["total_matching", 5], ["findings.0.severity", "Critical"]]} -->

- **Expected tool call:** `get_host_findings` with `host` = `prod-app-01` (default status is open
  plus reopened).
- **Expected answer:** **5** findings: Log4Shell (CVE-2021-44228, Critical), Spring4Shell
  (CVE-2022-22965, Critical), Heartbleed (CVE-2014-0160, High, reopened), Terrapin (CVE-2023-48795,
  Medium) and TLS Version 1.0 Protocol Detection (PLUGIN-104743, Info). The fixed Sweet32 finding
  (CVE-2016-2183) is **not** current and must not be listed as open.
- **Pass:** tool call logged; the answer lists those five and not Sweet32 as open.
- **Fail:** no tool call; Sweet32 listed as open; findings from another host.

### Q4. Critical findings with a public exploit, newest first

PENDING MANUAL EXECUTION

**Ask:** "Which Critical findings have a public exploit available? Show the newest first."

<!-- acceptance: {"id": "Q4", "tool": "list_findings", "arguments": {"severity": "Critical", "exploit_available": true}, "expect": [["total_matching", 5], ["findings.0.host", "prod-app-01"], ["findings.0.vulnerability_id", "CVE-2022-22965"], ["findings.1.host", "dc-01"], ["findings.4.host", "prod-app-01"], ["findings.4.vulnerability_id", "CVE-2021-44228"]]} -->

- **Expected tool call:** `list_findings` with `severity` = `Critical` and `exploit_available` = true.
- **Expected answer:** **5** findings, newest `last_seen` first: prod-app-01 Spring4Shell
  (2026-10-02), dc-01 Zerologon (2026-10-01 02:20), prod-app-02 Log4Shell (2026-10-01 02:10),
  dev-app-01 Spring4Shell (2026-09-30), prod-app-01 Log4Shell (2026-09-17).
- **Pass:** tool call logged; the answer lists the five in that order (the order is the point of
  "newest first").
- **Fail:** no tool call; fixed findings included (`prod-app-03`); a different count or order.

### Q5. Distinct hosts with serious, exploitable, open findings

PENDING MANUAL EXECUTION

**Ask:** "How many different hosts have an open High or Critical finding with a known exploit?"

<!-- acceptance: {"id": "Q5", "tool": "summarize_findings", "arguments": {"group_by": "host", "status": "open", "min_severity": "High", "exploit_available": true}, "expect": [["group_count", 4], ["total_findings", 5], ["groups.0.value", "prod-app-01"], ["groups.0.count", 2]]} -->

- **Expected tool call:** `summarize_findings` with `group_by` = `host`, `status` = `open`,
  `min_severity` = `High`, `exploit_available` = true.
- **Expected answer:** **4** hosts (prod-app-01, prod-app-02, dc-01, dev-app-01), from 5 findings;
  prod-app-01 has 2 of them.
- **Pass:** tool call logged with those filters; the answer says 4 hosts (5 findings is fine as
  extra detail).
- **Fail:** no tool call; an answer of 5 hosts, which comes from also counting reopened findings
  (`status` left at its default). Record which filters the model chose: a wrong filter is a
  model fault, not an integration fault.

### Q6. Details of one finding

PENDING MANUAL EXECUTION

**Ask:** "What is the CVSS vector and EPSS score of Log4Shell on prod-app-01?"

<!-- acceptance: {"id": "Q6", "tool": "get_finding", "arguments": {"host": "prod-app-01", "vulnerability_id": "Log4Shell"}, "expect": [["count", 1], ["findings.0.cvss_vector", "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"], ["findings.0.epss_score", 0.5], ["findings.0.network_port", 8080]]} -->

- **Expected tool call:** `get_finding` with `host` = `prod-app-01`, `vulnerability_id` =
  `Log4Shell` or `CVE-2021-44228`.
- **Expected answer:** vector `CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H`, EPSS **0.5**
  (port 8080).
- **Pass:** tool call logged; the answer quotes the exact vector string and 0.5.
- **Fail:** no tool call; a vector that differs by even one character (models often invent
  plausible vectors when they do not call the tool: this question catches that).

### Q7. What data exists

PENDING MANUAL EXECUTION

**Ask:** "Which hosts do you have vulnerability data for?"

<!-- acceptance: {"id": "Q7", "tool": "get_data_dictionary", "arguments": {}, "expect": [["hosts", ["dc-01", "dev-app-01", "prod-app-01", "prod-app-02", "prod-app-03", "prod-web-02", "stage-web-01"]]]} -->

- **Expected tool call:** `get_data_dictionary` (no arguments).
- **Expected answer:** **7** hosts: dc-01, dev-app-01, prod-app-01, prod-app-02, prod-app-03,
  prod-web-02, stage-web-01.
- **Pass:** tool call logged; the answer lists exactly those seven.
- **Fail:** no tool call; a host not in the list; a missing host.

### Q8. A host that does not exist

PENDING MANUAL EXECUTION

**Ask:** "List the vulnerabilities on prod-app-99."

<!-- acceptance: {"id": "Q8", "tool": "get_host_findings", "arguments": {"host": "prod-app-99"}, "error_contains": "Unknown host"} -->

- **Expected tool call:** `get_host_findings` with `host` = `prod-app-99`. The server answers with
  an error: `Unknown host 'prod-app-99'. Did you mean: prod-app-01, prod-app-02, prod-app-03?`
  (CloudWatch shows `ok` false for this call; that is correct here).
- **Expected answer:** the model says there is no such host, and may suggest `prod-app-01`,
  `prod-app-02` or `prod-app-03`. It must not list vulnerabilities for `prod-app-99`.
- **Pass:** tool call logged with an error result; the answer reports the unknown host honestly.
- **Fail:** no tool call; invented findings for `prod-app-99`.

### B1. A request the tools cannot do

PENDING MANUAL EXECUTION

**Ask:** "Mark the Log4Shell finding on prod-app-01 as fixed."

- **Expected tool call:** none that changes anything. The server is read-only and has no write
  tool. The model may look the finding up (`get_finding`), which is fine.
- **Expected answer:** the model says it cannot change findings.
- **Pass:** the answer does not claim the status was changed, and re-asking Q3 still lists
  Log4Shell as open on prod-app-01.
- **Fail:** the model says it marked the finding fixed.

## Part 3: authentication and repeat deployment (not model-driven)

These confirm what the questions assume. Both are **PENDING MANUAL EXECUTION**.

### A1. The server refuses a wrong token

PENDING MANUAL EXECUTION

**Local Windows PowerShell:**

```powershell
$url = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_url")
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
try { Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer not-the-token" } -Body $body }
catch { $_.Exception.Response.StatusCode.value__ }
```

**Pass:** prints `401`. **Fail:** any tool list is returned.

### A2. A second run changes nothing, and turning the feature off removes the connection

PENDING MANUAL EXECUTION

**Linux (SSM shell on the lab instance):**

```bash
sudo ai-lab-register-vuln-mcp
sudo ai-lab-register-vuln-mcp
```

**Pass:** both runs end with `Connection already correct; nothing to change.`, and
**External Tool Servers** still shows one connection. If a connection was added by hand (S3) with
the same address, the first run replaces it (`Connection registered.`) and the second reports no
change; there is still exactly one. Removal is exercised by deploying with `vuln_mcp_table_name = null`
(this replaces the instance, because the instance's configuration file changes): after
bootstrap, **External Tool Servers** shows **no** Vulnerability Findings connection, and
`sudo ai-lab-register-vuln-mcp --check` reports the connection is already correct (absent).

## Results

Fill in after running. Leave a cell as `Pending` until it has been run. Record the model name and
the date. **Connection** is `registered` or `by hand` (S3). For a failure, start the notes with its
kind, `registration` or `function` (see [Two kinds of failure](#two-kinds-of-failure)).

| Test | Result | Model / date | Tool call seen in chat | `tool_call` in CloudWatch | Notes | Connection |
|---|---|---|---|---|---|---|
| S1 | Pending | | n/a | n/a | | n/a |
| S2 | Pending | | n/a | n/a | | n/a |
| S3 | Pending | | n/a | n/a | | by hand |
| S4 | Pending | | n/a | n/a | | |
| Q1 | Pending | | | | | |
| Q2 | Pending | | | | | |
| Q3 | Pending | | | | | |
| Q4 | Pending | | | | | |
| Q5 | Pending | | | | | |
| Q6 | Pending | | | | | |
| Q7 | Pending | | | | | |
| Q8 | Pending | | | | | |
| B1 | Pending | | | | | |
| A1 | Pending | | n/a | n/a | | n/a |
| A2 | Pending | | n/a | n/a | | n/a |

## Clean up

1. Set `vuln_mcp_table_name` back to your normal table in `terraform.tfvars` and apply (Lambda
   environment only; the plan must not touch `aws_instance.ai_lab`).
2. Delete the acceptance table when you are finished (**Local Windows PowerShell**):

   ```powershell
   aws dynamodb delete-table --table-name aiwebdemo-vuln-acceptance --query TableDescription.TableStatus --output text
   ```

## Updating this document

If the fixture changes, regenerate the load files and re-check the expected values:

```powershell
python tests/test_vuln_acceptance_doc.py --write
python -m unittest tests.test_vuln_acceptance_doc -v
```
