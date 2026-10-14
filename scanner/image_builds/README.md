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

## Socket-only service isolation

Before startup, the runner normalizes Compose through Docker and verifies the
expected named volumes connect PostgreSQL to gvmd, Redis to OSPD, the migrator
to the PostgreSQL data/socket, and gvm-tools to the manager's GMP socket. Changes
to those paths, unexpected published ports or privileged service settings fail
preparation instead of silently applying an incompatible policy.

PostgreSQL, its migrator, Redis and gvm-tools run with `network_mode: none`,
`no-new-privileges:true` and `NET_RAW` dropped. Their Unix socket/data volumes
remain available. Redis also receives a read-only root filesystem and a limited
temporary filesystem; its socket and image-declared data volume remain writable.
OpenVAS/OSPD retain their required scanning network/capabilities; manager and web
service network settings are preserved. The runner publishes only the applied
control settings in `scanner-hardening.json`, without exporting Compose secrets.

These controls reduce access and escalation paths; they do not patch a CVE or
automatically disposition a finding. They require deployment and a full scan to
establish operational acceptance.

## Patched Trivy tool

Trivy's Go standard library and `golang.org/x/net` vulnerabilities require a
binary rebuild. The tool recipe retains release 0.75.0 at upstream commit
`591e9799316a602e703f0b484f6c6d7b234ec8f3`, verifies the complete source archive
SHA256, builds with Go 1.27.2 and x/net 0.60.0, verifies Go modules, and removes
compiler/source/cache packages from the final image. Build metadata remains in
`/usr/local/share/trivy-build.txt`. This is a custom patched build, not a new
upstream release. Its final archive still must pass vulnerability assessment.

Alpine package updates also address reported OS fixes. A linked-package list is
recorded in `/usr/local/share/trivy-packages.txt`; the build rejects the
unmaintained `golang.org/x/crypto/openpgp` package and its subpackages. Go advisory
[GO-2026-5932](https://pkg.go.dev/vuln/GO-2026-5932) affects those specific APIs,
not every package in the x/crypto module. A module-level UNKNOWN finding may
remain visible in Trivy's report; this guard does not suppress that finding.

CI builds the tool once in the inventory job and distributes a checksummed image
archive with its inputs. Each job verifies the archive hash and loaded image ID
before execution. The tool's own assessment uses that exact same archive without
rebuilding it. The scanner container retains its non-root user, read-only source
mount and absence of Docker socket/AWS credentials. A missing or altered tool
artifact fails the scan; there is no fallback to the unpatched upstream binary.

The deployed runner builds the same recipe before any assessment, uses its
immutable ID for all Trivy scans, and assesses the exact runtime tool archive
alongside the other managed images. `scanner-tool-build.json` records the tool
identity even when later assessments fail. ECR promotion remains tracked in #90.

## Open WebUI dependency candidate

The `open-webui` context is a candidate build from the assessed v0.12.0 digest.
It pins the reported fixed versions for PyJWT, cryptography, fsspec,
jaraco.context, MCP, PyArrow, pypdf, sentence-transformers, transformers,
unstructured and wheel. Binary-only package installation and `pip check` reject
unavailable wheels or dependency conflicts. Offline smoke checks exercise JWT,
crypto, in-memory storage, Arrow and PDF APIs and import the NLP/MCP APIs without
model downloads or persistent account/database state.

After building, CI starts the candidate without networking or host mounts, with
CPU/memory limits and disposable data. It checks backend health, signup, signin
and authenticated token use with a public test-only identity/key. It then removes
the container and its anonymous volumes, retaining only pass/fail metadata. The
tested image itself is unchanged; no test database or credentials are baked in.

CI assesses this candidate separately. It does **not** replace the original
configured Open WebUI image in inventory, and the Greenbone runner never builds
or deploys it. Reports label it `candidate` to prevent interpreting its results
as remediation of an unchanged live deployment. Retrieval and MCP registration
acceptance plus approved ECR publication in
#90 are required before promotion. No candidate is automatically approved or
exempted from the vulnerability policy.
