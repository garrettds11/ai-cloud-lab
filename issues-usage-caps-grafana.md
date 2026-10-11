# Issues to file: usage, spend caps, Grafana, log monitoring

Not found as existing issues in the repo (#59, #61 and #76 already exist). Check GitHub for duplicates before filing.
Each heading is an issue title; the code block is the body.

## Control panel: Usage and cost page (sessions, hourly shares, per-user charge)

```markdown
Labels: control-panel, tokenomics

Read-only admin page showing what each session cost and who owes what. Design: docs/monitoring-spec.md ("Cost allocation").

- Sessions table: start, stop, hours, cost, tokens, price per token, capacity utilization
- Per session: each user's tokens, hourly share, charge, active minutes
- Totals per user over a chosen date range
- Provisional marking for the hour in progress; final at stop
- Admin only; no per-user data goes to Grafana

Depends on #76 (usage data on the instance).

Acceptance criteria
- [ ] Hourly shares add up to the session cost
- [ ] Sessions with no tokens are charged to whoever started the instance, else listed as unallocated
- [ ] Non-admins get 403 from the API
```

## Spend caps: lab-total and per-user monthly dollar caps

```markdown
Labels: control-panel, cost, tokenomics

Dollar caps per calendar month (UTC), set on a Spend caps page. Design: docs/monitoring-spec.md ("Spend caps").

- Lab-total cap and per-user caps (default plus overrides); no cap means unlimited
- Panel roles: admin reads and changes; user_mgrs read only; operators read their own
- Scheduled check every 5 minutes, and before every Start
- 100% lab cap: instance stopped, Start disabled ("Monthly budget reached")
- 100% user cap: Cognito user disabled and Open WebUI role set to `pending`; "Blocked: spend cap" badge; reset on the 1st
- Every cap change, block and unblock goes in the change log

Acceptance criteria
- [ ] A capped user cannot sign in, and an open Open WebUI session ends
- [ ] Admins are never blocked
- [ ] Raising a cap above spend unblocks at once
```

## Spend cap emails at 50/80/98% through SNS, to verified identities (DONE offline)

```markdown
Labels: control-panel, cost

- One email per threshold per period (50, 80, 98, and one at 100%); admins copied at 100%
- Sent through one Amazon SNS topic; each person gets an email subscription filtered on a `recipient` attribute
- Only people whose Cognito identity has a verified email (`email_verified` true) are asked or emailed
- Address comes from the Cognito `email` only
- The spend job subscribes people and records the status on the user row (`unconfirmed`/`unverified`/`pending`/`confirmed`/`failed`/`expired`/`unsubscribed`); the admin page shows it and can send again
- Caps are enforced whether or not the address is confirmed
```

## Control panel: chat API test page

```markdown
Labels: control-panel

Admin page that sends a test chat request to the lab's Open WebUI API (see docs/open_webui/Model-Calls-via-API.md) and shows status, latency and the reply, to prove the model path end to end without opening Open WebUI.

Acceptance criteria
- [ ] Choose a model, send a short prompt, see reply and timing
- [ ] Uses the existing remote-command channel; no new public endpoint
- [ ] Admin only; the test traffic must not keep the lab awake (idle timer)
```

## Grafana: CloudWatch data source, five dashboards, alerts, panel link

```markdown
Labels: monitoring, grafana

Design: docs/monitoring-spec.md ("Dashboards", "Alerts", "Collection plan").

- Grafana Cloud CloudWatch data source with a read-only IAM role (Lambda tool-call logs, Lambda/ALB/EC2 metrics)
- Dashboards as JSON under `grafana/`: Lab overview, Tool calls, Tokenomics, Model serving and GPU, Logs
- Alerts: GPU hot, model on CPU, tool errors, MCP 401s, disk filling, Open WebUI 5xx, telemetry silent
- Sidebar link in the control panel to Lab overview

Depends on #76 (ai-lab-metrics).
```

## More MCP tools: log monitoring with a free, open-source backend

```markdown
Labels: mcp, enhancement

Add MCP tools for log monitoring. Splunk is out: even trial/free needs licensing. Evaluate cheap open-source options first: Grafana Loki (already receiving the lab's logs via Alloy), OpenSearch, or Graylog.

- Compare cost, licence, and fit with the existing Alloy/Grafana setup
- Pick one; build read-only tools (search, count, recent errors) in the ai-cloud-lab repo, like the vulnerability MCP
- Register in Open WebUI and add acceptance tests

Acceptance criteria
- [ ] No licence fee or trial expiry
- [ ] Tools are read-only and return bounded result sizes

Status: Grafana Cloud Loki chosen and the tools built offline (lambda/vuln_mcp/log_tools.py: list_log_sources, search_logs, count_log_events, summarize_log_errors, get_signin_events). Remaining: run against a real Loki (see the last issue below) and add acceptance questions for the log tools to docs/vuln-mcp-acceptance-tests.md.
```

---

## Added with steps 3 and 4 (not in the first six)

### Issue 7

**Title:** Decide: SES or SNS for the spend cap emails (DECIDED: SNS, to verified Cognito identities)

```
The monitoring spec asks for SES emails at 50/80/98/100%, so the spend caps job sends them with SES. The auto-stop alerts use SNS, and the project owner prefers SNS for alert emails. Decide which to keep.

SES: emails each person directly, needs a confirmed sender and (in the sandbox) a confirmed recipient. Built.
SNS: topic subscriptions need each recipient to click a confirmation link per topic, and one topic cannot address one person.

Acceptance: decision recorded in docs/monitoring-spec.md; if SNS, the job's send_email is swapped and the SES identity removed.
```

### Issue 8

**Title:** Cap warning email counts from the Terraform limit, not the panel's timer policy (DONE: the watchdog now applies the shorter policy limits)

```
The watchdog Lambda reads the session limit from Terraform (env var). When the panel sets a shorter session length, the instance stops at the shorter limit but the watchdog's "stopping soon" warning still counts from the Terraform limit, so no warning is sent.

Acceptance: the watchdog reads auto-stop/policy and applies the same shorter-limit rule as scripts/ai-lab-idle-check.sh; test added to tests/test_auto_stop_watchdog.py.
```

### Issue 9

**Title:** Spend caps: "override once" for the lab cap (DONE offline)

```
When the lab cap stops the instance, only raising the cap lets it start again. Add a one-time override for an administrator (start once without changing the cap).

Acceptance: an administrator-only control on Spend caps, recorded in the access changes history, expiring at the next stop.
```

### Issue 10

**Title:** Check the Grafana dashboards and alert queries against a real stack

```
The five dashboards and seven alert rules (grafana/) were written and tested offline only. On the first deploy, run docs/runbook/smoke-tests/lab-features.md "Grafana dashboards and alerts test" and fix whatever does not match: metric and label names as OpenTelemetry delivers them, the Loki stream labels, the CloudWatch Metrics Insights queries, and the alert rules' data source IDs.

Acceptance: every panel shows data (or an explained empty state) with the lab running; all seven rules load without an error state; grafana/generate.py updated and tests/test_grafana_dashboards.py passes.
```

### Issue 11

**Title:** Check the log tools against a real Loki and Open WebUI

```
log_tools.py assumes logs reach Loki with a service_name label and that `job`, `unit` and `container` are labels or structured metadata, and parses Open WebUI's access-log lines for sign-ins. None of that has been seen on a real stack.

Acceptance: "Log search tools test" in lab-features.md passes; get_signin_events counts a deliberate bad sign-in; if labels differ, log_mcp_stream_selector or the source mapping in log_tools.py is corrected and tests updated.
```

### Issue 12

**Title:** Time to first token and generation speed metrics

```
The exporter reads request time and model load time from Ollama's journal and counts chats and ratings from Open WebUI's database. Time to first token and generation speed (output tokens per second) need per-message timing that has not been checked on Open WebUI v0.11.4.

Run `sudo /usr/local/sbin/ai-lab-metrics --shapes` on a deployed lab, find the fields (or the Ollama eval_count / eval_duration) that hold them, add the metrics to scripts/ai-lab-metrics, the panels to grafana/generate.py, and tests. Also confirm feedback.data.rating is positive for thumbs up and negative for thumbs down, and add a per-model feedback split if the message record names the model.

Acceptance: both metrics appear in Grafana for a real chat, tests/test_usage_monitoring.py covers them, docs/monitoring-spec.md "Not built yet" is removed.
```
