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
| `../../tests/test_vuln_mcp.py` | Offline tests. |

The protocol layer is hand-written so the Lambda is a plain zip of these files, packaged by Terraform's
`archive_file` like `auto_stop_watchdog.py`, with no build step and nothing to install beyond the `boto3`
that Lambda already provides. Because the tools are separate from the protocol, it can be swapped for the
official MCP SDK later without changing them.

## Run the tests

From the repository root:

```powershell
python -m unittest discover -s tests
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
`vuln_mcp_token_secret_arn`), then plan and apply. The `vuln_mcp_url` output is the address to give
Open WebUI; the token is the bearer token.

## Not done yet

- Connecting Open WebUI, and the evaluation questions with known answers.
- Model comparison (see GitHub issue #41).
- Per-user authorization.
