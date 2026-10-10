# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has no
version numbers yet, so everything is listed under Unreleased.

## [Unreleased]

### Security
- The HTTPS listener now uses `ELBSecurityPolicy-TLS13-1-2-2021-06` (TLS 1.2 and 1.3, forward-secret
  ciphers only), and the ALB drops requests with malformed header names.
- With origin lockdown on, the optional port 80 redirect is now limited to Cloudflare addresses like
  port 443 (it was open to the whole Internet).
- The ALB's outbound rule allows only Open WebUI's port inside the VPC instead of all traffic everywhere.
- Auto-stop alert messages are encrypted at rest with the AWS managed SNS key.

### Changed
- `terraform-smoke-test-plan.md` is now only the deploy runbook (prepare, API stack, lab, checks,
  destroy) with a table of which tests to run for each kind of change. The feature tests moved to
  `docs/smoke-tests/` (access and network, control panel, lab features) and the failure points to
  `docs/troubleshooting.md`. The one-time cutover steps from the hand-built API were removed, the
  default profile in the runbook no longer names a profile for another account, and the SSM-only
  test now switches domain access off in the tfvars copy (an environment variable cannot override
  it).
- CI: the tflint and checkov job is now blocking, with checkov pinned. Every remaining finding is skipped
  inline next to its resource with the reason. The control panel API tests now also run in CI.

### Fixed
- Auto-stop timer reset: a far-future, oversized or malformed value in the `reset-at` parameter could
  switch the hard limit off (the instance monitor and the watchdog treated it as "reset just now" every
  run), and a huge value could crash the watchdog. Now only a decimal of at most 10 digits, no more than
  5 minutes in the future, counts; anything else is ignored and the limit counts from boot. The watchdog
  also falls back to the launch time on network errors instead of failing the run.

### Added
- The control panel API stack publishes its address, IDs and table names in the SSM parameter
  `/<project_name>/control-panel-api/settings`. With the new `control_panel_api_from_ssm = true` (set
  in the root example) the lab reads them at every plan, so a created or rebuilt API needs no IDs copied
  into `terraform.tfvars`. Values set in `terraform.tfvars` still win. If the parameter is missing, the
  lab plan warns instead of failing, so the lab can still be destroyed after the API stack.
- Open WebUI admin actions, foundations (#55 phase 0, #58): an SSM document in the API stack that accepts only
  named actions (today `status`: health and version against the pinned version), two more administrator-only routes
  (15 in all) to run one and read its result, kept readable after SSM forgets the command, a record and log line per action, and a desired-state table for phase 2. The
  panel never connects to Open WebUI or holds its credentials. The lab publishes its Open WebUI image for the
  version check.
- The control panel API as its own Terraform stack (`dashboards/api/terraform`): HTTP API with throttling and
  an access log, JWT authorizer starting on an empty holding pool, the 13 routes, both functions and their
  least-privilege roles, and the three tables (imported when they already exist, with deletion protection
  and point-in-time recovery). Tests check its routes against `openapi.yaml` and its IAM boundaries. The
  README has a no-outage cutover from the hand-built API.
- OpenAPI 3.1 description of the control panel API (`dashboards/api/openapi.yaml`), with a test that
  keeps its route list in step with `handler.py`.
- Auto-stop timer reset: a reset icon in the control panel (between Start and Access, on a running lab
  with a hard limit) gives the lab another full `auto_stop_max_uptime_minutes` without restarting it. Each
  reset writes a log line under the person who pressed it and shows "Timer reset at ..." under the status.
  The time is kept in the new SSM parameter `/<project_name>/auto-stop/reset-at` (created by Terraform, never
  overwritten by it); the instance monitor and the watchdog both count the limit from the later of boot and
  the last reset. The new API route and the customer Lambda's IAM change are applied by hand; see
  `dashboards/api/README.md`.
- Vulnerability findings MCP server (Lambda) and automatic registration in Open
  WebUI at instance bootstrap. The bearer token is read from Secrets Manager at
  run time and never stored in Terraform state, outputs or user data. See
  `lambda/vuln_mcp/README.md`.
- Manual acceptance tests for the vulnerability MCP integration
  (`docs/vuln-mcp-acceptance-tests.md`), all pending manual execution.
- CI workflow: `terraform fmt`, `validate` and `test`, shellcheck on the rendered
  bootstrap script, Python unit tests, and advisory tflint and checkov.
- `LICENSE` (MIT), this changelog, and an example S3 state backend
  (`backend.tf.example`) with a "Terraform state" section in the README.

### Changed
- The registration script sends the Open WebUI sign-in password to curl on stdin
  instead of writing it to a temporary file.
- CI actions updated to their Node 24 versions.
- Cloud-init user data is gzip-compressed to stay under the EC2 16 KB limit.

### Notes
- Changing the cloud-init template replaces the lab instance
  (`user_data_replace_on_change = true`), so the next `terraform apply` after
  pulling these changes replaces it.
