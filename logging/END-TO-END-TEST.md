# End-to-end test

Run this after the VictoriaLogs track has been applied and its Distributor association has succeeded. The root lab host stays running throughout.

## Send test events

Open an SSM shell session to the root lab instance and run:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
logger -p authpriv.notice "bakeoff-host-$RUN_ID"
docker exec open-webui python -c "import logging; logging.error('bakeoff-openwebui-$RUN_ID')"
printf '%s\n' "$RUN_ID" >/tmp/logging-bakeoff-run-id
echo "$RUN_ID"
```

The first event is a host syslog message. The second goes to the Open WebUI container's stderr and appears in its Docker log stream. Note the printed run ID.

## Check vmui

Allow a minute for buffering, then in vmui (`http://localhost:18080/select/vmui/`, see the [README](README.md)) run:

```
_time:15m "<RUN_ID>"
```

Confirm both events appear, from the `ubuntu-host` and `open-webui` sources, with the expected timestamps and host. Then run `_time:15m | stats by (source) count()` and confirm it counts both sources and changes when you change the time range.

The same query works against the API, which is what an MCP tool calls:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:18080/select/logsql/query -Body @{ query = '_time:15m "<RUN_ID>"' }
```

## Check the S3 archive

In the track folder:

```powershell
$bucket = terraform output -raw archive_bucket
aws s3 ls "s3://$bucket/raw/victorialogs/" --recursive
```

The objects are gzip-compressed NDJSON. Download one and confirm it holds both markers with the same timestamps. The archive is the independent record; don't use the VictoriaLogs index as proof of it.

## Uninstall and destroy

Before destroying, uninstall Vector with the command from `terraform output -json uninstall_agent_commands`, wait for the SSM command to succeed, and check on the root host:

```bash
systemctl is-active vector || true
```

Then destroy the track as described in the [README](README.md#destroy).
