# CI security assessment

[`security-scan.yml`](../.github/workflows/security-scan.yml) runs on every PR,
pushes to `dev`/`main`, Tuesday at 19:15 UTC on the default branch, and manual runs.
It needs no AWS credentials or running scanner instance. All actions use verified
commit pins; Trivy 0.75.0 uses the same immutable image as the runtime scanner.

## Coverage

| Asset | CI assessment |
| --- | --- |
| Git checkout: Python, frontend, scripts, manifests, Terraform, workflows | Trivy filesystem dependency vulnerabilities, secrets, advisory misconfiguration checks |
| Open WebUI | Scan the default upstream image from Terraform, resolved to a digest, for OS/application CVEs and secrets |
| Watchdog, vulnerability MCP, control API (customer/admin/spend), scanner controller | Assemble ZIP contents from **every** Terraform `archive_file` definition, honoring exclusions and filenames; scan unpacked contents and record ZIP/member SHA256 hashes |
| Logging Distributor packages | Run the copied package builder, scan each ZIP's contents and record its exact SHA256 |
| Logging backend images, Wazuh initialization images | Discover all Compose image declarations, including all optional backends and upstream Wazuh Compose at the bootstrap's pinned revision; deduplicate and scan each digest |
| Greenbone stack | Fetch and hash upstream Compose; scan every unique deployable image, including feed/data and initialization containers. PostgreSQL, its migrator, Redis, gvm-tools, OSPD and OpenVAS use [managed rebuilds](../scanner/image_builds/README.md), scanned as static image archives with base/recipe/image hashes recorded. |
| Terraform policy | Existing blocking Checkov job; Trivy misconfiguration findings are additional advisory SARIF evidence |

The current working tree contains **38 unique images and 11 packages**, plus a
repository scan. Counts change as asset definitions change. Dynamic Compose image
expressions, unsupported archive expressions, missing inputs, and unregistered Dockerfiles
fail inventory until their packaging/build strategy is implemented. There is no
placeholder Docker build or hand-maintained Lambda file list. The managed image
manifest registers exact build contexts and digest-pinned bases; all other
Dockerfiles fail inventory.

Images are scanned as `linux/amd64`, matching this lab's x86 instances. No scanned
image or Lambda is executed. Every target has a distinct stable SARIF category,
so one matrix upload cannot replace another target's results.

## Policy and publication

- HIGH and CRITICAL CVEs block, including findings without fixes.
- Detected secrets block except the exact, expiring immutable-feed test-data
  false positives documented in [the feed secret review](feed-secret-review.md).
  Reviewed findings remain in JSON/SARIF with rationale and expiry; the review
  manifest is included in the evidence bundle. GitHub may continue to display
  reviewed alerts because its supported SARIF subset does not guarantee automatic
  dismissal from suppression metadata. The CI gate uses the explicit review.
  Evidence contains rule/path/line information and
  redacts matched values and source excerpts.
- Trivy misconfiguration findings are advisory; Checkov remains the blocking
  Terraform policy. No blanket vulnerability ignores are introduced.
- Scanner errors, unresolved digests, missing reports and failed SARIF uploads
  fail `Security assessment gate`. Findings and available partial evidence are
  uploaded even when the scan gate fails.
- Transient scanner/registry process failures retry at most three times against
  the same resolved image digest with a shared timeout. Findings never trigger
  retries. Artifact names support reruns without upload conflicts.

SARIF appears under the repository's **Security → Code scanning**. Each run also
retains sanitized JSON/SARIF artifacts and a `security-evidence` artifact for 90
days (subject to the repository's retention cap). The evidence archive contains
inventory, scan statuses, package lists, resolved image digests, scanner/DB versions
and timestamps, source/package hashes, coverage reconciliation and `SHA256SUMS`.
Intermediate input artifacts expire after one day; they contain checkout/package
source and have the same repository access restrictions as the Actions run.

On trusted `dev`/`main` push, scheduled or manual runs, a separate job signs
`security-evidence.tar.gz` using GitHub artifact attestations and OIDC. That job
does not check out or execute repository code. PR runs publish reports but cannot
mint signing tokens. Failed-policy evidence is signed too: the attestation proves
where the report came from; `coverage.json` states whether coverage is complete
and the policy passed. It does **not** certify that the deployment is secure.

Download `security-evidence` from an Actions run, then verify:

```sh
gh attestation verify security-evidence.tar.gz --repo garrettds11/ai-cloud-lab
tar -xzf security-evidence.tar.gz
sha256sum --check SHA256SUMS
```

Set `Security assessment gate` as a required branch-protection check if merges
must be blocked. Code scanning must be available/enabled for the repository;
private repository availability and artifact attestation support depend on the
GitHub plan. No fallback silently treats failed publication as success.

## Assessment boundaries

CI evaluates repository defaults and packages, not private tfvars overrides or
deployed bytes. ZIP hashes identify deterministic CI repackaging; Terraform's
archive implementation can produce different ZIP bytes with equivalent contents.
The runtime scanner independently downloads and hashes deployed ZIPs/layers.

Python source with no bundled dependencies has no third-party package inventory
to report. CI does not include AWS-managed Lambda runtime/SDK vulnerabilities.
Boot-time apt packages, downloaded agents/Grafana plugins, AMIs, live IAM/API
configuration, network exposure and authenticated business logic require the
runtime/Greenbone or other assessment layers. An empty source CVE report is not
evidence that these runtime components were assessed.

Mutable upstream Compose definitions and image tags are resolved and recorded at
scan time; later deployments may select different content. All these limitations
also appear in the evidence inventory.

## Local verification

```sh
python -m pip install -r security/requirements.txt
python -m unittest discover -s security -p 'test_*.py' -v
python security/inventory.py
# Linux with Docker and buildx; use an ID from inventory.json:
python security/scan.py repository
python security/bundle.py
```

Generated inputs, raw temporary output and local databases live in the ignored
`.security-scan/` directory. Raw reports are deleted before sanitized publication.
