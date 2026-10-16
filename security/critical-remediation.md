# Critical finding remediation plan — 16 October 2026

## Verified baseline and scope

[Complete assessment 38617707801](https://github.com/garrettds11/ai-cloud-lab/actions/runs/38617707801)
assessed PR head `c8f8e10840ec05ecf7b4df0da1ae6c80754d57b5` (its inventory
records GitHub's test merge revision `334ca9a96b90e5619a6e878727e25e74d1a7b3fb`).
Its `remaining-cves.csv` contains **37 CRITICAL occurrences: 27 configured and
10 candidate**, within the previously reported 1,535 HIGH/CRITICAL occurrences.
These are finding rows, not assets or demonstrated exploits. Scope remains
the same 28 assessment targets. No candidate is deployed.

## Ordered work

| Priority | Component / finding | Decision and completion evidence required |
|---|---|---|
| 1 | Chroma: CVE-2026-45829, CVE-2026-45833 | Remains blocking. Latest upstream release is still 1.5.9; no released fix verified. Review a complete upstream patch or supported vector-store replacement. Test malicious persisted embedding configuration and nested kwargs, plus normal retrieval, before claiming mitigation. |
| 1 | SQLite: CVE-2025-7458, plus HIGH CVE-2026-11822/11824 | This batch replaces the candidate's 3.40.1 library with checksum-pinned upstream 3.54.0. Requires compiled source identity, old-database compatibility, feature tests, backend/authentication acceptance, fresh SBOM/SARIF and scan comparison. No waiver or scan exclusion. |
| 2 | libxml2: CVE-2026-6653 | Nine critical occurrences across eight configured images and the candidate. Both Bookworm and Trixie remain affected. A distro migration alone is insufficient; review upstream fix/backport and XML consumer compatibility. This is the broadest shared critical component blocker. |
| 2 | Mbed TLS crypto package: CVE-2025-47917, CVE-2026-34873/34875 | The candidate and original each contain libmbedcrypto7 2.28.3. Bookworm source package is end of life. Verify installed symbols/components against upstream affected functions and version ranges; otherwise replace/rebuild affected consumers with a supported library. Do not install an incompatible new SONAME over the old one. |
| 2 | AOM: CVE-2023-6879; GLib: CVE-2026-58016 | Bookworm remains affected. A supported distro rebuild can address these, but must retain media/document processing and test those paths. Removing media packages merely to reduce counts is not approved. |
| 2 | gvmd LMDB: CVE-2019-16224/16225/16227 | Three configured occurrences. Review upstream fixed code and distro applicability, then test manager database behavior before replacing its native library. |
| 2 | gvmd OpenSSH client: CVE-2026-60002 | One configured occurrence. Establish whether the manager requires the client and verify upstream/distro patch status before upgrade or dependency removal. |
| 3 | zlib MiniZip: CVE-2023-45853 | Four occurrences across zlib1g/zlib1g-dev. Debian documents a source/binary scope mismatch for Bookworm. Needs exact-image installed-file/bundled-code evidence before a not-affected decision. Remains counted. |
| Already patched in candidate | PyJWT CVE-2026-102268; sentence-transformers CVE-2026-68770; unstructured CVE-2026-71428; linux-libc-dev CVE-2026-43185 | These four critical rows remain in the original configured image. Candidate dependency upgrades/build-package removal already eliminate them. Promotion requires review and acceptance; retaining assessment of the configured original is intentional. Header removal does not patch a host kernel. |

## SQLite implementation and limits

Initial verification run 38905953785 stopped at preflight: the upstream registry
had deleted the pinned OpenVAS base `525c48bb...`. All eleven managed bases were
checked through their registry manifests/configs; only this base was missing.
Its replacement is pinned to `77d6f443...`, created 15 October, with upstream
command `/usr/local/bin/openvasd` and no entrypoint/user override. Manifest and
config digests were independently verified. Native scanner acceptance and a
new scan remain mandatory. Any OpenVAS result change includes this upstream
replacement, not merely the SQLite patch. Registry retention remains tracked
in [issue #90](https://github.com/garrettds11/ai-cloud-lab/issues/90).

The candidate uses the existing pinned upstream Open WebUI base and its build
toolchain. `build-sqlite.py` verifies the official canonical source archive's
SHA3-256, generates the parser using the distro's SQLite feature/limit options,
compiles with those options and hardening flags, and installs a real local
`libsqlite3-0` Debian package, version `3.54.0-0ai.cloud.lab1`.

The package records its upstream URL, source ID, checksums, original options and
generated source checksum and final library checksum in
`/opt/ai-cloud-lab/sqlite/security-build.json`. Upstream's included JimTCL/Lemon
generators require no additional installed build packages. Bounded UPDATE/DELETE
syntax is functionally tested: setting its compiler macro alone on the published
amalgamation does not preserve the distro parser feature.
Candidate acceptance includes that receipt in the scan's build metadata. This
is a locally maintained upstream build, **not an official Debian security update**.
It adds ongoing maintenance responsibility until a supported base includes the fix.
The version is not relabeled in place, package metadata is retained, and no
Trivy policy or CVE disposition is changed. Distro-oriented vulnerability data
may still report these findings; the new assessment determines the actual count.

Checks cover the actual loaded library/source ID, original database reopen and
schema migration, retained features, WAL, JSON, FTS5, RTree, rollback, integrity,
Chroma add/query, and backend signup/signin/token behavior in disposable storage.
Existing production databases and full live model/retrieval integration remain
outside this candidate test. Rollback of a deployed database would require a
separate backup/restore plan before promotion.

## Primary evidence checked on 16 October

- [SQLite official download checksums](https://sqlite.org/download.html) and
  [release/source identities](https://sqlite.org/changes.html), plus the
  [parser-generation requirement for optional compile features](https://sqlite.org/compile.html#enable_update_delete_limit).
- [Debian SQLite CVE-2025-7458](https://security-tracker.debian.org/tracker/CVE-2025-7458),
  [CVE-2026-11822](https://security-tracker.debian.org/tracker/CVE-2026-11822) and
  [CVE-2026-11824](https://security-tracker.debian.org/tracker/CVE-2026-11824).
- [Chroma latest releases](https://github.com/chroma-core/chroma/releases) and
  [reviewed CVE-2026-45833 advisory](https://github.com/advisories/GHSA-36p7-vc44-83pf).
- [Debian libxml2](https://security-tracker.debian.org/tracker/CVE-2026-6653),
  [Mbed TLS](https://security-tracker.debian.org/tracker/CVE-2025-47917),
  [AOM](https://security-tracker.debian.org/tracker/CVE-2023-6879), and
  [GLib](https://security-tracker.debian.org/tracker/CVE-2026-58016).

Merge remains blocked until findings are resolved or individually reviewed with
adequate evidence under an explicitly approved policy. This plan grants no
exceptions, registry promotion, deployment or merge approval.
