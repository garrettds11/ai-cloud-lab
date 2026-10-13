# Managed scanner image builds

`manifest.json` identifies exact upstream references replaced in Greenbone's
Compose definition. Each replacement uses a reviewed, digest-pinned base and a
small local build context. Unknown Dockerfiles and missing/changed upstream
references fail inventory or preparation instead of silently losing coverage.

## PostgreSQL

The `pg-gvm` recipe installs available updates from the upstream image's signed
Debian repositories without changing the PostgreSQL major version. It removes
the upstream baked-in snakeoil TLS key and certificate. Its entrypoint generates
a unique RSA key/certificate on first container start, restricts key permissions
to root and the upstream `ssl-cert` group, then runs the original entrypoint.
The upstream command, health check, database volumes and Greenbone extensions
are retained. No production database migration was performed by this change.
The paired migrator now has its own managed rebuild from a coordinated pinned
base. Available patches update its PostgreSQL 13–17 binaries without changing
the default target major (17). Its wrapper generates a unique runtime TLS key
before dispatching the original `tini` and upgrade command, so old clusters that
reference Debian's certificate paths can still start for recovery. The shared
upstream private key is absent from the final image.

The self-signed certificate is for the internal scanner database. It does not
establish a public trust chain or add a public database endpoint. Container
recreation generates another fresh key; normal restarts retain the existing key.

## Scanner services and tools

`redis-server`, `gvm-tools`, `ospd-openvas` and `openvas-scanner` receive available Debian
package updates from digest-pinned bases. Redis returns to its original `redis`
user after the build. The tools and OSPD retain their upstream entrypoints,
which select the `gvm` and `ospd-openvas` service users. Scanner capability
handling is inherited because raw network checks require it. These recipes do
not claim a complete container configuration hardening baseline.

The previously assessed OSPD digest was no longer available upstream on 13
October. Its replacement, version 22.10.6 with OpenVAS 23.51.0, is recorded in
the manifest and independently assessed; comparisons with the previous CI
report include both upstream changes and the additional package updates.

## CI and runtime

CI inventories the recipe and complete build context, then rebuilds with
`--no-cache --pull`, exports the resulting image and scans its static archive.
Trivy receives no Docker socket or build credentials. Sanitized SARIF/JSON and
build metadata enter the existing evidence/attestation workflow. The report
records the reviewed base, recipe hashes, built image ID and archive SHA256.
Build failures remain blocking. The upstream base is recorded as a build input;
the final image is the deployable asset assessed by the gate.

Terraform uploads a checksum-verified ZIP of the build contexts plus the builder
to the scanner's existing private assets bucket. At scan time the instance builds
the recipes, rewrites each managed Compose reference to its built image ID and
scans a static archive of each exact runtime image for vulnerabilities.
`scanner-image-builds.json` and `scanner-image-<context>.json` are published with run
reports. Building neither starts PostgreSQL nor reads AWS credentials into the
build. Greenbone images without a managed recipe continue to use their upstream
references.

CI and runtime resolve repository updates independently. They can produce
different image IDs; runtime reports identify the actual deployed rebuild.
Updating the pinned base or recipe requires a fresh image scan and database
startup/extension checks. Upstream application updates are not substituted by
OS package updates, and residual unfixed CVEs remain blocking.

## Verified result, 13 October 2026

The final recipe was built and scanned locally with Trivy 0.75.0 and the current
vulnerability database. HIGH/CRITICAL CVE instances fell from **212 to 74**;
secret detections fell from **one to zero**. None of the remaining 74 CVE
instances had a reported fixed version. Counts must be refreshed by CI.

PostgreSQL **17.11**, TLS enabled, and `pg-gvm`, `pgcrypto`, `plpgsql` and
`uuid-ossp` extensions were verified in a network-isolated container. Separate
fresh containers produced different certificate fingerprints. Compose startup
using the immutable built image ID also passed. These checks do not establish
acceptance of the entire Greenbone stack or compatibility with every legacy
database migration path. Residual image findings remain blocking.

The patched migrator additionally passed a network-isolated PostgreSQL 13-to-17
upgrade using disposable data. A test row and the `pgcrypto` extension survived
the upgrade, and the health marker was written. Redis passed a Unix-socket PING
as its non-root service user; `gvm-tools` passed its CLI/import checks as `gvm`.
OpenVAS/OSPD passed version and import checks as `ospd-openvas`, sharing only a
Redis Unix socket with another network-isolated container. These checks do not
replace a complete feed-backed Greenbone acceptance scan.

Additional local Trivy 0.75.0 scans of the final static image archives, using the
13 October vulnerability database, produced the following blocking counts:

| Image | Previous CI | Patched image | Removed CVE/package pairs | Secrets remaining |
|---|---:|---:|---:|---:|
| PostgreSQL migrator | 202 | 78 | 123 | 0 |
| OSPD/OpenVAS | 397 | 177 | 220 | 0 |
| Redis | 75 | 52 | 23 | 0 |
| gvm-tools | 73 | 69 | 4 | 0 |
| Standalone OpenVAS | 178 | 177 | 1 | 0 |

The migrator's previous count included one shared-key secret detection. This
iteration removes 371 HIGH/CRITICAL CVE/package pairs and that secret detection;
no new HIGH/CRITICAL pairs were observed in these comparisons. OSPD's reduction
includes the upstream replacement described above. All remaining findings stay
blocking. CI must refresh these counts and publish the final SARIF evidence.
The standalone OpenVAS and openvasd binaries also passed their version checks
with an isolated Redis socket. None of the residual HIGH/CRITICAL findings in
these five patched images reports an available fixed version in this scan.

ECR publication of approved artifacts and patched Ubuntu AMI automation are
tracked in [issue #90](https://github.com/garrettds11/ai-cloud-lab/issues/90).
CI and runtime still build independently until that follow-up is implemented.
