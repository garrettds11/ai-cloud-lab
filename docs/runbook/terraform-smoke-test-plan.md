# Deploy and Smoke Test

How to deploy the AI Cloud Lab, check that it works, and take it down. Everything here is
**Local Windows PowerShell** unless a block says **Linux (SSM shell on the lab instance)**.

This page is only what a deploy needs. The detailed tests and fixes live in separate pages,
linked from [step 5](#step-5-test-what-changed) and [Troubleshooting](troubleshooting.md):

| Page | Holds |
|---|---|
| [docs/smoke-tests/access-and-network.md](smoke-tests/access-and-network.md) | Accounts and passwords, domain access, Cloudflare Access, Cognito sign-in, SSM-only access, origin lockdown, outbound and edge checks, load balancer hardening, the lab unavailable page |
| [docs/smoke-tests/control-panel.md](smoke-tests/control-panel.md) | The full control panel config test, the API stack test, the Open WebUI admin action test, the auto-stop timer reset test |
| [docs/smoke-tests/lab-features.md](smoke-tests/lab-features.md) | Grafana telemetry, Grafana dashboards and alerts, the vulnerability findings MCP server, log search tools, Ollama and database metrics |
| [docs/troubleshooting.md](troubleshooting.md) | Likely failure points and their fixes |

## Which steps to run

There are two Terraform stacks, each with its own state:

| Stack | Folder | Holds | Runs with |
|---|---|---|---|
| **Control panel API** | `dashboards\api\terraform` | The panel's API, sign-in authorizer, routes, functions, tables, the Open WebUI admin document | plain `terraform` |
| **Lab** | repository root | The instance, Open WebUI, Ollama, load balancer, Cloudflare, Cognito, auto-stop | the wrapper from step 1 |

| What you are doing | Steps |
|---|---|
| A normal lab build or rebuild | 1, 3, 4 |
| Files changed in `dashboards\api` (including its `terraform` folder) | 1, 2, then 3 and 4 |
| Only the panel pages changed (files in `dashboards`, not `dashboards\api`) | 1, then [Deploy the pages](#deploy-the-pages) |
| First deploy to a new account or domain | 0, 1, 2, 3, 4 |
| After the API stack was destroyed | 1, 2, 3, 4 |
| Taking it down | 1, 6 |

Build the API stack before the lab; destroy the lab before the API stack.

## Step 0. New account or domain (first deploy only)

Skip this on an environment that is already set up, such as the demo account. Before the first
deploy anywhere else, complete [pre-deployment.md](pre-deployment.md): the tools, AWS account,
secrets, domain, certificate, Cloudflare, Grafana Cloud, control panel hosting and the two settings
files that Terraform needs but does not create. Then continue with step 1.

## Step 1. Prepare (every new PowerShell window)

1. Get the latest code:

   ```powershell
   Set-Location C:\GitHub\ai-cloud-lab
   git switch dev
   git pull origin dev
   ```

2. Choose the AWS account. Put your own AWS CLI profile name here; both stacks and every AWS CLI
   command use it:

   ```powershell
   $env:AWS_PROFILE = "ai-cloud-lab"
   $env:AWS_DEFAULT_REGION = "us-east-1"
   aws sts get-caller-identity --query Account --output text
   ```

   The last line must print the lab's account (`394566733278` for the demo). If not, stop and fix
   the profile. Both `terraform.tfvars.example` files keep `aws_profile = null`, which means
   "use `$env:AWS_PROFILE`". A new window without it would use your **default** profile, which may
   be another account.

3. Copy the example over the working settings. `terraform.tfvars.example` is the source of truth
   and is committed; `terraform.tfvars` is a git-ignored copy that every run replaces:

   ```powershell
   Copy-Item terraform.tfvars.example terraform.tfvars -Force
   ```

   Only if this run needs a different value, open the copy (`code terraform.tfvars` or
   `notepad terraform.tfvars`), change it and save. A lasting change goes in the example and is
   committed; a change made only in the copy is lost at the next run. Never put a token or
   password in either file.

4. Load the wrapper. It reads the Cloudflare token from Secrets Manager for each Terraform command
   and removes it afterwards, so the token is never typed or stored. It lasts only for this window:

   ```powershell
   $env:CLOUDFLARE_TOKEN_SECRET_ARN = "arn:aws:secretsmanager:us-east-1:394566733278:secret:CLOUDFLARE_API_TOKEN-LxdgxA"

   function Invoke-TerraformWithCloudflareToken {
     param([Parameter(Mandatory)][string[]]$Arguments)

     $cloudflareSecretString = aws secretsmanager get-secret-value `
       --secret-id $env:CLOUDFLARE_TOKEN_SECRET_ARN `
       --query SecretString `
       --output text `
       --region $env:AWS_DEFAULT_REGION `
       --profile $env:AWS_PROFILE | Out-String

     if ($LASTEXITCODE -ne 0) {
       throw "Cloudflare API token could not be retrieved from Secrets Manager."
     }

     $parsedCloudflareSecret = $null
     try {
       $parsedCloudflareSecret = $cloudflareSecretString.Trim() | ConvertFrom-Json -ErrorAction Stop
     }
     catch {
       # A plaintext SecretString is also supported.
     }

     if ($parsedCloudflareSecret -is [string]) {
       $retrievedCloudflareToken = $parsedCloudflareSecret
     }
     else {
       $tokenProperty = $parsedCloudflareSecret.PSObject.Properties |
         Where-Object { $_.Name.Trim() -eq "CLOUDFLARE_API_TOKEN" } |
         Select-Object -First 1

       if ($null -ne $tokenProperty) {
         $retrievedCloudflareToken = [string]$tokenProperty.Value
       }
       else {
         $retrievedCloudflareToken = $cloudflareSecretString.Trim()
       }
     }

     $env:CLOUDFLARE_API_TOKEN = $retrievedCloudflareToken.Trim()

     if ([string]::IsNullOrWhiteSpace($env:CLOUDFLARE_API_TOKEN)) {
       throw "Cloudflare API token could not be retrieved from Secrets Manager."
     }


     try {
       & terraform @Arguments
       if ($LASTEXITCODE -ne 0) {
         throw "Terraform exited with code $LASTEXITCODE."
       }
     }
     finally {
       Remove-Item Env:\CLOUDFLARE_API_TOKEN -ErrorAction SilentlyContinue
     }
   }
   ```

   Run every lab `plan`, `apply` and `destroy` through the wrapper. Plain `terraform` fails on the
   lab with `403 Missing X-Auth-Email header`. The API stack has no Cloudflare resources and uses
   plain `terraform`.

   In PowerShell, quote any plain Terraform argument with a dot after `=`, such as
   `"-out=api.tfplan"`. Unquoted, PowerShell splits it at the dot and Terraform fails with
   "Too many command line arguments". The wrapper's `@("...")` lists are already quoted.

5. Load the helper that reads values from `terraform.tfvars`, and the values later steps and the
   test pages use:

   ```powershell
   function Get-TfVar([string]$Name) {
     $m = Select-String -Path .\terraform.tfvars -Pattern ('^\s*' + $Name + '\s*=\s*"([^"]+)"') | Select-Object -First 1
     if (-not $m) { throw "$Name is not set in terraform.tfvars" }
     $m.Matches.Groups[1].Value
   }
   $projectName       = Get-TfVar project_name
   $domainName        = Get-TfVar domain_name
   $panelBucket       = Get-TfVar control_panel_bucket
   $panelDistribution = Get-TfVar control_panel_distribution_id
   ```

## Step 2. Control panel API stack

> SKIP THIS SECTION IF THE API STACK ALREADY EXISTS **UNCHANGED**--SKIP TO STEP 3.
>
> Do NOT SKIP this section if:
> - **There is a code change:** the functions update in place.
> - Only run this step when files in `dashboards\api` change, on the first build, or after the stack is destroyed.

On a new account or domain, `lab_project_name`, `panel_origin` and `bootstrap_admins` in this stack's example must be set first ([step 0](#step-0-new-account-or-domain-first-deploy-only)).

```powershell
Set-Location C:\GitHub\ai-cloud-lab\dashboards\api\terraform
Copy-Item terraform.tfvars.example terraform.tfvars -Force
terraform init
terraform plan "-out=api.tfplan"
```

**Read the plan before applying.**

- **First build, or after a destroy:** everything **will be created**. The three panel tables
  (`panel_users`, `instance_entitlements`, `control_panel_events`) **will be imported** with
  `adopt_existing_tables = true`, which is right when they already exist, as they do on the demo
  account (a destroy leaves them, because of deletion protection). On a new account, set it to
  `false` in the copy so they are created. To see which applies:

  ```powershell
  foreach ($t in 'panel_users', 'instance_entitlements', 'control_panel_events') {
    aws dynamodb describe-table --table-name $t --query "Table.TableName" --output text 2>$null
    if ($LASTEXITCODE -ne 0) { "$t does not exist" }
  }
  ```

  Use `true` only if all three names print, `false` only if all three are missing. A mix means a
  table is missing; stop and ask before building.
- **Stop** if anything shows **must be replaced** or **will be destroyed**: it would change the
  API's IDs or lose table data.

Apply, then check the settings the stack publishes for the lab:

```powershell
terraform apply api.tfplan
Remove-Item api.tfplan
aws ssm get-parameter --name (terraform output -raw lab_settings_parameter) --query Parameter.Value --output text
Set-Location C:\GitHub\ai-cloud-lab
```

The parameter command prints one line of JSON with `api_url`, `api_id`, `authorizer_id`,
`holding_pool_id`, `users_table` and `entitlements_table`. The lab reads it at every plan
(`control_panel_api_from_ssm = true`), so nothing is copied. Do not set those six values in the
root tfvars: a value set there wins over the parameter.

## Step 3. Lab

From the repository root:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("init")
Invoke-TerraformWithCloudflareToken -Arguments @("fmt", "-recursive")
Invoke-TerraformWithCloudflareToken -Arguments @("validate")
Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-out=ai-lab.tfplan")
```

`fmt -recursive` only fixes whitespace and alignment, including in your `terraform.tfvars`.

**Read the plan before applying.**

- **Stop** if it prints **Warning: Check block assertion failed** for
  `check.control_panel_api_settings_found`. The lab cannot find the API's settings: run step 2
  first, or check the profile. Applying anyway leaves the panel with no API address.
- **After step 2 created or rebuilt the API stack**, expect
  `terraform_data.control_panel_authorizer["authorizer"]` **must be replaced**, and the `config.js`
  object updated with the new address. That points the API's sign-in at this lab. If it is
  missing, see [Troubleshooting](troubleshooting.md).
- `aws_instance.ai_lab` **must be replaced** when the bootstrap or a script it embeds changed.
  Chats and pulled models on the instance are lost.
- On a normal rebuild with nothing changed, the plan matches what the last apply created.

Apply and remove the plan file (it can contain sensitive values; never commit it):

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("apply", "ai-lab.tfplan")
Remove-Item ai-lab.tfplan
$env:instance_id = terraform output -raw instance_id
```

Apply finishes when EC2 is running, not when Ollama, the model and Open WebUI are ready (step 4).

With `enable_cognito = true`, Terraform creates the Cognito users without passwords. Set them from
the secrets now, or nobody can sign in at step 4:

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\set-cognito-passwords.ps1"
```

### Deploy the pages

Only when files in `dashboards` changed (the pages, not `dashboards\api`). `config.js` is
published by the lab apply and is left alone here:

```powershell
aws s3 sync .\dashboards "s3://$panelBucket" --exclude "*.md" --exclude "config.example.js" --exclude "config.js" --exclude "api/*" --exclude "_deploy/*" --delete
aws cloudfront create-invalidation --distribution-id $panelDistribution --paths "/*"
```

Reload the panel with Ctrl+F5 after about a minute.

## Step 4. Check the deployment

Every deploy. Each takes a minute or two.

1. **Bootstrap finished.** Open a shell on the instance:

   ```powershell
   aws ssm start-session --target $env:instance_id
   ```

   **Linux (SSM shell on the lab instance).** The SSM shell is a plain `sh` shell: paste **one command at a time**. Pasting several lines at once interleaves them (errors such as `er: not found`).

   ```bash
   test -f /var/lib/ai-lab/ready && echo READY || (test -f /var/lib/ai-lab/failed && echo FAILED || echo NOT_READY)
   ```

   ```bash
   tail -n 100 /var/log/ai-lab-bootstrap.log
   ```

   ```bash
   sudo docker ps
   ```

   ```bash
   ai-lab-status
   ```

   Wait for `READY`. On `FAILED`, read the bootstrap log before retrying. Type `exit` to leave the
   shell.

2. **The load balancer sees a healthy instance** (with `enable_domain_access = true`):

   ```powershell
   aws elbv2 describe-target-health --target-group-arn (terraform output -raw open_webui_target_group_arn)
   ```

   The target reports `healthy`.

3. **The site is reachable.** Open ==[https://aiwebdemo.click]== in a private window. With Cloudflare
   Access on, you land on the Cognito sign-in page, not Open WebUI. Sign in as a demo user and send
   a chat message; the reply streams in. Accounts and passwords:
   [access-and-network.md](smoke-tests/access-and-network.md#accounts-and-passwords).
   Without domain access, use the [SSM-only test](smoke-tests/access-and-network.md#ssm-only-test).

   **"This site can't be reached ... server IP address could not be found"** right after a rebuild
   is DNS caching, not a fault: destroying the lab removed the record, and resolvers keep that
   "no such name" answer for up to 30 minutes. The control panel still loads because its record is
   never removed. Check that the record exists by asking Cloudflare's nameserver, which has no
   cache:

   ```powershell
   Resolve-DnsName aiwebdemo.click -Server albert.ns.cloudflare.com
   ```

   - **Cloudflare addresses (`104.x` or `172.x`):** the record is fine. Clear your caches and retry:

     ```powershell
     Clear-DnsClientCache
     Resolve-DnsName aiwebdemo.click -Server 1.1.1.1
     ```

     In Chrome, also open `chrome://net-internals/#dns` and choose **Clear host cache**. If your
     router or ISP still has the old answer, wait up to 30 minutes, or set your PC's DNS to
     `1.1.1.1`.
   - **No answer:** the record was not created. In Cloudflare → DNS, look for a proxied CNAME
     `aiwebdemo.click` pointing at the `open_webui_alb_dns_name` output, and check the apply output
     for `cloudflare_dns_record.domain`.

4. **The control panel is wired to this lab** (when `control_panel_url` is set):

   ```powershell
   aws s3 cp "s3://$panelBucket/config.js" -
   $panelApi = aws ssm get-parameter --name "/$projectName/control-panel-api/settings" --query Parameter.Value --output text | ConvertFrom-Json
   aws apigatewayv2 get-authorizer --api-id $panelApi.api_id --authorizer-id $panelApi.authorizer_id --query JwtConfiguration
   ```

   - `config.js` starts with `// Written by Terraform` and has an `apiUrl`. A `null` `apiUrl` means
     the plan showed the `control_panel_api_settings_found` warning.
   - The authorizer's issuer ends with this lab's Cognito pool ID (`terraform output -raw cognito_user_pool_id`).
   - Open ==[https://cp.aiwebdemo.click]==, sign in, and the lab instance is listed.

   The full set of panel checks is in
   [control-panel.md](smoke-tests/control-panel.md#control-panel-config-test).

## Step 5. Test what changed

Run only the tests for what this deploy changed:

| What changed | Tests |
|---|---|
| Domain, Cloudflare or network settings | [Domain access](smoke-tests/access-and-network.md#domain-access-test), [Cloudflare Access](smoke-tests/access-and-network.md#cloudflare-access-test), [Origin lockdown](smoke-tests/access-and-network.md#origin-lockdown-test), [Edge protections](smoke-tests/access-and-network.md#edge-protections-test), [Load balancer hardening](smoke-tests/access-and-network.md#load-balancer-hardening-test), [Outbound restriction](smoke-tests/access-and-network.md#outbound-restriction-test) |
| Cognito or the accounts | [Cognito sign-in](smoke-tests/access-and-network.md#cognito-sign-in-test), [Accounts and passwords](smoke-tests/access-and-network.md#accounts-and-passwords) |
| The control panel pages or its API | [Control panel config](smoke-tests/control-panel.md#control-panel-config-test), [API stack](smoke-tests/control-panel.md#control-panel-api-stack-test), [Open WebUI admin action](smoke-tests/control-panel.md#open-webui-admin-action-test), [Lab unavailable page](smoke-tests/access-and-network.md#lab-unavailable-page-test) |
| Auto-stop or the timer reset | [Auto-stop timer reset](smoke-tests/control-panel.md#auto-stop-timer-reset-test) |
| Grafana telemetry | [Grafana telemetry](smoke-tests/lab-features.md#grafana-telemetry-test) |
| Grafana dashboards, alerts or the panel link | [Grafana dashboards and alerts](smoke-tests/lab-features.md#grafana-dashboards-and-alerts-test) |
| Log search tools | [Log search tools](smoke-tests/lab-features.md#log-search-tools-test) |
| Ollama journal and Open WebUI database metrics | [Metrics test](smoke-tests/lab-features.md#metrics-from-ollama-and-the-open-webui-database-test) |
| The vulnerability findings tool | [Vulnerability MCP server](smoke-tests/lab-features.md#vulnerability-mcp-server-test), then the [acceptance tests](../vuln-mcp-acceptance-tests.md) |

The test pages expect this window's `$env:AWS_PROFILE`, the wrapper, `Get-TfVar`, `$projectName`,
`$domainName`, `$panelBucket` and `$env:instance_id` from steps 1 and 3.

## Step 6. Stop or destroy

**Pause** (keeps everything, stops compute charges):

```powershell
aws ec2 stop-instances --instance-ids $env:instance_id
aws ec2 describe-instance-status --instance-ids $env:instance_id
```

Start it again from the control panel.

**Destroy the lab** (first, if you are also removing the API stack):

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-destroy", "-out=ai-lab-destroy.tfplan")
```

Read the plan, then:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("apply", "ai-lab-destroy.tfplan")
Remove-Item ai-lab-destroy.tfplan
```

Afterwards the instance, load balancer, target group and security groups are gone, and with
Cloudflare on, the proxied apex record and the Access application too. The control panel stays up
and lists no instances; `config.js` is deleted, and the API's sign-in is back on its holding pool
(the `get-authorizer` command in step 4 shows the audience `holding-unused`). The ACM certificate,
its validation record and the Secrets Manager secrets are not managed by Terraform and remain.

**Destroy the API stack** (rarely; the panel is meant to outlive the lab):

```powershell
Set-Location C:\GitHub\ai-cloud-lab\dashboards\api\terraform
terraform plan -destroy "-out=api-destroy.tfplan"
terraform apply api-destroy.tfplan
Remove-Item api-destroy.tfplan
Set-Location C:\GitHub\ai-cloud-lab
```

The destroy stops with an error on the three panel tables and the desired-state table: they have
deletion protection, so your users and grants survive. That is intended. To really delete them, set
`deletion_protection_enabled = false` in `tables.tf` and `webui_admin.tf`, apply, then destroy;
their data is then gone for good. Rebuilding the API stack gives it new IDs, which the next lab
apply picks up by itself (step 3).

If something fails, see [Troubleshooting](troubleshooting.md).
