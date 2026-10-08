# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). The project has no
version numbers yet, so everything is listed under Unreleased.

## [Unreleased]

### Added
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
