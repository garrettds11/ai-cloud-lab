# VictoriaLogs track

Vector on the root Ubuntu host reads the system journal and the Open WebUI Docker logs. It writes gzip NDJSON to this track's raw S3 archive and forwards the same events through a private intake adapter into VictoriaLogs. The backend accepts intake only from the root host's security group. The VictoriaLogs API and its built-in vmui UI are reached through SSM port forwarding.

See [the logging README](../README.md) to apply, open vmui and destroy, and [the end-to-end test](../END-TO-END-TEST.md) to check events and the archive.
