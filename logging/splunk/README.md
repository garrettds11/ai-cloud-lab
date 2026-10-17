# Splunk track

Vector on the root Ubuntu host reads the system journal and Open WebUI Docker logs, writes gzip NDJSON to this track's raw S3 archive, and forwards events through a private intake adapter to Splunk HEC. Splunk Search and dashboards provide the time-based review surface. The backend opens its HTTPS intake only to the root host security group; the UI is reached through SSM port forwarding.

The track uses a Splunk Enterprise trial image; review current license terms and ingestion limits before applying. See [the shared end-to-end test](../END-TO-END-TEST.md) for marker generation, SPL query, dashboard check, archive verification, and uninstall steps.
