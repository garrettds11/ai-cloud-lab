# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has no
version numbers yet, so everything is listed under Unreleased.

## [Unreleased]

### Fixed
- Auto-stop timer reset: a far-future, oversized or malformed value in the `reset-at` parameter could
  switch the hard limit off (the instance monitor and the watchdog treated it as "reset just now" every
  run), and a huge value could crash the watchdog. Now only a decimal of at most 10 digits, no more than
  5 minutes in the future, counts; anything else is ignored and the limit counts from boot. The watchdog
  also falls back to the launch time on network errors instead of failing the run.

### Added
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
