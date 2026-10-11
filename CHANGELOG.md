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
- Auto-stop: new Terraform variables `auto_stop_absolute_max_minutes` (an absolute limit from boot that no
  timer reset or panel setting can extend; the instance monitor and the watchdog both enforce it, and the
  watchdog warns by email before it) and `auto_stop_max_resets` (how many times the Reset button may extend
  the hard limit in one run). The Timer policy page can lower the reset count, never raise it, and the
  Instances page shows the resets used and left. A reset is cut short at the absolute limit, and one that
  would add no time is refused. New runtime parameter `/<project>/auto-stop/reset-count` (count per run).
  The stop time stays derived from the stored reset time and the policy, rather than stored as a deadline,
  so a tighter policy applies at once. Both new variables default to 0 (off).
- Control panel, Open WebUI page: import a skill from pasted SKILL.md text (up to 1800 characters,
  stored as plain text and never run) and attach it to chosen models. It is read back before it counts
  and is not saved for the next start. Skills with files, ZIPs and folders are imported in Open WebUI
  itself (Workspace, Skills). Written against the Skills routes read from the v0.11.4 source.
- Control panel, Open WebUI page: read tool servers with a connection test, read a fixed list of
  settings (Global or User default), and download the configuration without any secret.
- Control panel, Open WebUI page: change the default model, a model's temperature and context length,
  four feature switches, the routes API keys may call, and add, change or remove a tool server (HTTPS
  only, checked by Open WebUI before saving, key chosen from `<project>/tool-tokens/` secrets). Each
  change is read back before it counts. New API variable `private_tool_hosts`, and the instance role
  may read `<project>/tool-tokens/*` (root stack apply needed).
- Changes made from the panel are saved in a DynamoDB table and put back by the spend job about 150
  seconds after each start; **Reapply saved settings** does it on demand. See
  `docs/open-webui-control-panel.md`.
- Control panel, Instances: an administrator button that opens the instance in the AWS console. This
  is the way to stop an instance by hand; the panel has no Stop button on purpose (auto-stop only).
  `awsIconUrl` in `config.js` shows the AWS icon you supply.
- Metrics from Ollama's journal (chat requests by status class, average and longest request time over
  5 minutes, last model load time) and from Open WebUI's database (chat count, answers rated up and
  down, read only, no text). Each has its own `ai_lab_exporter_source_up`. `ai-lab-metrics --shapes`
  prints the keys and types of Open WebUI's answers so time to first token and generation speed can
  be built against the real fields. Offline tests only.
- Grafana (step 5), written and tested offline only: five dashboards (Lab overview, Tool calls,
  Tokenomics, Model serving and GPU, Logs) and seven alert rules (GPU hot, model on CPU, tool errors,
  MCP refusals, disk filling, Open WebUI errors, telemetry silent) under `grafana/`, generated by
  `grafana/generate.py`, and `scripts/import-grafana.ps1` to load them into a Grafana stack. A
  read-only IAM role for Grafana's CloudWatch data source (`grafana_cloudwatch.tf`, on only when
  `grafana_cloudwatch_account_id` and `grafana_cloudwatch_external_id` are set). CloudWatch metric
  filters turn the MCP Lambda's log lines into `AILab/Tools` metrics (calls, errors, latency, refusals).
  A refused MCP request now logs one `unauthorized` line, never the token. New variable
  `grafana_dashboard_url` adds an administrators-only **Grafana** link to the control panel sidebar.
- Log search tools for the Security Analyst (step 6), offline only: when `log_mcp_loki_url`,
  `log_mcp_loki_user` and `log_mcp_loki_token_secret_arn` are set, the vulnerability MCP server also
  offers `list_log_sources`, `search_logs`, `count_log_events`, `summarize_log_errors` and
  `get_signin_events`, read from Grafana Cloud Loki. The model never writes a query, results are
  scrubbed and capped and marked as untrusted, and the Security Analyst prompt covers them. Loki was
  chosen over Splunk, OpenSearch and Graylog because the logs are already there and it needs no
  extra server or licence.
- Control panel pages for administrators, all written and tested offline only: **Open WebUI** (health,
  version, Ollama models and what is loaded), **Usage and cost** (tokens and each person's charge per
  session and period), **Spend caps**, **Timer policy** and **Chat API test**. Everyone sees their own
  cap on **Spend caps**; user managers see everyone's.
- Spend caps (#76): a lab total and a monthly cap per person, in dollars per calendar month (UTC). A
  scheduled job every five minutes keeps a monthly ledger, emails each person and the administrators at
  50%, 80%, 98% and 100% through Amazon SNS, and at 100% disables the person in Cognito and sets their
  Open WebUI role to `pending` (administrators are never blocked). The lab cap at 100% stops the
  instance and refuses **Start**. Caps reset on the 1st; raising a cap lets people back in. New variable
  `spend_emails_enabled` (API stack), a new `ai-lab-set-role` script, and a `set-role` action (job only)
  in the SSM document. Emails go only to people whose Cognito identity has a verified email address:
  the job gives each one a filtered email subscription on one topic, and SNS sends them a confirmation
  link they must click once (statuses: unverified, pending, confirmed, expired, unsubscribed).
- Log tools acceptance tests: `docs/log-tools-acceptance-tests.md` (eight questions, with lines to plant in the
  lab's logs so the answers are known) and `docs/log-acceptance-cases.json`, which
  `tests/test_log_acceptance_doc.py` checks against the real tool code on every run. Not yet run live.
- Lab cap override (#9): while the lab is at its monthly cap, an administrator can choose **Allow one
  run past the cap** on Spend caps. The next start (or the run in progress) is allowed without
  changing the cap, and the permission ends when the lab next stops. It is recorded in the access changes
  history. New route `POST /admin/spend-caps/lab-override`.
- Timer policy (#61): the panel can set shorter idle and session-length limits inside the limits
  Terraform sets, stored in the new `auto-stop/policy` parameter. The instance monitor and the panel
  apply the shorter value, and so does the watchdog, so its warning email and its backstop stop follow
  the shorter limit too (it ignores the policy where Terraform leaves a limit off).
- Chat API test: the `chat-test` action sends one fixed prompt to a chosen model through Open WebUI's
  chat API (`scripts/ai-lab-chat-test`) and reports the reply, time taken and token counts.
- Usage data on the instance (#76): a session log of start and stop times, hourly token usage per
  user and model read from Open WebUI's analytics API, and `ai-lab-metrics`, which writes GPU,
  Ollama model load, token totals, model settings and idle-timer metrics for Alloy's textfile
  collector. The control panel's Open WebUI action channel has a new `usage` action that returns the
  usage record. New variable `instance_hourly_cost_usd` for pricing. Scripts that call Open WebUI as
  the admin restore the admin's last-active time so they cannot keep the lab from auto-stopping.
- A **Security Analyst** model is created in Open WebUI at boot when the vulnerability MCP is on: it
  runs on `llm_model` with the Vulnerability Findings tools attached and switched on in every new chat,
  native tool calling, Open WebUI's built-in tools off so they don't compete, and a system prompt that
  tells it to answer vulnerability questions only from the tools. It becomes the default model unless
  an admin chose one. `ai-lab-register-vuln-mcp` manages it with the connection, puts it back if edited,
  and removes it when the feature is off.
- NVIDIA GPU instances: GPU instance types boot AWS's Deep Learning Base GPU AMI (Ubuntu 24.04 with the
  driver, CUDA and Docker preinstalled) and Ollama runs the model on the GPU. The example now deploys
  `g6.xlarge` (one 24 GB L4, about 13% more per hour than `c7i.4xlarge`) with `qwen3:14b`, which calls
  tools reliably, and a 100 GiB root volume. `ollama_context_length` (default 16384) leaves room for tool
  definitions. `ai-lab-status` shows the GPU.
- The instance is placed in a default subnet whose zone offers its instance type, and the ALB always
  covers that zone (#8). Plan fails early if no zone offers the type, or if `root_volume_size` is
  smaller than the AMI.
- The helper scripts (idle check, MCP registration, Alloy config) are downloaded at boot from a private
  per-lab S3 bucket and checked against Terraform's SHA-256, instead of being embedded in user data,
  which had reached its size budget. The user data dropped from about 14.5 KB to about 7 KB.
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
