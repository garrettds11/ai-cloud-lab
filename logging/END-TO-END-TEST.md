# One end-to-end test per track

Run this after that track's plan has been reviewed and applied. Finish the uninstall and destroy before starting the next track. The root Ubuntu lab host remains running for all five runs.

## Generate the same test events

Use an SSM shell session to the root lab instance. Each run should use the same host and application log paths. Save the generated run ID in your notes so you can search the backend and archive:

```bash
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
logger -p authpriv.notice "bakeoff-host-$RUN_ID"
docker exec open-webui python -c "import logging; logging.error('bakeoff-openwebui-$RUN_ID')"
printf '%s\n' "$RUN_ID" >/tmp/logging-bakeoff-run-id
```

The first event is a host auth-priority syslog message. The second is emitted to the Open WebUI container's stderr and appears in its Docker log stream. Both collectors should return the same unique `RUN_ID` markers.

Wait for the Distributor association to succeed, then allow a minute for buffering and ingestion. In each UI, set the time picker to **Last 15 minutes** and search for `bakeoff-<RUN_ID>`. Confirm both host and container markers, the event timestamp, source, and host. Create a small dashboard panel or saved view that groups event counts by source, then confirm it follows the time picker. In a second shell, verify the independent raw archive:

```powershell
$bucket = terraform output -raw archive_bucket
aws s3 ls "s3://$bucket/raw/<track>/" --recursive --region <root-region>
```

The S3 objects are gzip-compressed NDJSON. Download one object and confirm it contains both markers and the same event timestamps. Do not rely on the backend index as proof of the S3 archive.

## Candidate-specific UI checks

| Track | Search surface | Dashboard check |
|---|---|---|
| Wazuh | In Wazuh Dashboard, create/select `wazuh-archives-*` with time field `timestamp`; search `bakeoff-<RUN_ID>`. Security alerts remain visible in `wazuh-alerts-*`. | Use Discover with the archive pattern, then save a visualization or dashboard showing event counts by `source`. |
| Graylog | Search `message:bakeoff-<RUN_ID>` in Graylog Search. | Save a search or dashboard widget showing counts by source. |
| VictoriaLogs | In Grafana Explore, query `bakeoff-<RUN_ID>` through the VictoriaLogs data source. | Save the query as a Grafana panel and verify the time range changes the result. |
| Quickwit | In Grafana Explore, search the `webui-logs` index for `bakeoff-<RUN_ID>`. | Save a Grafana panel for counts by source and change the time range. |
| Splunk | In Search & Reporting, run `index=main "bakeoff-<RUN_ID>"`. | Save the search as a dashboard panel and change the time range. |

## Uninstall and destroy

The apply creates a one-time State Manager install association and a custom Distributor package. Before destroying the track, uninstall every package shown by `terraform output -json ssm_package_names`. Run the corresponding command from `terraform output -json uninstall_agent_commands`, wait for the SSM command to finish successfully, and verify the package is gone from the host:

```bash
systemctl is-active vector || true
systemctl is-active wazuh-agent || true
```

Then review and run a saved destroy plan in that track directory. The track destroy deletes the archive and its objects, its backend, its package bucket and document, and the narrow permissions and parameters attached to the root host. Save any desired test results before destroying.
