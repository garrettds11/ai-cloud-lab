# Accepted risk

Findings the owner has accepted for now. Accepting a finding here does not change the assessment gate: it still counts every HIGH and CRITICAL vulnerability, so each one stays visible in SARIF, the evidence bundle and the blocking count. A merge past these findings is an owner decision, made at merge time.

Keep accepted risk separate from verified source fixes (`source-fixes.json`) and from secret reviews (`feed-secret-review.json`).

## VictoriaLogs v1.53.0: Go standard library 1.27.1

- **Accepted by:** the owner, 2026-10-17, for PR #91.
- **Asset:** `victoriametrics/victoria-logs:v1.53.0@sha256:251121fa882af99b95ba0c230a4a2f412ea602d2698c64a96c58dc9842bb755d`, the logging track's only image (`logging/modules/track/compose.yaml.tftpl`).
- **Findings:** 13 distinct CVEs in the Go standard library compiled into the `victoria-logs-prod` binary, all fixed in Go 1.26.9 and 1.27.2. Three are HIGH and block the gate:

| CVE | Severity | Area |
|---|---|---|
| CVE-2026-97031 | HIGH | `crypto/tls`: denial of service through repeated ECH outer extension references |
| CVE-2026-78667 | HIGH | `net/http`: denial of service through crafted Range headers |
| CVE-2026-78669 | HIGH | `net/http` HTTP/2: denial of service through excessive SETTINGS frames |

The other ten are MEDIUM, LOW or UNKNOWN and do not block.

- **Why accepted:** v1.53.0 is the latest upstream release, so no fixed image exists yet. The track is a short-lived test backend whose purpose is to give Open WebUI MCP tools a log store to query.
- **Exposure:** VictoriaLogs listens only on `127.0.0.1:9428` on the backend instance. The only clients are the local intake gateway and users who open an SSM port-forwarding session. It serves plain HTTP, not TLS, so the `crypto/tls` finding has no listener to reach. The backend's security group accepts only HTTPS from the lab host's security group, and that traffic ends at the gateway, not at VictoriaLogs. The three HIGH findings are denial of service, which at worst stops the test log store.
- **Ends when:** a VictoriaLogs release built with Go 1.27.2 or later is available. Then re-pin the image digest, confirm the findings are gone, and remove this entry. Review it before 2026-11-16 if that hasn't happened.
