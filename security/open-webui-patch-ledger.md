# Open WebUI: verification of the 27 reported fixes

## Assessment evidence

[Complete assessment 38576332719](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38576332719). All source evidence checksums verified before generating this ledger. Original and candidate were scanned in the same assessment.

Assessment commit: `b39305cc694abffea06f2fa79a5ec4747cdc75ed`.

Original target: `image-2d717189fe5995b3f7c5`. Candidate target: `image-0c400ab58c98d07db992`.

Assessed candidate image ID: `sha256:28e44fd127f74b3cda81856a2028c85bf53e4291e99f50385d22ce73b3954df1`.

**27 of 27 finding IDs are absent in the candidate scan across all severities.** Detected candidate package versions are below; vendored copies are included in the scan. This verifies the candidate patches. The configured original image still contains these findings. No deployment or promotion has occurred.

**Merge remains blocked:** the candidate still has 277 HIGH/CRITICAL finding occurrences; configured assets have 1,386. These are package finding occurrences, not asset counts. An absent finding does not prove a live deployment was patched.

| Finding | Package | Original version | Reported fixed version | Detected candidate versions | Verification |
|---|---|---|---|---|---|
| [CVE-2026-69247](https://avd.aquasec.com/nvd/cve-2026-69247) | cryptography | 48.0.0 | 50.0.0 | 50.0.0 | Absent in candidate |
| [CVE-2026-69249](https://avd.aquasec.com/nvd/cve-2026-69249) | cryptography | 48.0.0 | 49.0.0 | 50.0.0 | Absent in candidate |
| [GHSA-537c-gmf6-5ccf](https://github.com/advisories/GHSA-537c-gmf6-5ccf) | cryptography | 48.0.0 | 48.0.1 | 50.0.0 | Absent in candidate |
| [CVE-2026-104851](https://avd.aquasec.com/nvd/cve-2026-104851) | fsspec | 2025.3.0 | 2026.6.0 | 2026.6.0 | Absent in candidate |
| [CVE-2026-23949](https://avd.aquasec.com/nvd/cve-2026-23949) | jaraco.context | 5.3.0 | 6.1.0 | 6.1.0 | Absent in candidate |
| [CVE-2026-59950](https://avd.aquasec.com/nvd/cve-2026-59950) | mcp | 1.27.2 | 1.28.1 | 1.28.1 | Absent in candidate |
| [CVE-2026-25087](https://avd.aquasec.com/nvd/cve-2026-25087) | pyarrow | 20.0.0 | 23.0.1 | 24.0.0 | Absent in candidate |
| [CVE-2026-102266](https://avd.aquasec.com/nvd/cve-2026-102266) | PyJWT | 2.13.0 | 2.14.0 | 2.14.0 | Absent in candidate |
| [CVE-2026-102267](https://avd.aquasec.com/nvd/cve-2026-102267) | PyJWT | 2.13.0 | 2.14.0 | 2.14.0 | Absent in candidate |
| [CVE-2026-102268](https://avd.aquasec.com/nvd/cve-2026-102268) | PyJWT | 2.13.0 | 2.14.0 | 2.14.0 | Absent in candidate |
| [CVE-2026-102271](https://avd.aquasec.com/nvd/cve-2026-102271) | PyJWT | 2.13.0 | 2.14.0 | 2.14.0 | Absent in candidate |
| [CVE-2026-102272](https://avd.aquasec.com/nvd/cve-2026-102272) | PyJWT | 2.13.0 | 2.14.0 | 2.14.0 | Absent in candidate |
| [CVE-2026-102273](https://avd.aquasec.com/nvd/cve-2026-102273) | PyJWT | 2.13.0 | 2.14.0 | 2.14.0 | Absent in candidate |
| [CVE-2026-102993](https://avd.aquasec.com/nvd/cve-2026-102993) | pypdf | 6.7.5 | 6.17.0 | 6.19.0 | Absent in candidate |
| [CVE-2026-102994](https://avd.aquasec.com/nvd/cve-2026-102994) | pypdf | 6.7.5 | 6.18.0 | 6.19.0 | Absent in candidate |
| [CVE-2026-102995](https://avd.aquasec.com/nvd/cve-2026-102995) | pypdf | 6.7.5 | 6.18.1 | 6.19.0 | Absent in candidate |
| [CVE-2026-102996](https://avd.aquasec.com/nvd/cve-2026-102996) | pypdf | 6.7.5 | 6.18.1 | 6.19.0 | Absent in candidate |
| [CVE-2026-102997](https://avd.aquasec.com/nvd/cve-2026-102997) | pypdf | 6.7.5 | 6.18.1 | 6.19.0 | Absent in candidate |
| [CVE-2026-102998](https://avd.aquasec.com/nvd/cve-2026-102998) | pypdf | 6.7.5 | 6.19.0 | 6.19.0 | Absent in candidate |
| [CVE-2026-102999](https://avd.aquasec.com/nvd/cve-2026-102999) | pypdf | 6.7.5 | 6.19.0 | 6.19.0 | Absent in candidate |
| [CVE-2026-103000](https://avd.aquasec.com/nvd/cve-2026-103000) | pypdf | 6.7.5 | 6.19.0 | 6.19.0 | Absent in candidate |
| [CVE-2026-59935](https://avd.aquasec.com/nvd/cve-2026-59935) | pypdf | 6.7.5 | 6.14.2 | 6.19.0 | Absent in candidate |
| [CVE-2026-59936](https://avd.aquasec.com/nvd/cve-2026-59936) | pypdf | 6.7.5 | 6.14.1 | 6.19.0 | Absent in candidate |
| [CVE-2026-68770](https://avd.aquasec.com/nvd/cve-2026-68770) | sentence-transformers | 5.5.1 | 5.6.0 | 5.6.0 | Absent in candidate |
| [CVE-2026-9856](https://avd.aquasec.com/nvd/cve-2026-9856) | transformers | 5.5.4 | 5.10.0 | 5.10.1 | Absent in candidate |
| [CVE-2026-71428](https://avd.aquasec.com/nvd/cve-2026-71428) | unstructured | 0.22.31 | 0.24.0 | 0.24.0 | Absent in candidate |
| [CVE-2026-24049](https://avd.aquasec.com/nvd/cve-2026-24049) | wheel | 0.45.1 | 0.46.2 | 0.46.2, 0.46.3 | Absent in candidate |

## Acceptance still required

- The new Chroma storage/query compatibility check must pass in CI. Existing dependency, backend and authentication checks passed in the complete assessment above.
- Full retrieval/model/MCP integration and feed-backed Greenbone acceptance remain pending.
- Residual findings require patches or individual evidence-backed mitigation review; no blanket exceptions have been added.
- Promotion needs a retained, reviewed registry digest and deployment verification. Issue #90 tracks registry and AMI work.

## Reproduce the comparison

Download and extract the linked run's `security-evidence` artifact. Verify `SHA256SUMS`, select the original HIGH/CRITICAL rows with nonempty `FixedVersion` from `reports/image-2d717189fe5995b3f7c5/findings.json`, and compare their IDs against every vulnerability in `reports/image-0c400ab58c98d07db992/findings.json`. Detected package versions come from the candidate `status.json` package inventory. This ledger is a historical comparison, not an automatic waiver or the latest-run assessment.
