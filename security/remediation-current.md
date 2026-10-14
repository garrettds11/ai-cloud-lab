# Remediation status — 14 October 2026

## Completed assessment baseline

[Assessment 38533402718](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38533402718)
has complete coverage for 28 targets: repository source, five packaged inputs,
21 configured images and one separate application candidate. It reports 1,875
HIGH/CRITICAL CVE instances (364 distinct IDs): 1,396 in configured assets and
479 in the candidate. These are image/package instances, not unique CVEs or
proof of exploitability. Its 72 feed test-data secret detections did not inherit
the previous digest's review. The gate remains blocking.

Download its `security-evidence` artifact, extract it, and open
`remaining-cves.md` or `remaining-cves.csv`. Rows include the assessed identity,
package, installed/fixed versions and advisory. All sanitized findings and
SARIF remain under `reports/`. PR evidence is checksummed but unsigned;
trusted dev/main runs use artifact attestation. Publication is not deployment.

## Verified patches

- **Trivy:** rebuild 0.75.0 with Go 1.27.2 and x/net 0.60.0, then update Alpine
  packages. Its exact executing image is assessed separately: zero
  HIGH/CRITICAL findings. The remaining UNKNOWN GO-2026-5932 module finding is
  retained; a build guard rejects the affected deprecated OpenPGP package.
- **Open WebUI candidate:** the same-run comparison is **507 → 479**
  HIGH/CRITICAL instances, **28 removed / zero added**. Dependency checks,
  offline API checks, backend health, signup/signin, valid token use and invalid
  token rejection pass. Upgrading setuptools replaces vulnerable vendored
  jaraco.context/wheel copies. No remaining HIGH/CRITICAL candidate finding
  reports a fixed version. The original configured image remains assessed and
  unchanged; the candidate is not deployed.
- **Open WebUI runtime reduction:**
  [assessment 38573301642](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38573301642)
  confirms **479 → 277** HIGH/CRITICAL instances after removing build packages,
  **202 removed / zero added**. Against the original image in this same run,
  the complete candidate reduction is **507 → 277**, **230 removed / zero
  added**. Dependency, CPU tensor, backend and authentication checks pass;
  the image has zero secrets and no HIGH/CRITICAL finding reporting a fixed
  version. Runtime libgcc, libstdc++, MariaDB, OpenSSL and zlib libraries remain
  installed. The assessed candidate is
  `sha256:38b7c254f4f4479da466ea0a15ad947365e30601f0cf527517d77ec6bb88d0d4`.
  It remains undeployed and its 277 residual findings remain blocking.
- **Greenbone:** managed rebuilds of PostgreSQL, its migrator, Redis, gvm-tools,
  OSPD and OpenVAS remain assessed. The PostgreSQL shared private keys are gone;
  unique keys are created at startup. Socket-only service isolation reduces
  network/escalation paths without accepting residual CVEs.
- **Feed classification:** the new assessed feed digest `bc7d28cf...` has the
  same 28 complete file hashes and 72 path/rule/line matches as the previous
  review. Its OCI index/manifest and four layer hashes were independently
  verified. The exact-image review is renewed with unchanged 12 November
  expiry. A subsequent scan must confirm its use. No CVE or service key is
  covered by this classification.
- **Greenbone curl:** [assessment 38571189370](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38571189370)
  confirms complete scans for the three new rebuilds. Compared with the
  preceding assessment, gsad is **69 → 67**, gvmd **120 → 116**, and nginx
  **68 → 64** HIGH/CRITICAL instances: **ten removed / zero added**. All ten
  reported fixes are gone. Unfixed curl CVEs and other residual findings remain
  blocking. Service binary/version checks pass; full stack acceptance is pending.

## Remaining acceptance

The Open WebUI candidate removes native compilers and development headers
after installing binary wheels. An apt simulation refuses removal of runtime
packages, and autoremove is disabled. Dependency, CPU tensor, startup and
authentication checks run after removal, followed by an image scan. Removing
kernel headers removes their image findings; it does not patch the host kernel.
Its first build correctly stopped when the removal plan included shared
libraries outside the allowed list. Binutils and those libraries are now
retained. Its corrected image scan and compatibility checks now pass; removal
of development packages is distinguished from patching executable libraries.

ChromaDB's remaining critical findings require further work. Its latest
published release is still 1.5.9. The
[merged remote-code patch](https://github.com/chroma-core/chroma/pull/7237)
has a documented nested-kwargs bypass concern; it is not treated as a complete
fix. An absent fixed-version field grants no exception.

## Merge and deployment boundaries

All unreviewed secrets and HIGH/CRITICAL CVEs, including unfixed findings,
remain blocking. No vulnerability waiver or ignore-unfixed policy was added.
The candidate needs retrieval/MCP acceptance and reviewed image publication
before promotion. ECR and patched Ubuntu AMI work remains tracked in
[#90](https://github.com/garrettds11/ai-cloud-lab/issues/90). No AWS apply,
image promotion, production migration or merge has been performed.

Dockle remains an optional image-configuration follow-up; it does not patch
the residual package CVEs. CI covers committed defaults and packaged artifacts;
live overrides, host kernels/AMIs and deployed services require runtime
assessment. Full Greenbone feed-backed operational acceptance remains pending.
