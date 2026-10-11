# Demo script

A start-to-finish script for showing the AI Cloud Lab: deploy it, prove it is online, tour the control
panel and Open WebUI, and show a model answering questions from the vulnerability database through
MCP. About 45 minutes, including about 10 minutes of boot time to talk over.

Commands are **Local Windows PowerShell** from `C:\GitHub\ai-cloud-lab`. The full detail behind each
deploy step is in [the runbook](runbook/terraform-smoke-test-plan.md).

## Before the audience joins

- **Rehearse the MCP questions once on the same day** (step 9). The default model is **Security
  Analyst**, which the lab creates at boot on `qwen3:14b` with the vulnerability tools switched on.
  If it answers a question without calling the tool in rehearsal, plan to show the tool call from the
  server's log for that question.
- Have two private browser windows ready: one for the administrator, one for a demo user.
- Have the passwords at hand (the administrator and demo-user secrets), not on screen.
- Optional: start step 2 before the audience joins and pick up at step 3, to skip the wait.

## 1. Enter the demo

What to say, briefly:

- **What it is:** a private, multi-user AI chat lab on AWS. Open WebUI in front, Ollama running
  local models, nothing sent to an outside AI service.
- **How people get in:** Cloudflare Access and Amazon Cognito sign people in before anything reaches
  AWS; the server itself is not open to the internet.
- **How it stays cheap:** the lab is built by Terraform when needed and destroyed afterwards;
  auto-stop shuts it down when nobody uses it, and the control panel lets approved users start it
  again.
- **What we will show:** deploy, health, the control panel, Open WebUI, and a model querying a
  vulnerability database through MCP.

## 2. Deploy the lab

The runbook's steps 1 and 3, condensed. The control panel and its API are always up, so only the lab
is built.

```powershell
Set-Location C:\GitHub\ai-cloud-lab
git switch dev; git pull origin dev
$env:AWS_PROFILE = "garrett_gspear"
$env:AWS_DEFAULT_REGION = "us-east-1"
aws sts get-caller-identity --query Account --output text      # must print 394566733278
Copy-Item terraform.tfvars.example terraform.tfvars -Force
```

Paste the wrapper and the `Get-TfVar` block from [runbook step 1](runbook/terraform-smoke-test-plan.md#step-1-prepare-every-new-powershell-window)
(items 4 and 5), then:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("init")
Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-out=ai-lab.tfplan")
Invoke-TerraformWithCloudflareToken -Arguments @("apply", "ai-lab.tfplan")
Remove-Item ai-lab.tfplan
$env:instance_id = terraform output -raw instance_id
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\set-cognito-passwords.ps1"
```

While it builds (about 3 minutes for Terraform, then about 10 for the instance to install Ollama, the
model and Open WebUI), point out what the plan creates: the instance, the load balancer, the
Cloudflare record and Access application, the Cognito users, the auto-stop watchdog, and the wiring
that tells the control panel about this lab.

## 3. Show that everything is online

One block that prints a status board:

```powershell
$id = $env:instance_id
"Account        " + (aws sts get-caller-identity --query Account --output text)
"Instance       " + (aws ec2 describe-instances --instance-ids $id --query "Reservations[0].Instances[0].State.Name" --output text)
"Load balancer  " + (aws elbv2 describe-target-health --target-group-arn (terraform output -raw open_webui_target_group_arn) --query "TargetHealthDescriptions[0].TargetHealth.State" --output text)
$cfg = aws s3 cp "s3://$panelBucket/config.js" - | Out-String
"Panel API      " + [regex]::Match($cfg, '"apiUrl":"([^"]+)"').Groups[1].Value
$panelApi = aws ssm get-parameter --name "/$projectName/control-panel-api/settings" --query Parameter.Value --output text | ConvertFrom-Json
$issuer = aws apigatewayv2 get-authorizer --api-id $panelApi.api_id --authorizer-id $panelApi.authorizer_id --query JwtConfiguration.Issuer --output text
"Panel sign-in  " + $(if ($issuer.EndsWith((terraform output -raw cognito_user_pool_id))) { "this lab's Cognito pool" } else { "NOT this lab: $issuer" })
@{ commands = @(
  "test -f /var/lib/ai-lab/ready && echo 'Bootstrap      READY' || echo 'Bootstrap      NOT READY'",
  "grep -q 'Connection registered' /var/log/ai-lab-bootstrap.log && echo 'Vuln MCP       registered' || echo 'Vuln MCP       not registered'",
  "grep -q '(security-analyst) created' /var/log/ai-lab-bootstrap.log && echo 'Analyst model  created' || echo 'Analyst model  missing'",
  "docker ps --filter name=open-webui --format 'Open WebUI     {{.Status}}'",
  'ollama list | tail -n +2 | awk ''{print "Model          " $1}'''
) } | ConvertTo-Json | Set-Content -Encoding ascii demo-check.json
$cmd = aws ssm send-command --instance-ids $id --document-name AWS-RunShellScript --parameters file://demo-check.json --query Command.CommandId --output text
Start-Sleep -Seconds 10
aws ssm get-command-invocation --command-id $cmd --instance-id $id --query StandardOutputContent --output text
Remove-Item demo-check.json
```

Expected: `running`, `healthy`, the API address, `this lab's Cognito pool`, `READY`,
`registered`, `created`, `Up ... (healthy)` and `qwen3:14b`. If bootstrap is `NOT READY`, give it a few more
minutes and run the block again.

Explain each line in one sentence: the instance runs, the load balancer can reach Open WebUI, the
panel knows where its API is, the panel's sign-in trusts this lab's users, the install finished, and
Open WebUI is already connected to the vulnerability database.

## 4. If needed, fix the DNS resolver

Right after a rebuild, `https://aiwebdemo.click` can show "server IP address could not be found" for
up to 30 minutes: the record was removed when the lab was last destroyed, and resolvers remember
that. Prove the record exists, then clear your caches:

```powershell
Resolve-DnsName aiwebdemo.click -Server albert.ns.cloudflare.com
Clear-DnsClientCache
Resolve-DnsName aiwebdemo.click -Server 1.1.1.1
```

In Chrome, also open `chrome://net-internals/#dns` and choose **Clear host cache**. If it still
fails, switch the PC's DNS to `1.1.1.1` or carry on with the control panel and come back. Details:
[runbook step 4](runbook/terraform-smoke-test-plan.md#step-4-check-the-deployment).

## 5. Sign in to the control panel

Open <https://cp.aiwebdemo.click>.

1. **As a demo user** (for example `demo2@example.local`, an operator): the Instances page lists
   only the instances this user was granted. Point out the status, the **Start** button, the
   auto-stop line ("Stops at about ...") and the **Access** button.
2. **Reset the timer:** press the timer icon between Start and Access, read the confirmation, and
   choose **Reset timer**. A "Timer reset at ..." line appears and the stop time moves out.
3. **As the administrator** (`admin@example.local`, second window): the same instances, plus
   **User management** (roles and grants), **Changes**, **Logins** and **Logs**. Show the log line
   from the timer reset with the operator's name: every action is attributed.

Points to make: the panel stays up when the lab is destroyed; people can start the lab themselves
without AWS access; only instances tagged for the panel can be started; roles live in the panel, not
in the sign-in token, so a change takes effect on the next click.

## 6. The advanced controls (issue #55 and what follows)

These are planned, not built yet. One piece already works behind the scenes: the panel's API can ask
the lab for Open WebUI's health and version. Show it from PowerShell:

```powershell
$doc = terraform -chdir=dashboards/api/terraform output -raw webui_admin_document
$cmd = aws ssm send-command --document-name $doc --instance-ids $env:instance_id --parameters "action=status,expectedVersion=0.11.4" --query Command.CommandId --output text
Start-Sleep 15
aws ssm get-command-invocation --command-id $cmd --instance-id $env:instance_id --query "[Status, StandardOutputContent]" --output text
```

It runs one fixed, named action on the instance; the panel cannot send arbitrary commands. Then walk
through the roadmap:

| Phase | What administrators get |
|---|---|
| [#59](https://github.com/garrettds11/ai-cloud-lab/issues/59), read-only | Open WebUI health and version, installed models, Ollama status, tool-server status with **Test connection**, a settings summary, and a configuration export without secrets |
| [#61](https://github.com/garrettds11/ai-cloud-lab/issues/61), timer policy | Session length, idle time and resets per run set in the panel, inside a hard limit Terraform owns |
| [#60](https://github.com/garrettds11/ai-cloud-lab/issues/60), limited writes | Default model, basic model settings, curated feature switches, Skills, and adding or testing MCP and OpenAPI tool servers, each checked against the pinned Open WebUI version and kept across rebuilds |
| [#63](https://github.com/garrettds11/ai-cloud-lab/issues/63), model catalog | Install vetted models from an approved catalog, with hardware checks and verified downloads |
| [#64](https://github.com/garrettds11/ai-cloud-lab/issues/64), larger features | Sign-up policy and approvals, model sync, configuration restore, groups and knowledge collections |
| [#62](https://github.com/garrettds11/ai-cloud-lab/issues/62), decision | Decided: no **Stop now** button. Auto-stop only; an administrator stops a run by hand from the **AWS console** button, which opens the instance in the AWS console |

The design rule to mention: Terraform owns how the lab is built, the panel owns day-to-day choices,
and Open WebUI stays the place for advanced administration.

## 7. Open Open WebUI

From the panel, press **Access** (or open <https://aiwebdemo.click>). Cloudflare Access sends you to
the Cognito sign-in page; sign in as the demo user. Choosing **Continue with Cognito** in Open WebUI
reuses that sign-in, so there is no second password. Point out the security banner at the top.

## 8. Show the Open WebUI features

Pick what suits the audience:

- **Chat with a local model:** ask a general question and let the reply stream in. Nothing leaves
  AWS.
- **Model selector:** **Security Analyst** is the default; the plain `qwen3:14b` model is also there
  for general chat. **Workspace > Models > Security Analyst** shows how it is built: the base model,
  the system prompt, and the Vulnerability Findings tools attached.
- **Chat history:** earlier chats, search, renaming and folders.
- **Documents:** attach a file to a chat and ask about its contents.
- **Code Interpreter:** turn it on from the message box menu and ask for a small calculation or chart.
- **Integrations:** the menu next to **+** lists the tools a chat can use, including **Vulnerability
  Findings** (step 9).
- **As the administrator:** **Admin Panel > Users** (people created through Cognito sign-in) and
  **Admin Panel > Settings > Integrations > External Tool Servers**, where the lab registered the
  vulnerability server by itself at boot.

## 9. Query the vulnerability database through MCP

1. Start a **new chat**. The model is already **Security Analyst** and **Vulnerability Findings** is
   already switched on; open **Integrations** (next to **+**) to show it. The lab set both up at
   boot, so no one has to remember a toggle.
2. Ask, one per message:
   - "How many open Critical findings are there?" (the standard data has **9**)
   - "Which hosts are affected by Log4Shell?"
   - "What vulnerabilities are open on prod-app-01?"
   - "Which Critical findings have a public exploit? Show the newest first."
   - "Which hosts do you have vulnerability data for?"
3. **Show the proof**, not just the answer: expand the tool-call entry under the reply to show the
   tool name, the arguments and the data that came back.
4. **Show the server side** (optional), in PowerShell:

   ```powershell
   aws logs tail (terraform output -raw vuln_mcp_log_group) --since 10m
   ```

   Each question appears as a `tool_call` line with the tool, the arguments and `ok`. No token and no
   finding data is logged.
5. **Show that it is read-only:** ask "Mark the Log4Shell finding on prod-app-01 as fixed." The model
   should say it cannot; there is no tool that changes data.

If the model answers without a tool-call entry, say so plainly: models do not always call tools.
Check the chat is on **Security Analyst**, ask again in a new chat, or show the same question
answered in rehearsal.

## 10. Wrap up

- **Recap:** one Terraform run built a private, signed-in AI lab; the panel lets approved people
  start it and extend it without AWS access; the model answered questions from live data through a
  read-only MCP tool, with proof of each call.
- **Cost:** auto-stop shuts it down when idle or at its hard limit; destroying it leaves only the
  panel, its API and the domain.
- **What is next:** the #55 panel controls (step 6), larger tool-calling models on a GPU
  ([#41](https://github.com/garrettds11/ai-cloud-lab/issues/41)), more security tools such as Splunk,
  CrowdStrike and Qualys ([#43](https://github.com/garrettds11/ai-cloud-lab/issues/43)–[#48](https://github.com/garrettds11/ai-cloud-lab/issues/48)),
  and keeping models and chats across rebuilds ([#13](https://github.com/garrettds11/ai-cloud-lab/issues/13)).
- **Questions.**

Afterwards, stop or destroy the lab: [runbook step 6](runbook/terraform-smoke-test-plan.md#step-6-stop-or-destroy).
