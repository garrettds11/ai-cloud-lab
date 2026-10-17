# Graylog track

Vector on the root Ubuntu host reads the system journal and Open WebUI Docker logs, writes gzip NDJSON to this track's raw S3 archive, and forwards events to a private GELF HTTP input on the Graylog backend. Graylog Search and dashboards provide the time-based review surface. The backend opens its HTTPS intake only to the root host security group; the UI is reached through SSM port forwarding.

See [the shared end-to-end test](../END-TO-END-TEST.md) for marker generation, Graylog query, dashboard check, archive verification, and uninstall steps.
