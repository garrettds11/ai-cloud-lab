# Merge readiness and finding remediation

Assessment: [run 38342215823](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38342215823), reviewed 13 October 2026.
Coverage is complete for the committed tree: 21 images, four Lambda ZIPs and source.
There are 2,029 blocking finding instances, not 2,029 unique vulnerabilities.
The verified [feed secret review](feed-secret-review.md) classifies 72 test-data
detections as false positives for this exact immutable image. Replaying this
assessment with the review leaves **1,957 blocking instances**: 1,955 CVEs and two
PostgreSQL service-key detections. A new CI run must confirm the current digests
and findings before using that count for a merge decision.

[Run 38352152872](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38352152872)
confirmed complete coverage, 72 reviewed false positives and 1,957 blockers.
The next change adds a [managed PostgreSQL rebuild](../scanner/image_builds/README.md).
Local verification reduced its 213 blockers to 74: **138 CVE instances fixed and
one shared private key removed**. Applying that delta to the previous assessment
would leave 1,818 blockers; fresh CI results must establish the actual total.

## Corrections in this PR

- **CKV_AWS_355 / Grafana IAM:** restrict `logs:GetQueryResults` to the lab's Lambda log groups;
  restrict `cloudwatch:DescribeAlarms` to the lab's alarm names. Metric/list actions
  without IAM resource-level support retain their required wildcard scope.
- **Terraform null handling:** use a conditional before numeric comparisons on the
  nullable hourly price. A logical `||` does not protect those comparisons.
- **Grafana external-ID validation:** separate the allowed-character regex from length
  checks. Terraform's regex engine rejects repetition counts above 1,000, so the
  previous `{2,1224}` expression rejected valid IDs.
- **Usage test fixture:** install an executable copy of the real session helper, as
  cloud-init does, so the session assertion exercises the integration on CI.
- **API tests:** compare deployed routes against the independent OpenAPI contract
  instead of the stale count of fifteen; evaluate computed IAM policies after a
  mocked apply and supply valid fake AWS ARNs. All providers remain mocked.

## Image findings that still require remediation

| Image | HIGH/CRITICAL CVE instances | Detected secrets | CVE instances with a reported fix |
| --- | ---: | ---: | ---: |
| Open WebUI v0.12.0 | 508 | 0 | 27 |
| Trivy 0.75.0 | 4 | 0 | 4 |
| Greenbone gvm-tools | 73 | 0 | 4 |
| Greenbone gvm-config | 43 | 0 | 0 |
| Greenbone gvmd | 123 | 0 | 0 |
| Greenbone gsad | 71 | 0 | 0 |
| Greenbone nginx | 70 | 0 | 0 |
| Greenbone pg-gvm-migrator | 201 | 1 | 123 |
| Greenbone pg-gvm (previous upstream image) | 212 | 1 | 138 |
| Greenbone ospd-openvas | 397 | 0 | 220 |
| Greenbone redis-server | 75 | 0 | 23 |
| Greenbone openvas-scanner | 178 | 0 | 1 |
| Greenbone vulnerability-tests feed | 0 | 72 | 0 |

“Reported fix” means the vulnerability database supplied a fixed version. It does
not establish that an upstream container release includes that version or that
an in-place dependency upgrade is compatible.

Official latest-release checks on 12 October still show Open WebUI v0.12.0 and
Trivy v0.75.0. Updating these version tags alone does not resolve the findings.

### Next remediation work

1. **Open WebUI:** prioritize compatible updates to PyJWT, cryptography, fsspec,
   jaraco.context, mcp, pyarrow, pypdf, sentence-transformers, transformers,
   unstructured and wheel. Use a verified upstream rebuild or a maintained image
   build with dependency-resolution and application regression tests. Source-only
   changes in this repo cannot patch packages inside the upstream image.
2. **Trivy:** use an upstream release rebuilt with patched Go/x/net, or build and
   test an explicitly maintained scanner image. Do not silently switch to canary.
3. **Greenbone:** review updated upstream image builds and scan their exact digests.
   PostgreSQL now uses a tested managed rebuild with Debian updates and unique
   runtime TLS keys; its local rescan has 74 unfixed CVEs and zero secrets.
   Prioritize the migrator, ospd-openvas and Redis fixable instances. Unfixed findings
   need vendor/reachability review and an explicit disposition.
4. **Secret detections:** the 72 feed detections are now reviewed with verified
   file hashes and exact digest/path/rule/line dispositions, expiring 12 November.
   The PostgreSQL server's bundled `ssl-cert-snakeoil.key` is removed in the
   managed rebuild and generated uniquely at startup. The separate migrator
   image's bundled key remains blocking and needs its own rebuild.
   Matching values remain
   redacted from published evidence.

The gate remains blocking. No vulnerability baselines, ignore-unfixed option,
blanket secret suppression or CVE risk acceptances have been added. Merge requires
remediation or reviewed, explicit exceptions for the residual blockers.

Dockle can be tracked as a separate optional image-hardening follow-up. It does
not resolve these package CVEs and is not required to fix the present blockers.
