# CI security assessment

[`security-scan.yml`](../.github/workflows/security-scan.yml) runs on every PR,
pushes to `dev`/`main`, Tuesday at 19:15 UTC on the default branch, and manual runs.
It needs no AWS credentials or running scanner instance. All actions use verified
commit pins; Trivy 0.75.0 is rebuilt from pinned source with patched Go
dependencies using the same reviewed recipe as the runtime scanner. The exact
CI tool image is checksummed, distributed to jobs and assessed independently.

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

- HIGH and CRITICAL CVEs block only while a vendor fix exists that has not been
  taken (owner policy, issue #82). The lab does not patch downstream software:
  - No vendor fixed version (empty `FixedVersion`): accepted risk by policy.
  - A fixed version exists but the image vendor has not shipped it: accepted only by
    an owner record in [accepted-risk.json](accepted-risk.json), bound to the exact
    image digest and expiring within 31 days. Re-pinning the image ends the record.
  - Otherwise the finding blocks until the image or package is upgraded.
  Accepted findings are annotated, not removed or suppressed: they stay in
  `findings.json`, SARIF and the evidence reports, and are counted apart from
  blocking findings. The rule lives in `risk_acceptance.py`.
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
`remaining-cves.md` with readable asset names and `remaining-cves.csv` with every
HIGH/CRITICAL vulnerability instance, assessed artifact identity, package,
installed/fixed versions and advisory link. These files are checksummed and enter
the same trusted-run attestation bundle. They distinguish repeated instances
from distinct IDs; mitigation status is explicitly unassessed. Each row's `policy` column says whether it
blocks or is accepted, and on what basis. Owner records are explained in
[accepted-risk.md](accepted-risk.md).
Per-target `findings.json` and SARIF also include lower severities and secrets.

The archive additionally contains
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

## Software bills of materials

Evidence bundles include a CycloneDX 1.6 SBOM for each successfully assessed
target, including images, packaged Lambda/source artifacts and repository
dependencies that Trivy detected. `sboms.md` provides a readable index and
`sbom-index.json` records exact assessed identities, component counts and file
SHA256 hashes. Download the dedicated `security-sboms` artifact to read these
files directly, or find the identical files under `sboms/` in the checksummed
`security-evidence` archive. SBOMs are generated from the existing `--list-all-pkgs` inventory
at bundle time, with no extra image build, pull or vulnerability scan.

This exporter publishes package names, detected versions/PURLs/licenses and
their locations. It does not infer dependency edges, supplier information or
undetected versions. CycloneDX compositions explicitly remain incomplete;
these are detected-package SBOMs, not a claim that every software dependency
was found. Failed/missing assessments produce unavailable entries, not clean
empty SBOMs. Missing SBOM coverage prevents the assessment gate from passing.

The existing evidence checksum/provenance chain includes the SBOMs. Trusted
dev/main workflows attest the complete evidence archive; PR archives are
unsigned. Neither an SBOM nor an attestation approves an image or waives CVEs.

To export SBOMs from an older extracted evidence bundle without rescanning:

```sh
python security/sbom.py /path/to/extracted-security-evidence /path/to/sbom-output
```

The exporter verifies the source bundle's file checksums before reading its
inventories. It preserves that assessment's commit/image/archive identities;
it does not pretend the export came from a new scan. The output is separate
from the original evidence. See the [container catalogue](../scanner/CONTAINERS.md)
for what each Greenbone component does.

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
