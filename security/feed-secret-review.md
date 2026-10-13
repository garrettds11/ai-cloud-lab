# Greenbone feed secret review

Reviewed 13 October 2026 against completed assessment
[38342215823](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38342215823).

## Disposition

The **72 secret detections in 28 files** are intentional vulnerability-test inputs,
duplicated across feed directories 21.04, 22.04, 24.10 and 25.0. They are false
positives for leaked lab/scanner credentials. Their presence is required to detect
known shared-key and authentication-bypass vulnerabilities in scan targets.
No CVE finding or PostgreSQL TLS key is covered by this review.

The machine-readable [review](feed-secret-review.json) records every exact path,
rule, line, verified file SHA256 and rationale. It applies only to
`registry.community.greenbone.net/community/vulnerability-tests@sha256:c165a094faa041b30f66f20b1a7966f8f411e05347487c4a20262da4755c3da0`
on linux/amd64, and expires **12 November 2026 at 00:00 UTC**. Changed image digests,
rules, paths or line numbers require new review. A registry tag or path pattern
alone never grants a disposition. This is a test-data classification, not
acceptance of an application vulnerability or a credential leak.

## Verification

The OCI index/manifest and all four compressed image layer hashes were verified
against their registry SHA256 digests. Only the flagged regular files were read
from the tar archives; no image or NASL code was executed. Each file's complete
bytes were hashed. All flagged files occurred in layer
`sha256:f63f3a554c6203a31c7be7a9895b79f022e7e60ba2e079263c57f049ec08758b`.
Matched key/token values are excluded from this document and published evidence.

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

## PostgreSQL keys remain open

Both PostgreSQL images contain `/etc/ssl/private/ssl-cert-snakeoil.key`.
These are actual bundled service-key material, unlike the feed test payloads.
They remain blocking pending an image rebuild that removes the shared key and
uses a newly generated key if TLS is required. Suppressing the filename would
hide a different class of risk and is not justified by this feed review.
