# Greenbone feed secret review

Reviewed 14 October 2026 against completed assessment
[38533402718](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38533402718);
renewed 16 October 2026 against assessment
[38985470580](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38985470580).

## Disposition

The **72 secret detections in 28 files** are intentional vulnerability-test inputs,
duplicated across feed directories 21.04, 22.04, 24.10 and 25.0. They are false
positives for leaked lab/scanner credentials. Their presence is required to detect
known shared-key and authentication-bypass vulnerabilities in scan targets.
No CVE finding or PostgreSQL TLS key is covered by this review.

The machine-readable [review](feed-secret-review.json) records every exact path,
rule, line, verified file SHA256 and rationale. It applies only to
`registry.community.greenbone.net/community/vulnerability-tests@sha256:e0f85aca0d448653cb8da8e1065c374cb7239bd8d902b988f2b3d4b924eae583`
on linux/amd64, and expires **12 November 2026 at 00:00 UTC**. Changed rules,
paths or line numbers require new review. A newer digest of the same feed
repository qualifies only file by file: CI pulls that exact image, copies out the
flagged files without starting it, and grants a disposition only where the
recomputed full-file SHA256 equals the reviewed hash. Changed or new files, other
repositories and any hashing failure stay blocking. A registry tag or path pattern
alone never grants a disposition. This is a test-data classification, not
acceptance of an application vulnerability or a credential leak.

## Verification

The OCI index/manifest and all four compressed image layer hashes were verified
against their registry SHA256 digests. Only the flagged regular files were read
from the tar archives; no image or NASL code was executed. Each file's complete
bytes were hashed. All flagged files occurred in layer
`sha256:7b5fae2c3ea64e54582ddb038490eba763b3b83548716c846a4982b164beb338`.
Matched key/token values are excluded from this document and published evidence.

The feed image changed after the first review of digest `c165a094...`. The new
index/manifest and all four compressed layers were independently verified. All
28 flagged files have exactly the previously reviewed full-file SHA256 hashes;
all 72 paths, rules and line numbers are unchanged. The review was renewed for
the new exact image digest without extending its expiry or changing its scope.

On 14 October the digest changed again, from `3439f12d...` to `bc7d28cf...`.
The new OCI index/manifest and all four compressed layers were verified against
their registry digests. Every one of the 28 full-file hashes and all 72
path/rule/line matches equal the previous review. Only the exact image and
verified layer identities and review date changed; expiry remains 12 November.
That renewal was not used: the feed changed again, to `42410b5a...` and then
`e0f85aca...`, before a scan could confirm it.

On 16 October assessment 38985470580 resolved the feed to `e0f85aca...`. Its 72
detections have exactly the reviewed path/rule/line set. The index, amd64
manifest, config and all four compressed layers were downloaded and verified
against their registry digests. All 28 flagged files are regular files in layer
`7b5fae2c...`, and every full-file SHA256 equals the review. Only the exact image,
verified layer and review date changed; expiry remains 12 November. A subsequent
scan must confirm use of this review.

| File within each feed's NASL directory | Matches per copy | Evidence from file contents |
| --- | ---: | --- |
| `2012/gb_f5_big_ip_K13600.nasl` | 1 | CVE-2012-1493 active SSH check passes the known product key to `ssh_login`. |
| `2014/gb_loadbalancer_org_enterprise_va_static_ssh_key.nasl` | 1 | Static SSH key vulnerability check passes the product key to `ssh_login`. |
| `2014/gb_quantum_dxi_ssh_root_auth_bypass_vuln.nasl` | 1 | Root authentication-bypass check passes the product key to `ssh_login`. |
| `2016/gb_exagrid_default_ssh_login.nasl` | 1 | Known private key active check uses the key as an SSH probe. |
| `2016/vmware/gb_vmware_vsphere_data_protection_admin1_known_ssh_key.nasl` | 2 | CVE-2016-7456/VMSA-2016-0024 check uses known product keys in SSH probes. |
| `2017/gb_logrhythm_netmon_auth_bypass_vuln.nasl` | 1 | Comments at lines 66–67 identify an advisory-example forged JWT; the HTTP request uses it to test static-key authentication bypass. |
| `bad_ssh_keys.inc` | 11 | Comments identify an unused control key and public known-bad/example keys, citing Rapid7, example RSA code and the SSH badkeys collection. |

## Enforcement and evidence

The CI scanner retains all 72 sanitized findings. SARIF carries explicit external
accepted suppressions and false-positive review metadata; JSON reports retain
the classification, exact image digest, file hash, rationale and expiry. Scan
status records both the reviewed count and the review manifest hash. The evidence
bundle includes the complete review manifest under its SHA256 checksum list.

All other secrets and HIGH/CRITICAL CVEs remain blocking, including unfixed CVEs.
Invalid review manifests fail the scan. Unit tests cover digest/path/line/rule
drift, other images, CVEs, expiry, invalid reviews and replayed dispositions.

## PostgreSQL service keys

The original PostgreSQL server and migrator images contained a shared
`/etc/ssl/private/ssl-cert-snakeoil.key`. Those service keys were never covered
by this review. Both [managed rebuilds](../scanner/image_builds/README.md) now
remove the bundled key and generate a unique runtime key. Their fresh CI scans
report zero secret detections; residual vulnerabilities remain blocking.
