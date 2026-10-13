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
The paired migrator is pinned to its assessed digest so a moving upstream tag
cannot silently change its target PostgreSQL major independently of this base.
Pinning does not remediate the migrator's existing CVEs or shared-key detection.

The self-signed certificate is for the internal scanner database. It does not
establish a public trust chain or add a public database endpoint. Container
recreation generates another fresh key; normal restarts retain the existing key.

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
the same recipe, rewrites the exact PostgreSQL Compose reference to that built
image ID and scans a static archive of the exact runtime image for vulnerabilities.
`scanner-image-builds.json` and `scanner-image-pg-gvm.json` are published with run
reports. Building neither starts PostgreSQL nor reads AWS credentials into the
build. The paired migrator uses its coordinated digest; other Greenbone images
continue to use their upstream references.

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
database migration path. The separate migrator image remains an open finding.
