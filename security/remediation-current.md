# Remediation status — 16 October 2026

## Latest complete assessment

[Assessment 38985470580](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38985470580)
assessed PR head `6b90f2a27d3526d5d88b2f53f0256aa5dbbefb28` with complete coverage
for the same **28 targets**. The evidence archive and all **120 published
evidence checksums** were independently verified. The policy gate remains
blocking; no deployment, registry promotion or merge occurred. Counts match the
previous complete assessment
[38909417812](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38909417812).

| Reported result | Current |
|---|---:|
| HIGH/CRITICAL finding occurrences | 1,614 |
| Distinct finding IDs | 351 |
| Configured occurrences | 1,302 |
| Undeployed candidate occurrences | 312 |
| CRITICAL occurrences | 37: 27 configured, 10 candidate |
| Original 27 finding IDs present in candidate, all severities | 0 |
| Rows with a verified upstream source fix (still counted and blocking) | 3 |
| New feed digest secret detections awaiting review | 72 |

Counts are package finding occurrences, not assets or proven exploits. Scope
remains 21 configured images, one candidate, five packaged inputs and repository
source. The five packaged inputs include the image-build recipe bundle; they
are not five distinct Lambda functions. Uncommitted logging work is excluded.

## Verified SQLite source replacement; automated findings retained

The candidate's system SQLite library is upgraded from **3.40.1** to official
upstream **3.54.0**, installed as a real local Debian package
`3.54.0-0ai.cloud.lab1`. The canonical source ZIP is checksum-pinned. Parser
generation preserves Debian's optional bounded UPDATE/DELETE syntax, which
cannot be preserved by merely setting a compiler macro on the published
amalgamation. Existing runtime packages remain in place; no new runtime
packages were added for source generation.

The actual assessed candidate is
`sha256:daa8f9b20de276d424e73663498ca281176c580afba6fa793a7cc1b8e70396a5`.
It is built with Debian's own hardening flags, keeps all 283 public `sqlite3_*`
symbols of the distro library and is marked `Multi-Arch: same`. The library is
byte-identical to the previous build: Bookworm's flags add only compile-time
format checks to what was already used.
Its image-build compatibility metadata records:

- Official source: `https://sqlite.org/2026/sqlite-src-3540000.zip`.
- Archive SHA3-256: `a5c29342ca185abcda332448fbb27c536661245be27e20199b46c49099b0e15e`.
- Generated source SHA3-256: `6880dacd1d6e9fde1f535d8ea3da7bdf71a2107bfff81b6301667eb13579168a`.
- Installed library SHA256: `38e3f19e61593abd10a825c99348c01487848ca8388cba224c26567de6b654b6`.
- Package-owned receipt: `/opt/ai-cloud-lab/sqlite/security-build.json`.

Checks pass against the actual assessed image: loaded library/source identity,
old-version database reopen/schema migration, WAL, JSON, FTS5, RTree, bounded
UPDATE/DELETE, rollback, integrity and backend authentication.

A Chroma add/query check also passes, but it does **not** exercise the replaced
library. Chroma 1.5.9 stores the vector database through its Rust bindings,
which statically embed their own SQLite 3.46.0 (source ID
`2024-05-23 13:25:27 96c92aba...`, FTS5 enabled). That copy is unchanged, falls
within the affected range of CVE-2026-11822/11824 (fixed upstream in 3.53.2) and
is not reported by Trivy, because it has no package metadata. Exploitation needs
a crafted `chroma.sqlite3`, meaning write access to the Open WebUI data volume.
It is an unreported, unremediated copy, not a verified fix. Candidate acceptance
now records bundled SQLite engines as `sqlite_embedded_copies`.

These use disposable storage and no external networking. They do not exercise
an existing production database, live model download or a full deployed stack.
This is a locally maintained upstream build, not a Debian-issued update.

| Finding still reported by Trivy | Upstream fix evidence | Gate status |
|---|---|---|
| CVE-2025-7458 | Debian identifies 3.42.0 as the upstream fixed version; loaded source is 3.54.0 | Retained |
| CVE-2026-11822 | Debian identifies 3.53.2 as fixed; loaded source is 3.54.0 | Retained |
| CVE-2026-11824 | Debian identifies 3.53.2 as fixed; loaded source is 3.54.0 | Retained |

Trivy's [Debian detector](https://github.com/aquasecurity/trivy/blob/v0.75.0/pkg/detector/ospkg/debian/debian.go#L95)
adds advisories with an empty distro fixed version without comparing installed
versions. Thus all three remain reported against the truthful 3.54.0 package.
The source fix is verified; **zero automated findings were removed in this batch**.
These rows now carry report-only [source-fix records](source-fixes.json). A
record applies only when the package, exact installed version, pinned archive
SHA3, source ID and the library hash in the assessed image's build receipt all
match, the distro still publishes no fixed version and the 31-day review is
current. Annotated rows stay in findings, in SARIF (no suppression, unchanged
fingerprint) and in the blocking count. `remaining-cves` labels them and the run
summary counts them separately. Counting them as closed would be a separate,
explicitly approved policy change. No CVE exclusions, VEX suppression or gate
changes were added.

**Superseded (#82, 2026-10-18).** Under the owner's vendor-patch policy the lab no
longer builds SQLite from source. The candidate now keeps Debian's own `libsqlite3-0`,
and the source-fix record was removed from `source-fixes.json`. Debian publishes no
fixed version for these three CVEs, so they are accepted risk with basis
`no_vendor_fix`: still reported in findings and SARIF, counted apart from blocking
findings, and gated again as soon as Debian ships a fix.

Primary fix evidence: [CVE-2025-7458](https://security-tracker.debian.org/tracker/CVE-2025-7458),
[CVE-2026-11822](https://security-tracker.debian.org/tracker/CVE-2026-11822),
[CVE-2026-11824](https://security-tracker.debian.org/tracker/CVE-2026-11824),
[upstream checksums](https://sqlite.org/download.html) and
[release/source identity](https://sqlite.org/changes.html).

## Why the reported count increased

Compared with complete assessment 38617707801, the current scan reports
**79 added occurrences, none removed**, across five additional finding IDs.
The vulnerability database changed from 14 October to 16 October. The candidate
adds 35 reported HIGH rows in unchanged packages: 27 FFmpeg rows (three IDs
across nine packages) and eight util-linux rows (one ID across eight packages).
Its FFmpeg remains `5.1.9-0+deb12u1`; util-linux remains `2.38.1-5+deb12u3`.
The original configured image adds those 35 plus one kernel-header finding;
Redis adds eight util-linux rows. No asset was added to obtain these counts.

The configured Open WebUI image is now 543, candidate 312 and Redis 58. Other
asset counts are unchanged. The previously pinned OpenVAS base was deleted
upstream and was replaced with verified immutable digest `77d6f443...`; native
scanner acceptance passes and its count remains 100. That replacement was itself
deleted on 16 October and is now pinned to verified digest `030fed85...`
(see the critical plan); its acceptance and scan are pending. Registry retention and
scanner-tool reuse requirements were added to [issue #90](https://github.com/garrettds11/ai-cloud-lab/issues/90).

## Remaining merge work

The [critical remediation plan](critical-remediation.md) accounts for all 37
critical rows. Chroma needs a complete patch or replacement; reachability evidence now
runs in candidate acceptance (see the critical plan). Libxml2 is the broadest shared critical component and
remains affected in both Bookworm and Trixie. Mbed TLS, AOM, GLib, LMDB, OpenSSH
and zlib need their recorded package/applicability reviews. Supported component
replacement or audited source backports must retain application functionality.

The feed resolved to immutable digest
`registry.community.greenbone.net/community/vulnerability-tests@sha256:e0f85aca0d448653cb8da8e1065c374cb7239bd8d902b988f2b3d4b924eae583`,
which changed again after the previous assessment (`42410b5a...`).
Its 72 secret detections did not inherit the reviewed `bc7d28cf...` test-data review.
They remain blocking pending a new exact-image/file review; this report does
not establish that they are live credentials or renew the prior disposition.

Baseline CI, early candidate acceptance, native scanner acceptance and all 33
local scanner tests pass. Early candidate verification now runs before the
expensive Trivy build. SARIF and SBOM evidence is published even on policy
failure. PR evidence is checksummed but unsigned; trusted dev/main runs attest
provenance. Full feed-backed Greenbone deployment acceptance remains pending.

## Earlier assessment history

### Completed assessment baseline

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

### Verified patches

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

### Remaining acceptance

The final bundle of assessment 38573301642 reports **incomplete coverage**:
the previously pinned gvm-tools base was deleted upstream during the scan.
It has 1,594 known HIGH/CRITICAL instances, including the 277 candidate
instances, with the gvm-tools target missing. This is not a complete remaining
count or a clean result. The feed job confirmed 72 reviewed false positives
and zero feed blockers. The replacement gvm-tools digest is pinned in the
manifest, preserves its inspected upstream startup configuration, and checks
GMP and Unix-socket library imports after signed Debian updates. Assessment
38576332719 confirms that the replacement build and imports pass, its image
scan completes with 69 residual blockers, and aggregate coverage is restored. This
upstream retention problem reinforces the ECR promotion work in #90.

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

### Merge and deployment boundaries

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
