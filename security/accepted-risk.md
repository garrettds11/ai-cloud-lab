# Accepted risk

Owner policy (issue #82): the lab does not patch downstream software. A HIGH or CRITICAL vulnerability blocks the assessment gate only while a vendor fix exists that has not been taken.

- **No vendor fix:** findings with no published fixed version are accepted by policy. They need no entry here and are counted as "accepted, no vendor fix".
- **Fix exists, vendor hasn't shipped it:** accepted only by an owner record in [accepted-risk.json](accepted-risk.json). A record names the exact assessed artifact, the advisory IDs (or `all`), a reason and an expiry no more than 31 days after acceptance. The artifact is the registry digest (`repo@sha256:…`) for a pulled image. For a Greenbone managed rebuild, which only adds its base image's own Debian updates, it is either the local image ID (`sha256:…`, `accepted_risk_identity` in the target's `status.json`, valid for that one build) or the vendor base digest (`accepted_risk_base_identity`), since the vendor's software is unchanged by the rebuild. Re-pinning produces a new identity and ends the record, so each new vendor artifact is assessed again. Each record is explained below.
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

## Open WebUI v0.12.3: dependencies the vendor has not updated

- **Accepted by:** the owner, 2026-10-22, for #82 and #113. Record `open-webui-v0.12.3`, expiring 2026-11-21.
- **Asset:** `ghcr.io/open-webui/open-webui@sha256:a537bbe1e6b428820f52ed4a029d7fff97062e24d5cf32c365fba36fd0542def`, the deployed lab image (`open_webui_container_image`).
- **Findings:** 117 HIGH/CRITICAL occurrences, 116 distinct IDs, in assessment run 39723004064 (22 October). Every one has a fixed version upstream that Open WebUI has not shipped:

| Group | Occurrences | Reachability in this lab |
|---|---:|---|
| `linux-libc-dev` kernel headers (Debian fix 6.1.190-1) | 61 | Not reachable: header files only; the container runs on the host kernel |
| Pillow 12.3.0 (fix 13.0.0) | 19 | Image handling in uploads and chats; not reviewed individually |
| pypdf 6.7.5 (fixes 6.14.1 to 6.19.0) | 10 | Reachable: any user's PDF upload; not reviewed individually |
| PyJWT 2.13.0 (fix 2.14.0) | 6 | Cognito sign-in checks tokens with `PyJWKSet`; CVE-2026-102266 needs an empty symmetric key in the key set, and Cognito publishes only RSA keys. Others not reviewed individually |
| Debian `libssh-gcrypt-4` (fix deb12u3) | 4 | Not reviewed individually |
| aiohttp 3.14.3 (fix 3.14.4) | 3 | Not reviewed individually |
| cryptography 48.0.0 | 3 | Not reviewed individually |
| jq, libjq1 (Debian fix deb12u3) | 2 | Not used by the application at run time |
| unstructured 0.22.31 (CVE-2026-71428, CRITICAL) | 1 | Not reachable: the flaw is in the `url=` option; Open WebUI only calls `partition_html(text=…)` |
| sentence-transformers 5.5.1 (CRITICAL), transformers 5.5.4 | 2 | Admin-set embedding and reranking model only |
| certifi, fsspec, pyarrow, mcp, jaraco.context, wheel | 6 | `wheel` and `jaraco.context` are install-time tools; the rest not reviewed individually |

- **Why accepted:** v0.12.3 is Open WebUI's latest release. The lab does not upgrade a vendor application's pinned dependencies (#113). The lab is an internal, per-deployment demo behind Cloudflare Access and Cognito, and is not online all the time.
- **Ends when:** Open WebUI ships a release that takes these fixes. Then upgrade, re-assess, and remove this record. The record expires on 2026-11-21.

## VictoriaLogs v1.53.0: Go 1.27.3 fix

- **Accepted by:** the owner, 2026-10-22. Record `victorialogs-v1.53.0-go-1.27.3`, expiring 2026-11-21.
- **Asset and exposure:** as in the VictoriaLogs section above.
- **Finding:** CVE-2026-111943 (HIGH), Go standard library, fixed in Go 1.26.10 and 1.27.3. v1.53.0 is still the latest VictoriaLogs release.
- **Ends when:** the same re-pin that ends the record above (#93).

## Greenbone ospd-openvas: Go 1.27.3 fix

- **Accepted by:** the owner, 2026-10-22. Record `ospd-openvas-go-1.27.3`, expiring 2026-11-21.
- **Asset:** vendor base `registry.community.greenbone.net/community/ospd-openvas@sha256:31a85272abc77521c98451ce6834ed7d2f887e98ff1b26ff3fd94f00d000b169`, rebuilt by the lab with Debian updates only.
- **Finding:** CVE-2026-111943 (HIGH) in Greenbone's `ospd-scans` Go binary, fixed in Go 1.26.10 and 1.27.3, not yet shipped by Greenbone.
- **Exposure:** the scanner stack runs only while an assessment is in progress, inside its own instance.
- **Ends when:** Greenbone publishes an image built with the fixed Go; re-pin the base digest.

## Greenbone gvm-tools: certifi

- **Accepted by:** the owner, 2026-10-22. Record `gvm-tools-certifi`, expiring 2026-11-21.
- **Asset:** vendor base `registry.community.greenbone.net/community/gvm-tools@sha256:53d294ab34b1a7fe3582826560aa8a6dcdfb395e396b195a03dd7d4b3b9cd885`, rebuilt by the lab with Debian updates only.
- **Finding:** GHSA-6q48-6c7w-9gw2 (CRITICAL) in Python certifi 2026.7.22, fixed in 2026.10.19, not yet shipped by Greenbone.
- **Exposure:** gvm-tools is the scanner stack's command-line client; it runs only during an assessment.
- **Ends when:** Greenbone publishes an image with the fixed certifi; re-pin the base digest.
