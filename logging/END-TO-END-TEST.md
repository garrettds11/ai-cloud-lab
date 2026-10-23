# End-to-end test

Test docs: [Deploy and smoke test](../docs/runbook/terraform-smoke-test-plan.md) · [Access and network](../docs/runbook/smoke-tests/access-and-network.md) · [Control panel](../docs/runbook/smoke-tests/control-panel.md) · [Lab features](../docs/runbook/smoke-tests/lab-features.md) · [Vulnerability MCP](../docs/vuln-mcp-acceptance-tests.md) · [Scanner](../scanner/README.md) · [Logging](README.md) · **Logging end-to-end**

Run this after the VictoriaLogs track has been applied and both Distributor associations (`vector` and `mcp`) have succeeded. The root lab host stays running throughout.

Each step says where it runs:

- **Local PowerShell:** your Windows PowerShell window, in `C:\GitHub\ai-cloud-lab\logging\victorialogs`.
- **Linux (SSM shell on the lab instance):** a plain `sh` shell; paste **one command at a time**. To open it from any PowerShell window:

  ```powershell
  $env:instance_id = terraform -chdir=C:\GitHub\ai-cloud-lab output -raw instance_id
  aws ssm start-session --target $env:instance_id
  ```

  Type `exit` to return to PowerShell.
- **Browser (vmui):** searches typed into vmui's query box, with the tunnel running in its own window ([README](README.md#open-vmui)).

## Before you start

**Local PowerShell** (not the tunnel window). Use the lab account and look up the lab instance:

```powershell
$env:AWS_PROFILE = "ai-cloud-lab"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:instance_id = terraform -chdir=C:\GitHub\ai-cloud-lab output -raw instance_id
$env:instance_id
```

The last line prints an instance ID such as `i-0123456789abcdef0`. Check that both installs succeeded:

```powershell
aws ssm list-associations --query "Associations[?contains(AssociationName,'-install')].[AssociationName,Overview.Status]" --output text
```

**Local PowerShell.** Both lines must say `Success`. `Pending` means the lab instance is stopped or the install is still running; start the instance and wait a few minutes. `Failed` means read the install output (see [Troubleshooting](#troubleshooting)).

**Local PowerShell.** Open a shell on the lab instance:

```powershell
aws ssm start-session --target $env:instance_id
```

**Linux (SSM shell on the lab instance).** Check that the log shipper and the Lab Logs MCP server are running. Each prints `active`:

```bash
systemctl is-active vector
```

```bash
systemctl is-active ai-lab-logs-mcp
```

If `vector` prints `failed`, read why with `sudo journalctl -u vector --no-pager | tail -30`. Stay in this shell for the next section.

## Send test events

**Linux (SSM shell on the lab instance).** Paste these one at a time:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
```

```bash
logger -p authpriv.notice "bakeoff-host-$RUN_ID"
```

```bash
sudo docker exec open-webui sh -c "echo ERROR bakeoff-openwebui-$RUN_ID > /proc/1/fd/2"
```

```bash
echo "$RUN_ID"
```

The first event is a host syslog message. The second is written to the Open WebUI container's main process output, so it appears in the container's Docker log stream. (Output of a separate `docker exec` command goes to your terminal, not to the container's log, so it would not test this path.) Note the printed run ID, then type `exit` to return to PowerShell.

## Check vmui

Allow a minute for buffering.

**Browser (vmui).** With the tunnel from the [README](README.md#open-vmui) running, open [http://localhost:18080/select/vmui/](http://localhost:18080/select/vmui/) and type this into the query box, replacing `<RUN_ID>` with your run ID:

```
_time:15m "<RUN_ID>"
```

Confirm both events appear: `bakeoff-host-<RUN_ID>` from the `ubuntu-host` source and `bakeoff-openwebui-<RUN_ID>` from the `open-webui` source, with the expected timestamps and host. A third line, sudo's own record of your `docker exec` command, also contains the run ID; it comes from the host and does not count as the Open WebUI event. Then run `_time:15m | stats by (source) count()` and confirm it counts both sources and changes when you change the time range.

**Local PowerShell.** The same query works against the API, which is what an MCP tool calls:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:18080/select/logsql/query -Body @{ query = '_time:15m "<RUN_ID>"' }
```

## Check Lab Logs in Open WebUI

**Linux (SSM shell on the lab instance).** Open the shell again (see the top of this page), then confirm the MCP server is running and registered, one command at a time:

```bash
systemctl is-active ai-lab-logs-mcp
```

```bash
sudo ai-lab-register-logs-mcp
```

The second command should report that the connection is already correct and list five tools. Type `exit`. Then sign in to Open WebUI, open a new chat, turn on **Lab Logs** in the tools menu and ask:

```
Search the lab logs for <RUN_ID> in the last 15 minutes.
```

Confirm the answer shows both test events and says it used `search_logs`. Then ask "Are there any errors in the lab logs in the last hour?" and confirm it calls `summarize_log_errors`, and that the `bakeoff-openwebui-<RUN_ID>` line is among the errors.

## Check the S3 archive

**Local PowerShell** (any folder):

```powershell
$bucket = terraform -chdir=C:\GitHub\ai-cloud-lab\logging\victorialogs output -raw archive_bucket
aws s3 ls "s3://$bucket/raw/victorialogs/" --recursive
```

Pass: it lists one object about every minute while the lab was running, under `date=<UTC date>`. The objects are gzip-compressed NDJSON. Download one and confirm it holds both markers with the same timestamps. The archive is the independent record; don't use the VictoriaLogs index as proof of it.

## Troubleshooting

**An install association says `Failed`.** Read the install output on the lab instance. Set `$name` to the failed association's name from the list above, then run:

```powershell
$name = "ai-cloud-lab-victorialogs-vector-install"
$assoc = aws ssm list-associations --query "Associations[?AssociationName=='$name'].AssociationId" --output text
$exec = aws ssm describe-association-executions --association-id $assoc --max-results 1 --query "AssociationExecutions[0].ExecutionId" --output text
$cmd = aws ssm describe-association-execution-targets --association-id $assoc --execution-id $exec --query "AssociationExecutionTargets[0].OutputSource.OutputSourceId" --output text
aws ssm list-command-invocations --command-id $cmd --details --query "CommandInvocations[0].CommandPlugins[].[Name,Status,Output]" --output text
```

After a fix in `modules/track/package-src`, rebuild the archives with `python C:\GitHub\ai-cloud-lab\logging\modules\track\package-src\build_packages.py`, then plan and apply this track again. The package version follows the archive's hash, so the association reinstalls the changed package.

**vmui shows no logs.** Check the [Before you start](#before-you-start) items first: if `vector` is not `active`, nothing is shipped.

## Uninstall and destroy

Before destroying, uninstall both packages (`mcp`, then `vector`) with the commands from `terraform output -json uninstall_agent_commands`, wait for each SSM command to succeed, and check on the root host.

**Linux (SSM shell on the lab instance):**

```bash
systemctl is-active ai-lab-logs-mcp vector || true
```

Both should report `inactive`, and **Lab Logs** should no longer be listed in Open WebUI's **Admin Settings > External Tools**.

Then destroy the track as described in the [README](README.md#destroy).
