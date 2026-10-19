# Accepted risk

Owner policy (issue #82): the lab does not patch downstream software. A HIGH or CRITICAL vulnerability blocks the assessment gate only while a vendor fix exists that has not been taken.

- **No vendor fix:** findings with no published fixed version are accepted by policy. They need no entry here and are counted as "accepted, no vendor fix".
- **Fix exists, vendor hasn't shipped it:** accepted only by an owner record in [accepted-risk.json](accepted-risk.json). A record names the exact assessed artifact, the advisory IDs (or `all`), a reason and an expiry no more than 31 days after acceptance. The artifact is the registry digest (`repo@sha256:…`) for a pulled image, or the local image ID (`sha256:…`, shown as `accepted_risk_identity` in the target's `status.json`) for a managed rebuild or candidate. Re-pinning or rebuilding produces a new identity and ends the record, so each new artifact is assessed again. Each record is explained below.
- **Everything else blocks** until the image or package is upgraded.

Accepted findings stay visible in `findings.json`, SARIF (with no suppression) and the evidence reports, and are counted apart from blocking findings. Keep accepted risk separate from verified source fixes (`source-fixes.json`) and from secret reviews (`feed-secret-review.json`).

## VictoriaLogs v1.53.0: Go standard library 1.27.1

- **Accepted by:** the owner, 2026-10-17, for PR #91. Record `victorialogs-v1.53.0-go-stdlib` in `accepted-risk.json`, expiring 2026-11-16.
- **Asset:** `victoriametrics/victoria-logs:v1.53.0@sha256:251121fa882af99b95ba0c230a4a2f412ea602d2698c64a96c58dc9842bb755d`, the logging track's only image (`logging/modules/track/compose.yaml.tftpl`).
- **Findings:** 13 distinct CVEs in the Go standard library compiled into the `victoria-logs-prod` binary, all fixed in Go 1.26.9 and 1.27.2. Three are HIGH; the record accepts them:

| CVE | Severity | Area |
|---|---|---|
| CVE-2026-97031 | HIGH | `crypto/tls`: denial of service through repeated ECH outer extension references |
| CVE-2026-78667 | HIGH | `net/http`: denial of service through crafted Range headers |
| CVE-2026-78669 | HIGH | `net/http` HTTP/2: denial of service through excessive SETTINGS frames |

The other ten are MEDIUM, LOW or UNKNOWN and are outside the gate.

- **Why accepted:** v1.53.0 is the latest upstream release, so the vendor has not shipped a fixed image yet (re-pin tracked in #93). The track is a short-lived test backend whose purpose is to give Open WebUI MCP tools a log store to query.
- **Exposure:** VictoriaLogs listens only on `127.0.0.1:9428` on the backend instance. The only clients are the local intake gateway and users who open an SSM port-forwarding session. It serves plain HTTP, not TLS, so the `crypto/tls` finding has no listener to reach. The backend's security group accepts only HTTPS from the lab host's security group, and that traffic ends at the gateway, not at VictoriaLogs. The three HIGH findings are denial of service, which at worst stops the test log store.
- **Ends when:** a VictoriaLogs release built with Go 1.27.2 or later is available. Then re-pin the image digest, confirm the findings are gone, and remove this entry and its record. The record expires on 2026-11-16; after that the findings block again until it is renewed or the image is re-pinned.
