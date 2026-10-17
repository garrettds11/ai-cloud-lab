# Quickwit track

Vector on the root Ubuntu host reads the system journal and Open WebUI Docker logs, writes gzip NDJSON to this track's raw S3 archive, and forwards events through a private intake adapter into the Quickwit `webui-logs` index. Grafana provides time-based search and dashboards through the Quickwit data source. The backend opens its HTTPS intake only to the root host security group; the UI is reached through SSM port forwarding.

See [the shared end-to-end test](../END-TO-END-TEST.md) for marker generation, Grafana query and dashboard checks, archive verification, and uninstall steps.
