# Open WebUI vulnerability scanner

This is a separate Terraform stack for issue 82. It creates a normally stopped
Greenbone Community Edition EC2 instance, a dedicated security VPC, private
peering to the lab, encrypted report storage, a demand queue, and a controller.
The controller wakes both instances when needed. The runner assesses the deployed
control-panel Lambda ZIPs/layers and live AWS configuration, scans the exact
Open WebUI image digest with Trivy, then runs Greenbone against the private host,
the public Open WebUI hostname and `cp.aiwebdemo.click`. Greenbone's **Full and fast**
profile excludes invasive and denial-of-service tests. The controller publishes
success or failure to SNS and stops the scanner when it started it.

The five-minute EventBridge rule checks whether seven days have passed since the
**last successful** run; its five-minute tick also advances long SSM commands.
Any message on the SQS queue requests an immediate run. A request received
during a run is held for the next run. A failed run leaves `last_success` alone
and retries after six hours; another SQS request can retry immediately. Each
run gets a UUID and S3 prefix.
Send an SQS request after each Open WebUI image deployment and after firewall
or proxy changes; the weekly interval is the fallback cadence.

## Before deployment

1. Pin `open_webui_container_image` in the lab stack to an OCI digest and apply
   the lab. The scanner verifies that the running container uses this exact
   digest before it starts. This prevents a report for a tag that moved.
2. Create a dedicated SSH key for `svc_vulnscan`. Put **only the private key** in
   Secrets Manager as a plaintext secret; supply the public key to Terraform.
   Use this key nowhere else. The lab account is created by SSM with a locked
   password, key-only SSH and no sudo. The private key never enters Terraform
   state, user data or SSM parameters.
3. Create a second plaintext Secrets Manager secret with a strong, single-line
   Greenbone admin password. The first scan replaces Greenbone's initial
   `admin/admin` password. Both secrets must permit the scanner instance role
   to call `GetSecretValue` (and `kms:Decrypt` if using a customer KMS key).
4. Get the lab instance ID, private IP, VPC ID, security group ID, and every
   route table used by the lab subnet. Get the control-panel events table name
   from `dashboards/api/terraform` output `tables.events`. Routes in both VPCs
   and the lab SSH security-group rule are managed by this stack.
5. Review `scanner.tfvars.example`, use your own non-overlapping scanner VPC
   CIDR, and set a remote Terraform backend for this separate state before
   deploying. `terraform plan` will show an EC2 instance, S3 bucket, SQS queue,
   SNS topic, Lambda, peering routes and IAM roles. EBS/report storage costs persist
   while stopped; EC2 compute and its automatically assigned public IPv4 are
   charged while running.
6. Set `control_panel_functions` from the API stack's new `scanner_functions`
   output (customer, admin and spend). Supply `control_panel_api_id` and the
   **active** JWT issuer/audiences; the holding pool is intentionally not the
   expected active-lab baseline. Supply exact layer-version ARNs and attached
   managed-policy/permissions-boundary ARNs if present. Missing permissions or
   unlisted deployed layers/policies make coverage incomplete. The scanner reads
   only the configured functions, roles, policies, layers and HTTP API; it has
   no Lambda invocation or IAM/configuration write permissions for assessment.
   This deployment supports the same account and region as the lab/API.

```powershell
cd scanner
Copy-Item scanner.tfvars.example scanner.auto.tfvars
# Edit scanner.auto.tfvars with the actual values, then:
terraform init
terraform plan
terraform apply
```

The scanner's first boot installs Docker and AWS CLI and downloads the versioned
runner from its private S3 bucket. The first due run begins after deployment
and then stops the scanner. Its Docker volumes and feeds remain on encrypted
EBS across stops. Changes to bootstrap/scripts or assessment settings replace
the scanner instance and root disk, resetting local feeds/history; S3 reports
remain. The initial feed load can take
hours. Greenbone's upstream Community Container guide calls this deployment
appropriate for familiarization, not a supported production installation.
Treat its results as lab security data and validate the first live scan before
relying on it for enforcement.

## Request, monitor and retrieve

```powershell
$queue = terraform output -raw request_queue_url
aws sqs send-message --queue-url $queue --message-body '{"scan":true}'
aws dynamodb get-item --table-name ai-lab-scanner-state --key '{"pk":{"S":"scanner"}}'
$bucket = terraform output -raw report_bucket
aws s3 ls "s3://$bucket/runs/" --recursive
```

Subscribe an operator to `results_topic_arn` if email notifications are wanted.
The S3 prefix contains `host.xml`, `public.xml`, `control-panel.xml`,
`greenbone.json`, `trivy.json`, `aws-assessment.json`,
`lambda-<name>-code-trivy.json`, one `lambda-<name>-layer-<index>-trivy.json`
per layer, and `manifest.json` on success. Greenbone also exports PDF and
CSV when those report formats are available; `greenbone.json` records which
formats succeeded. Failed runs send SNS and leave
the last successful timestamp unchanged. Check the controller CloudWatch log
group, the scanner's SSM command, and `/var/log/ai-scanner-bootstrap.log` for
setup failures. The Greenbone UI is bound to localhost by the upstream Compose
file and the scanner security group has no inbound rules. Administer through
SSM Session Manager if needed.
Reports expire after 365 days; noncurrent report/script versions expire after
30 days. VPC flow logs and controller logs retain 30 days of operational evidence.
The VPC's default security group is closed. Failed SQS requests and exhausted
asynchronous controller events go to `dead_letter_queue_url`; inspect that queue
when a demand signal receives no report. It is retained for 14 days and does not
automatically notify an operator.

## Assessment evidence and limits

`aws-assessment.json` records `coverage` separately from PASS/FAIL/REVIEW checks.
A completed assessment can contain vulnerabilities or failed configuration
checks. SNS success means the assessment executed; it does **not** mean the
system is vulnerability-free. Missing targets, denied reads, changed function
revisions, bad ZIP hashes and failed artifact scans make coverage incomplete,
fail the run and leave the weekly success clock unchanged. Sanitized partial
reports are uploaded before Greenbone starts and again if a later step fails.

For each function, the runner downloads the deployed `$LATEST` ZIP and exact
configured layer versions, verifies their AWS `CodeSha256` values, rejects unsafe
ZIP paths/symlinks and scans each extracted artifact read-only with Trivy rootfs.
It records function/version/revision, artifact hashes, package counts and report
names, then checks that the function revision/hash/layers did not change. It
never executes downloaded code. Artifacts and raw secret scan output are removed;
reports retain secret rule/severity/file/line only, without matched values or
source excerpts. Environment variables and signed download URLs are not exported.

The live checks cover:

- Reviewed runtime allowlist (default Python 3.12), automatic runtime updates,
  expected execution role, absence of function URLs and narrowly scoped invocation
  policies (admin limited to this API's admin paths; spend to its schedule).
- Execution-role trust, inline/attached policies and permissions boundaries.
  Wildcard actions and common privilege escalation grants fail; wildcard resource
  grants require review. This is a policy heuristic, not an effective IAM access
  simulation; SCPs and other resource/session policies remain outside this scope.
- HTTP API routes and customer/admin integrations matching the repository route
  inventory, exact JWT issuer/audiences/header source, exact CORS origin/methods/
  headers, stage inventory, auto-deployment, access logging and positive rate/burst
  limits including route overrides. Route inventory is generated from the API
  stack's `api.tf`; update the scanner baseline after API changes. CORS is a browser
  restriction, not an authorization mechanism.

The API ZIP currently contains source only, with no bundled third-party packages.
`no_identified_packages` explicitly records this limited package coverage instead
of implying all runtime software was assessed. AWS's managed OS/runtime/SDK are
not in that ZIP. AWS manages runtime patches; the allowlist is a reviewed baseline,
not an automatically maintained runtime deprecation catalogue. Inspector has the
same managed-SDK exclusion and is not enabled by this stack.
Artifact and invocation-policy assessment covers the explicitly configured
unqualified functions (`$LATEST`), which the current API integrations use. Other
published versions/alias policies and account-wide IAM callers are outside the
inventory. Unexpected API integrations are findings instead of implicitly
authorizing new code downloads.

Public scans see the exposed proxy/CDN services. They do not prove application
authorization or assess every origin component. Authenticated customer/admin and
cross-customer API tests remain a separate next layer; this runner does not
perform state-changing API operations or use an operator's login credentials.

## Offline verification

```powershell
python -m unittest discover -s scanner -p 'test_*.py' -v
terraform -chdir=scanner init -backend=false
terraform -chdir=scanner validate
terraform -chdir=scanner test
```

CI validates the separate scanner stack and runs its offline Python and mocked
Terraform tests. No live scan or AWS deployment is performed by those tests.

The scanner never calls an LLM API. An unauthenticated external scan therefore
uses zero Open WebUI tokens. When it wakes a stopped lab, it records a start in
the control-panel events table under `scanner:greenbone`, so a scan-only session
is charged to the scanner identity. If people use the lab during the scan,
their actual model tokens remain attributed to their normal Open WebUI users.
The controller refreshes the lab's existing idle clock every five minutes while
a scan runs. If the controller stops, the normal idle timer resumes by itself;
hard and absolute uptime limits still apply. After the scan, the normal
idle-stop policy handles the lab so a newly active person is not disconnected.
Set hard and absolute uptime limits longer than the expected scan window, or a
scan will correctly fail when the lab stops.

Sources: [Greenbone Community Containers](https://greenbone.github.io/docs/latest/22.4/container/),
[Greenbone `Full and fast` safety description](https://docs.greenbone.net/GSM-Manual/gos-24.10/en/scanning.html),
[gvm-tools scripting](https://greenbone.github.io/gvm-tools/scripting.html),
[AWS Systems Manager command events](https://docs.aws.amazon.com/systems-manager/latest/userguide/monitoring-systems-manager-event-examples.html),
[Trivy Python package coverage](https://trivy.dev/docs/latest/coverage/language/python/),
[Lambda runtime responsibilities](https://docs.aws.amazon.com/lambda/latest/dg/runtime-management-shared.html),
[Inspector Lambda coverage](https://docs.aws.amazon.com/inspector/latest/user/scanning_resources_lambda.html).
