# What are these containers?

The name is **pg-gvm**, not pv-gmv: **PostgreSQL for Greenbone Vulnerability
Management**. It is the scanner's database. It stores targets, tasks, scan
results and vulnerability-management data. It does not scan your applications.

An **image** is a reusable software template. A **container** is an instance of
that image, running or stopped. Tags such as `ai-cloud-lab/pg-gvm:patched` are
our rebuilt versions of upstream images. They are not separate new products.

This catalogue describes the Greenbone Compose stack used by the repo. It is
not a live inventory of your Docker Desktop or AWS deployment. The full stack
is Greenbone's upstream Community Edition architecture; several services share
one image, and feed loaders are temporary containers rather than permanent
daemons.

## Components and their jobs

| Name | Job | Expected use |
|---|---|---|
| **pg-gvm** | PostgreSQL with Greenbone database extensions; stores manager data/results. | Persistent database service; its data volume matters. |
| **pg-gvm-migrator** | Upgrades an existing PostgreSQL data directory for the supported database version. | Migration utility, not another application database. |
| **gvmd** | Greenbone Vulnerability Manager: schedules tasks, manages targets and stores/interprets results. | Persistent manager service. |
| **ospd-openvas** | Controller between the manager and the OpenVAS scanning engine; its image includes the engine it invokes. | Network scanner/control service. |
| **openvas** | Shows shared scanner configuration and tails the OpenVAS log in this Compose definition. | Logging helper; uses the openvas-scanner image. |
| **openvasd** | Runs the scanner's package-detection service in `service_notus` mode in this Compose definition. | Detection service; uses the openvas-scanner image. |
| **configure-openvas** | Prepares shared scanner configuration. | Initialization task; shares the openvas-scanner image. |
| **redis-server** | Scanner cache and coordination data. | Internal service, accessed through the shared socket. |
| **gsad** | Greenbone web/API server communicating with the manager. | Web service. |
| **gsa** | Greenbone Security Assistant frontend assets. | Supplies the web interface's files. |
| **nginx** | Serves/proxies the Greenbone web interface and API. | Web proxy; distinct from your application/control panel. |
| **gvm-tools** | Command-line utilities used by our runner to request scans and retrieve results. | Administrative utility, not a scanning engine. |
| **gvm-config** | Supplies shared Greenbone configuration. | Configuration initialization. |
| **vulnerability-tests** | NASL vulnerability-test feed: the checks OpenVAS runs against targets. | Loads feed data into shared storage. |
| **notus-data** | Package-vulnerability detection data. | Feed loader. |
| **scap-data** | Security Content Automation Protocol vulnerability/reference data. | Feed loader. |
| **cert-bund-data / dfn-cert-data** | CERT advisory/reference feeds. | Feed loaders. |
| **data-objects / report-formats** | Scan configurations, port lists and report-format definitions. | Feed/configuration loaders. |
| **gpg-data** | Keys/data used to verify feed content. | Feed verification initialization. |

The flow is: **web interface or gvm-tools → gvmd → ospd-openvas/OpenVAS →
targets**, with **pg-gvm storing manager data/results**, Redis supporting the
scanner, and feeds supplying detection/reference data.

## Other images in this change

| Image | Job | Deployment status |
|---|---|---|
| **Trivy** | Inspects images and Lambda/source package inventories for known vulnerabilities and secrets; also assesses its own exact build. | Scanner tool, distinct from the Greenbone network scanner. |
| **Open WebUI** | The application being assessed. | Assessed as the configured upstream image; the lab does not rebuild it. |

The [managed-image manifest](image_builds/manifest.json) lists the exact bases
and recipes for our rebuilt images. It is an image inventory, not an SBOM.
The [recipe documentation](image_builds/README.md) explains their patching and
hardening. Rebuilding/scanning an image does not deploy it into AWS.

## Where to find the SBOMs

Download `security-sboms` from an assessment run:

- `sboms.md`: readable asset list, purpose-independent package counts and links.
- `sbom-index.json`: target/image identity, deployment status and each SBOM hash.
- `reports/<target-id>/sbom.cdx.json`: CycloneDX 1.6 detected-package SBOM.

The same SBOMs are under `sboms/` in the checksummed `security-evidence`
archive. That archive also contains `remaining-cves.csv` and SARIF:
vulnerability findings, kept separate from the SBOM.

SBOMs describe detected packages in the exact assessed image/ZIP/source
artifact. They do not establish a complete dependency graph, inventory a live
host kernel or AWS-managed Lambda runtime, or prove a release is safe. Missing
versions are left unknown; a failed assessment is reported as unavailable.
An empty package list means no supported packages were detected, not that the
image/source has no software. See [SBOM generation](../security/README.md#software-bills-of-materials).
