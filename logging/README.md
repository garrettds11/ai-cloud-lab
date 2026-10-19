# Logging: VictoriaLogs log store

Test docs: [Deploy and smoke test](../docs/runbook/terraform-smoke-test-plan.md) · [Access and network](../docs/runbook/smoke-tests/access-and-network.md) · [Control panel](../docs/runbook/smoke-tests/control-panel.md) · [Lab features](../docs/runbook/smoke-tests/lab-features.md) · [Vulnerability MCP](../docs/vuln-mcp-acceptance-tests.md) · [Scanner](../scanner/README.md) · **Logging** · [Logging end-to-end](END-TO-END-TEST.md)

This folder adds a log store to the lab so MCP tools in Open WebUI have real logs to query. The current track is VictoriaLogs. Other logging tools each have an issue ([#94](https://github.com/garrettds11/ai-cloud-lab/issues/94) to [#101](https://github.com/garrettds11/ai-cloud-lab/issues/101)) and come back one at a time as their own tracks; Splunk is planned as the final one.

Logging is an optional add-on. The deploy order is: control panel API, lab, scanner (optional), logging (optional). Destroy the add-ons before the lab.

Each folder under this directory is a separate Terraform root and state. It does not read or own the lab's state. Instead the lab publishes its instance ID, security group, IAM role name and Open WebUI container name in the SSM parameter `/<project_name>/lab/settings` (`addon_settings.tf` at the repository root), and the track reads it using `lab_project_name`. Nothing is copied by hand.

A track apply creates a backend instance, a private raw-event S3 archive and an SSM Distributor package. When the lab exists, it also installs Vector on the lab host with an SSM State Manager association, gives the lab's role write access to the archive, and allows the lab host to reach the backend's intake. When no lab is found, plan shows a warning and only the backend side is built; apply the track again after the lab is deployed. Apply it again after a lab rebuild too, because the rebuilt lab has new IDs.

Vector collects the host journal and the Open WebUI container logs. It writes every event to the S3 archive and forwards the same events through the backend's private HTTPS intake to VictoriaLogs. The VictoriaLogs API and its built-in web UI (vmui) are reachable only through an SSM port-forwarding session.

The track also installs the **Lab Logs** MCP server on the lab host (a second Distributor package, `mcp`) and registers it in Open WebUI, so chat models can search these logs. See [Lab Logs in Open WebUI](#lab-logs-in-open-webui).

## One-time preparation

1. Deploy the lab (Step 3 of [the Terraform smoke test plan](../docs/runbook/terraform-smoke-test-plan.md)) and make sure its Ubuntu host is online in Systems Manager. You can also deploy the track first; see above.
2. Open a PowerShell window prepared as in Step 1 of the smoke test plan: `AWS_PROFILE` and `AWS_DEFAULT_REGION` set to the lab account, with the account check passing. The logging track uses only the AWS provider, so it runs plain `terraform`; the Cloudflare token wrapper is needed only for the repository-root lab.
3. Check `terraform.tfvars.example` in the track folder. `lab_project_name` must equal the lab's `project_name` (`aiwebdemo` in this repository). Set `aws_profile` there only if you use a named profile that is not already in `AWS_PROFILE`.

## Apply

```powershell
Set-Location C:\GitHub\ai-cloud-lab\logging\victorialogs
Copy-Item terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check
terraform validate
terraform plan '-out=track.tfplan'
terraform show -no-color track.tfplan | Out-File track-plan.txt
# Stop here and review track-plan.txt. Continue only after review.
terraform apply track.tfplan
terraform output lab_found
```

Keep the quotes around `'-out=track.tfplan'`: Windows PowerShell otherwise splits the argument at the dot, and the plan fails with "Too many command line arguments".

`lab_found` is `true` when the collector was installed on the lab. If it is `false`, deploy the lab and run the plan and apply again.

Wait for both `AWS-ConfigureAWSPackage` associations (`vector` and `mcp`) to report success in Systems Manager State Manager.

The associations run again by themselves when their settings change, for example when an apply replaces the backend instance and it gets a new private IP.

## Open vmui

The tunnel holds its window until you stop it, so open a **new** PowerShell window for it and leave it running. A new window has none of the settings from your Terraform window, and without them the AWS CLI would use your **default** profile, which is a different account. Set them first (Step 1 of the [smoke test plan](../docs/runbook/terraform-smoke-test-plan.md)):

```powershell
$env:AWS_PROFILE = "ai-cloud-lab"
$env:AWS_DEFAULT_REGION = "us-east-1"
aws sts get-caller-identity --query Account --output text
Set-Location C:\GitHub\ai-cloud-lab\logging\victorialogs
Invoke-Expression (terraform output -raw ui_tunnel_command)
```

The account number must be the lab account (`394566733278` for the demo); if not, stop and fix the profile. Stop the tunnel with Ctrl+C when you are done.

Then open [http://localhost:18080/select/vmui/](http://localhost:18080/select/vmui/) in your browser. VictoriaLogs has no sign-in; only the tunnel reaches it. The same address serves the query API (`http://localhost:18080/select/logsql/query`) that an MCP tool uses.

Follow [END-TO-END-TEST.md](END-TO-END-TEST.md) to send test events and check vmui and the S3 archive.

## Lab Logs in Open WebUI

The `mcp` package runs a small read-only MCP server on the lab host at `http://127.0.0.1:8766/mcp` (systemd unit `ai-lab-logs-mcp`). It answers only on the lab host itself, which is where Open WebUI runs, so it has no token. It queries VictoriaLogs through the backend gateway, which passes only two read endpoints (`/select/logsql/query` and `/select/logsql/hits`) and only from the lab host's security group. The model never writes a query: each tool takes a few checked fields and the server builds the LogsQL. Lines are scrubbed of tokens, passwords and email addresses, and every result says that log text is untrusted.

| Tool | Use |
|---|---|
| `list_log_sources` | The two sources: `services` (host journal) and `containers` (Open WebUI) |
| `search_logs` | Recent lines, optionally with a text, a level, a systemd unit or a container |
| `count_log_events` | Counts per time bucket, for "how many" and "since when" |
| `summarize_log_errors` | The most frequent error messages in each source |
| `get_signin_events` | Open WebUI password and single sign-on results |

The install registers the server in Open WebUI as the tool server **Lab Logs**, using the lab's admin sign-in. If the lab has local password login turned off, the install cannot sign in; add it yourself in **Admin Settings > External Tools**: type **MCP (Streamable HTTP)**, URL `http://127.0.0.1:8766/mcp`, authentication **None**. To register it again later, open an SSM shell on the lab host and run `sudo ai-lab-register-logs-mcp`.

In a chat, turn on **Lab Logs** in the tools menu, then ask, for example, "Are there any errors in the lab logs in the last hour?". [END-TO-END-TEST.md](END-TO-END-TEST.md) has the check.

## Destroy

First uninstall both packages (`mcp`, then `vector`) from the root host with the commands from `terraform output -json uninstall_agent_commands`, and wait for each to finish. Uninstalling `mcp` also removes the Lab Logs connection from Open WebUI. Then review and apply a destroy plan:

```powershell
terraform plan -destroy '-out=destroy.tfplan'
terraform show -no-color destroy.tfplan
terraform apply destroy.tfplan
```

Destroy removes the archive bucket and its raw objects (`force_destroy` is on), the backend instance and volume, the SSM package document, the package bucket, the association and the archive permission on the root host role. Save any results you want first. The root lab host stays deployed.

## Cost and licence

The track adds one `t3.large` backend with an encrypted 80 GiB gp3 volume and a public IPv4 address, a small package bucket, and an S3 archive kept for `archive_retention_days` (30 by default). Estimate a run as `instance hourly rate × hours + gp3 GiB-month + public IPv4 hours + S3 GB-month + S3 requests`, using the region's [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/), [S3](https://aws.amazon.com/s3/pricing/) and [public IPv4](https://aws.amazon.com/vpc/pricing/) rates. A stopped instance still pays for its volume and public IPv4 address.

VictoriaLogs is Apache 2.0; see the [VictoriaLogs documentation](https://docs.victoriametrics.com/victorialogs/). This is a single-node lab stack, not production sizing.

## State and safety

Never run Terraform from `logging/` itself; run it in the track folder. Its local `.tfstate` is separate from the root state. The plan is the review gate: check the instance type, ingress rule, S3 lifecycle, IAM actions, package contents, association target and destroy behaviour before applying. Nothing here applies automatically.
