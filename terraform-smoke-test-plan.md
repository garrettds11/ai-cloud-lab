# Terraform Smoke Test Plan

This plan deploys the AI Cloud Lab, verifies bootstrap and the selected access path, then safely dismantles the test system.

## Two Terraform stacks, and which to run

The repository has two separate Terraform configurations, each with its own state:

| Stack | Folder | Holds | Applied |
|---|---|---|---|
| **Control panel API** | dashboards\api\terraform | The panel's API, sign-in authorizer, routes, both functions and roles, the panel's tables, the Open WebUI admin document | Once, then only when the API changes |
| **Lab** | repository root | The instance, Open WebUI, Ollama, ALB, Cloudflare, Cognito, auto-stop | Often: every build, destroy and rebuild |

Order:

- **First time, or after the API stack was destroyed:** the API stack first (section 2A), then the lab (section 2B). The lab needs the API's IDs, which change whenever the API stack is created; section 2A step 3 puts them in the root example.
- **Every other build:** only the stack whose files changed. Lab changes need section 2B only. Changes to the API stack's inputs (anything in `dashboards\api\terraform`, or `handler.py`, `customer.py`, `admin.py` or `webui-admin.sh` in `dashboards\api`) need section 2A's "Later API changes" only. Page changes need "Deploy the pages" only. If both stacks changed in one pull, do the API stack first.
- **Destroying:** the lab first, then the API stack if you mean to remove it too (section 4).

Always start from the latest code:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
git switch dev
git pull origin dev
```

## 1. Prepare for infrastructure deployment

Run Terraform from the directory containing `main.tf`, `variables.tf`, and the other configuration files:

```powershell
Set-Location C:\code\GitHub\ai-cloud-lab
Set-Location C:\GitHub\ai-cloud-lab
Get-ChildItem *.tf
```

If `Get-ChildItem *.tf` returns *no files*, `STOP` and *change to the correct repository directory* before continuing.

At the start of **every** run, copy the example over the local variables file. `terraform.tfvars.example` is the source of truth and is kept in the repository; `terraform.tfvars` is a git-ignored working copy that each run replaces:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars -Force
```

Then, only if this run needs a different value, edit the copy explicitly with one of the commands below. A lasting change belongs in `terraform.tfvars.example` (committed); an edit made only in the copy is lost at the next run.

```VS-Code
code terraform.tfvars
```

```Notepad
notepad terraform.tfvars
```

Stop after opening Notepad. Before running any Terraform command, replace each
deployment-specific example value in `terraform.tfvars`, then save and close
Notepad. At minimum, review:

- `aws_region` and `aws_profile`
- `open_webui_admin_password_secret_arn`
- `open_webui_demo_user_password_secret_arn`
- `domain_name`, `route53_zone_name`, and `acm_certificate_arn`
- `cloudflare_account_id`
- `cloudflare_api_token_secret_arn`
- `cloudflare_access_allowed_emails`
- `instance_type`, `root_volume_size`, and `llm_model` for the intended test

For the current demo, the shipped values are already populated. A user cloning
the repository should replace them in this one file rather than edit Terraform
source files or repeat them on every command.

For the Cloudflare domain smoke test, uncomment or confirm these settings in
`terraform.tfvars`:

```hcl
enable_domain_access     = true
enable_cloudflare_access = true
```

Replace the deployment-specific values in that file when cloning the project.
The example contains identifiers and ARNs, not secret values. Do not add the
Cloudflare token or Open WebUI passwords to the file.

### Set the test variables

Set only the runtime and credential-selection values in the environment. Change
the `AWS_PROFILE` value below to the profile you intend to use, then leave it
unchanged for the rest of the test. Do not assign `AWS_PROFILE` again later;
Terraform's profile variable and all AWS CLI commands must use the same value.
The deployment configuration is loaded from `terraform.tfvars` by Terraform.
AWS CLI commands will use `AWS_PROFILE` and `AWS_DEFAULT_REGION`, while
Terraform receives the same values through `TF_VAR_aws_profile` and
`TF_VAR_aws_region`.

```powershell
$env:AWS_PROFILE = "ai-cloud-lab"
$env:AWS_DEFAULT_REGION = "us-east-1"
```

After setting the profile and region above, copy this block unchanged:

```powershell
$env:TF_VAR_aws_profile = $env:AWS_PROFILE
$env:TF_VAR_aws_region = $env:AWS_DEFAULT_REGION
```

**Where the profile really comes from.** A value in a `terraform.tfvars` file beats a `TF_VAR_` environment variable, and both example files set `aws_profile = null`. So with the examples as they are, `TF_VAR_aws_profile` is ignored. `null` means "name no profile", and the AWS provider then uses `$env:AWS_PROFILE` from this window. That works only in a window where `$env:AWS_PROFILE` is set. In a new window without it, Terraform would use your **default** profile, which may be a different account.

Keep `aws_profile = null` in both examples and set `$env:AWS_PROFILE` (your own profile name) at the start of every window. Do not put a profile name in only the working `terraform.tfvars`: the next copy from the example removes it. A profile name in the examples would be committed, and would be wrong for anyone whose profile is named differently.

Confirm the account before any plan, apply or destroy, in either stack:

```powershell
aws sts get-caller-identity --query Account --output text
```

It must print the lab's account (`394566733278` for the demo). If it does not, stop and fix the profile.

### Set Cloudflare tokens if provider is in use.

When Cloudflare resources are enabled, Terraform retrieves the Cloudflare API
token from the separate AWS Secrets Manager secret below. Do not paste the token
into the shell or store it in Terraform variables, `terraform.tfvars`, or the
repository. Run Terraform through this wrapper so the token exists only for the
duration of each command:

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

Run every Terraform command that plans, applies, or destroys through the
wrapper, including teardown. Plain `terraform plan`, `apply`, or `destroy`
sends no Cloudflare credentials and fails with
`403 Missing X-Auth-Email header` when Cloudflare is enabled. Paste the wrapper
into the same PowerShell window that runs the later steps; it does not persist
into a new window.

The complete command sequence is in **Section 2B** below (after section 2A the first time). Do not run a separate unsaved `plan` or `apply` here.

Confirm the certificate is `ISSUED`, belongs to the same region as
`TF_VAR_aws_region`, and covers the exact `domain_name` before applying.
The current demo values are in `terraform.tfvars`; replace them there when
using another account, domain, zone, certificate, or Cloudflare account.

## 2A. Control panel API stack

Skip this section on a normal lab build. Do it the first time, after the API stack was destroyed, or when one of its inputs changed: anything in `dashboards\api\terraform`, or `handler.py`, `customer.py`, `admin.py` or `webui-admin.sh` in `dashboards\api` (the functions are built from the first three, and the Open WebUI admin document embeds the last).

The API stack has no Cloudflare resources, so it runs with plain `terraform`, not the wrapper. It uses the same `TF_VAR_aws_profile` and `TF_VAR_aws_region` set in section 1.

### First time (moving from the hand-built API)

The new API is created next to the hand-built one (`65j334bc19`), which keeps serving the panel until section 2B switches the lab over. There is no outage, and `dashboards\api\terraform\README.md` has the rollback.

1. Settings, once:

   Like the lab, this stack's `terraform.tfvars.example` is the source of truth. Copy it over the working copy at the start of every run, and edit the copy only for a one-off change:

   ```powershell
   Set-Location C:\GitHub\ai-cloud-lab\dashboards\api\terraform
   Copy-Item terraform.tfvars.example terraform.tfvars -Force
   notepad terraform.tfvars
   ```

   Check that `lab_project_name` matches the root example's `project_name` (`aiwebdemo` for the demo), and that `bootstrap_admins` has your address.

   Set `adopt_existing_tables` by whether the tables already exist:
   - **`true`** when `panel_users`, `instance_entitlements` and `control_panel_events` already exist, as on this account (built by hand, or left behind by a destroyed API stack). They are imported, data included.
   - **`false`** on a new account. Terraform creates them. With `true` and no tables, the plan fails because there is nothing to import.

   Check with:

   ```powershell
   foreach ($t in 'panel_users', 'instance_entitlements', 'control_panel_events') {
     aws dynamodb describe-table --table-name $t --query "Table.TableName" --output text 2>$null
     if ($LASTEXITCODE -ne 0) { "$t does not exist" }
   }
   ```

   Use `true` only if all three names print, and `false` only if all three say they do not exist. A mix means a table is missing; stop and ask before building.

2. Plan:

   ```powershell
   terraform init
   terraform plan -out=api.tfplan
   ```

   **Read the plan before applying.** Expected:
   - The three tables: **will be imported** (with `adopt_existing_tables = true`), possibly with in-place updates (deletion protection, point-in-time recovery). On a new account, **will be created**.
   - Everything else: **will be created**. That covers the API, the authorizer, 15 routes, the stage, two functions and their roles and log groups, the holding pool, the Open WebUI admin document and the desired-state table.
   - **Nothing** may show **must be replaced** or **will be destroyed**. If a table does, stop: its keys differ from `tables.tf`.

3. Apply, then print the lab's new settings:

   ```powershell
   terraform apply api.tfplan
   Remove-Item api.tfplan
   terraform output -raw lab_tfvars
   ```

   Replace the six matching lines in the **root** `terraform.tfvars.example` with the printed ones (`control_panel_api_url`, `control_panel_api_id`, `control_panel_authorizer_id`, `control_panel_holding_pool_id`, `control_panel_users_table`, `control_panel_entitlements_table`), commit that change, then copy the example over `terraform.tfvars` again before section 2B. Changing only the working copy works for this run but is lost at the next copy, and the lab would then point the panel back at the old API's IDs.

4. Check, before the lab is pointed at it:

   ```powershell
   $newApi = terraform output -raw api_id
   aws apigatewayv2 get-routes --api-id $newApi --query "length(Items)"
   curl.exe -s -o NUL -w "%{http_code}\n" "$((terraform output -raw api_url).TrimEnd('/'))/instances"
   ```

   The first prints `15`. The second prints `401`: until section 2B, the new authorizer trusts only the empty holding pool, so nobody can sign in to the new API yet.

### Later API changes

After a change to any of the stack's inputs (see the start of this section):

```powershell
Set-Location C:\GitHub\ai-cloud-lab\dashboards\api\terraform
terraform plan -out=api.tfplan
terraform apply api.tfplan
Remove-Item api.tfplan
```

Code changes update both functions in place. The API address and IDs do not change, so the lab needs nothing. If the plan ever shows the API, the authorizer or a table being **replaced**, stop: that would change the IDs or lose data.

## 2B. Lab

### Validate and preview
```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("init")
Invoke-TerraformWithCloudflareToken -Arguments @("fmt", "-recursive")
Invoke-TerraformWithCloudflareToken -Arguments @("validate")
Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-out=ai-lab.tfplan")
```

`fmt -recursive` rewrites whitespace and alignment in place, including in your
local `terraform.tfvars`, so the later steps do not fail on formatting. It
changes no values. Use `fmt -check -recursive` only in CI, where the files must
not be modified.

When `control_panel_url` is set, the plan also shows the control panel changes: three
new `aws_ssm_parameter` resources under `/<project_name>/control-panel/`, the
`control-panel=managed` tag on the lab instance, and, if the three authorizer
variables are set, the `terraform_data` resource that points the API's authorizer at
the lab's pool. It also shows a Cloudflare Worker script and route for the lab-hostname fallback. With `control_panel_bucket` set it also shows the `config.js` object, and with
`control_panel_distribution_id` set the cache-clearing step. Section "Control panel
config test", has the checks after `apply`.

**First lab apply after section 2A's first-time steps** (the API IDs in `terraform.tfvars` changed). Also expect:

- `terraform_data.control_panel_authorizer["authorizer"]` **must be replaced**. This is the switch-over. Its destroy step points the old hand-built authorizer back at its holding pool, so the old API starts answering 401, and its create step points the new authorizer at the lab's Cognito pool. If the plan does not show it, stop; see cutover step 5 in `dashboards\api\terraform\README.md`.
- `aws_ssm_parameter.control_panel_open_webui_image` **will be created** (`/<project_name>/control-panel/open-webui-image`).
- The `config.js` object updates with the new `apiUrl`.

**Any lab apply** may also show:

- `aws_instance.ai_lab` **must be replaced** when the bootstrap or a script it embeds changed (for example `scripts/ai-lab-idle-check.sh`). Chats and pulled models on the instance are lost (issue #13).
- In-place changes to the ALB, its listeners and security group, and the SNS topic. These come from the hardening in #57: TLS 1.2+ policy, dropping invalid headers, port 80 limited to Cloudflare under lockdown, ALB egress limited to Open WebUI's port, and the encrypted alert topic.

### Deploy

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("apply", "ai-lab.tfplan")
```

Apply completes when EC2 is running, not necessarily when Ollama, the model, and Open WebUI are ready.

The plan file can contain sensitive values. Do not commit it. After the apply
completes, remove the local plan file:

```powershell
Remove-Item ai-lab.tfplan
```

### Deploy the pages

Needed only when files in `dashboards` (the pages, not `dashboards\api`) changed, and once after the first cutover so the pages include every merged change (for example the Reset button). `config.js` is published by the lab apply and is excluded here:

The bucket and distribution come from the root `terraform.tfvars` (`control_panel_bucket`, `control_panel_distribution_id`), so this works for any deployment. Run from the repository root:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
function Get-TfVar([string]$Name) {
  $m = Select-String -Path .\terraform.tfvars -Pattern ('^\s*' + $Name + '\s*=\s*"([^"]+)"') | Select-Object -First 1
  if (-not $m) { throw "$Name is not set in terraform.tfvars" }
  $m.Matches.Groups[1].Value
}
$panelBucket       = Get-TfVar control_panel_bucket
$panelDistribution = Get-TfVar control_panel_distribution_id
aws s3 sync .\dashboards "s3://$panelBucket" --exclude "*.md" --exclude "config.example.js" --exclude "config.js" --exclude "api/*" --exclude "_deploy/*" --delete
aws cloudfront create-invalidation --distribution-id $panelDistribution --paths "/*"
```

## 3. Access and testing system

### Status check path to server

Check ALB target health from PowerShell:

```powershell
$env:alb_target_group_arn = terraform output -raw open_webui_target_group_arn
aws elbv2 describe-target-health `
  --target-group-arn $env:alb_target_group_arn
```

The EC2 target should report `healthy`. 

### Verify reachability

Choose exactly one access path based on `TF_VAR_enable_domain_access`.

### Domain-access test

When `TF_VAR_enable_domain_access` is `true`, do not start an *SSM port-forwarding*
session. The SSM shell in this section is still used for readiness and troubleshooting;
the ALB is the public entry point for browser access.

- URL loads Open WebUI: https://aiwebdemo.click

> If `enable_cloudflare_access = false` this check returns a `500` internal error,
> otherwise it should show a branded Cognito login page.

After apply, capture the instance ID in an environment variable to set the SSM target and open a shell:

```powershell
$env:instance_id = terraform output -raw instance_id
aws ssm start-session --target $env:instance_id
```

Inside the SSM shell, check bootstrap readiness:

```bash
if test -f /var/lib/ai-lab/ready; then
  echo READY
elif test -f /var/lib/ai-lab/failed; then
  echo FAILED
else
  echo NOT_READY
fi

tail -n 100 /var/log/ai-lab-bootstrap.log
sudo docker ps
ai-lab-status
```

Wait for `READY` before opening Open WebUI. If the result is `FAILED`, inspect the bootstrap log before retrying.

The SSM shell is a Linux shell. Run Linux commands there; run PowerShell commands such as `curl.exe` from a separate Windows PowerShell window.

**The project’s EC2 host runs `Ubuntu Server 24.04 LTS` (amd64)** and [main.tf](https://github.com/garrettds11/ai-cloud-lab/blob/dev/main.tf) selects its AMI through AWS’s Canonical SSM parameter:

```text
/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
```

`Open WebUI` runs **inside a Docker container on that Ubuntu host**. The container has its own userspace, which can differ from Ubuntu, while sharing the host’s Linux kernel.

From your SSM shell, check the host OS with:

```bash
cat /etc/os-release
```

To check the Open WebUI container’s OS:

```bash
sudo docker ps --format '{{.Names}}' |
while read -r container_name; do
    printf '\nContainer: %s\n' "$container_name"
    sudo docker exec "$container_name" cat /etc/os-release
done
```

Ollama runs directly on the Ubuntu host as a systemd service. The model weights are stored on the host’s disk and loaded by Ollama for inference.

```bash
systemctl status ollama
ollama list
```

### Origin lockdown test

Skip this section unless `enable_origin_lockdown = true`. Before enabling it,
confirm the public site works, and run the direct check below once to see the
"before" result: it connects and shows the Open WebUI login page.

After applying with lockdown on, from a network that is not in
`origin_lockdown_extra_cidrs`:

```powershell
$env:alb_dns_name = terraform output -raw open_webui_alb_dns_name
curl.exe -k -I --max-time 6 -H "Host: aiwebdemo.click" "https://$env:alb_dns_name"
```

The request must time out or fail to connect. It must not return a response from
Open WebUI. Then confirm the public path still works:

```powershell
curl.exe -I https://aiwebdemo.click
```

This should still return a `302` from Cloudflare Access.

### Cloudflare Access test

When `TF_VAR_enable_cloudflare_access` is `true`, Cloudflare Access sits in
front of Open WebUI and checks every visitor before the Open WebUI login page
loads. The login method depends on the other settings:

- **`enable_cognito = true` (this repo's default):** Cognito is the only login
  method on the Access app, so Access sends you straight to the Cognito sign-in
  page. There is no Access method-choice page and no one-time PIN.
- **`enable_cognito = false`:** Access shows the login methods you configured in
  Zero Trust. New accounts offer only the **Cloudflare** identity provider, which
  is limited to members of your Cloudflare account. Add One-time PIN under
  **Zero Trust > Integrations > Identity providers** if other people need to sign
  in.

In both cases the email must be in the Access policy: with Cognito, that is every
Cognito user's email plus `cloudflare_access_allowed_emails`; without Cognito, it
is `cloudflare_access_allowed_emails` only.

**Run these from a browser:**

1. In a private window, open https://aiwebdemo.click. You must land on a
   sign-in page (Cognito, or the Access page when Cognito is off), not the Open
   WebUI login page.
2. Sign in with a user that is **not** in the Access policy, if you have one.
   Access must deny it.
3. Sign in with an approved user. You must reach the Open WebUI login page.
   With Cognito, choose **Continue with Cognito**; it reuses the Cognito
   session, so there is no second password prompt.
4. In Open WebUI, confirm the security and acceptable use banner shows at the
   top (see `security_banner_text`), send a chat message, and confirm the reply
   streams in (this exercises API calls and websockets through Cloudflare).
5. Confirm the apex record is proxied: `nslookup aiwebdemo.click` must return
   Cloudflare addresses, not the ALB address.
6. In the Cloudflare dashboard, confirm SSL/TLS is **Full (strict)** and that
   Security > WAF shows the Free Managed Ruleset enabled.

This should return a `302` to Cognito or `cloudflareaccess.com` rather than an
Open WebUI page.

```Powershell
curl.exe -I https://aiwebdemo.click
```

### Control panel config test

*Verify that the control panel is connected to the Cognito pool and API for the deployment.*

Skip this section unless `enable_cognito` is `true` and `control_panel_url` is set
in `terraform.tfvars`. Terraform creates the control panel's Cognito app client
and publishes the panel's `config.js` into its bucket during `apply`, then clears
that file from CloudFront's cache. This needs `control_panel_bucket`, and
`control_panel_distribution_id` for the cache. There is nothing to write or upload
by hand.

The checks below need values that live in `terraform.tfvars`. Read them into
variables first, from the repository directory, in the same PowerShell window as
`AWS_PROFILE`:

```powershell
function Get-TfVar([string]$Name) {
  $m = Select-String -Path .\terraform.tfvars -Pattern ('^\s*' + $Name + '\s*=\s*"([^"]+)"') | Select-Object -First 1
  if (-not $m) { throw "$Name is not set in terraform.tfvars" }
  $m.Matches.Groups[1].Value
}
$projectName       = Get-TfVar project_name
$domainName        = Get-TfVar domain_name
$panelApiId        = Get-TfVar control_panel_api_id
$panelAuthorizerId = Get-TfVar control_panel_authorizer_id
$panelBucket       = Get-TfVar control_panel_bucket
```

Check:

1. `aws s3 cp "s3://$panelBucket/config.js" -` prints a
   file that starts with `// Written by Terraform` and holds the region, the user
   pool ID, an app client ID, the API address and the `redirectUri`
   `https://cp.aiwebdemo.click/`. A `null` for `apiUrl` means `control_panel_api_url`
   is not set in `terraform.tfvars`.
2. Open `https://cp.aiwebdemo.click`. The page must show a sign-in button, not a
   message about a missing `clientId` or a missing settings file. If it shows an old
   message, the cache clearing did not run: check the `apply` output for the
   `terraform_data.control_panel_config_cache` step.
3. In the Cognito console, the user pool has a second app client named
   `<project_name>-control-panel` with no client secret.
4. If `control_panel_users_table` is set, the DynamoDB table it names has a row for
   each of the 11 demo accounts: `demo1@example.local`, `demo3@example.local` and the
   other odd demo users with `operators` and `user_mgrs` in `roles`;
   `demo2@example.local` and the other even demo users with `operators`;
   `admin@example.local` with `operators` and `admin`. Only an administrator can change
   roles, and any administrator can make another.
   If `control_panel_entitlements_table` is also set, the table it names has one row per
   demo user (all 11, because all have `operators`) for the lab instance, with
   `status` `applied` and `grantedBy` `terraform`. Signed in as `demo1@example.local`
   (or any demo user), the Instances page lists the lab instance. Signed in as an
   administrator, it lists every managed instance with or without a grant.

5. If `control_panel_api_id`, `control_panel_authorizer_id` and
   `control_panel_holding_pool_id` are set, `apply` pointed the control panel API's authorizer at
   this lab's pool. Check:

   ```powershell
   aws apigatewayv2 get-authorizer --api-id $panelApiId --authorizer-id $panelAuthorizerId --query JwtConfiguration
   ```

   The issuer must end with the lab's `cognito_user_pool_id` and the audience must be the
   control panel app client ID. After `destroy`, the same command must show the holding
   pool's issuer and the audience `holding-unused`.

   After the first cutover, `$panelApiId` and `$panelAuthorizerId` are the new IDs from
   section 2A. Also check that the old hand-built authorizer went back to its holding pool:

   ```powershell
   aws apigatewayv2 get-authorizer --api-id 65j334bc19 --authorizer-id pnlj78 --query JwtConfiguration
   ```

   Its issuer must end with `us-east-1_xcTOLNQJM` and its audience must be `holding-unused`.

6. The lab publishes its wiring for the panel. With `control_panel_url` set, `apply`
   creates three SSM parameters and tags the lab instance `control-panel=managed`.
   Check:

   ```powershell
   aws ssm get-parameters-by-path --path "/$projectName/control-panel" --query "Parameters[].[Name,Value]" --output table
   aws ec2 describe-instances --instance-ids $env:instance_id --query "Reservations[].Instances[].Tags[?Key=='control-panel']"
   ```

   The parameters are `instance-ids` (the lab instance ID), `open-webui-image` (the
   `open_webui_container_image` value), `target-group-arn` and `service-url`
   (`https://$domainName`). `target-group-arn` and `service-url` exist only when
   `enable_domain_access` is also `true`. The tag value is `managed`.

7. Sign in at https://cp.aiwebdemo.click as a user in the lab's Cognito pool. The
   panel must list the lab instance with no setting copied into the Lambda functions.
   Only a sign-in whose email is in `BOOTSTRAP_ADMINS` (`garrettds11@gmail.com`) can
   open the admin screens; other users get only the customer view.

   After `destroy`, the parameters are gone and the panel lists no instances. The
   `config.js` object is deleted too, so the page reports that it has no settings file.

8. If the instance is shutdown and you need to restart it or check its status, use these commands:

```powershell
aws ec2 stop-instances `
  --instance-ids $env:instance_id
```

```powershell
aws ec2 describe-instance-status `
  --instance-ids $env:instance_id
```

### Control panel API stack test

Skip this until section 2A has been applied and the lab pointed at it. Run from `dashboards\api\terraform`.

1. **Panel end to end:** sign in at https://cp.aiwebdemo.click. Expected:
   - The instances are listed.
   - Start, Reset and Access behave as before.
   - As an administrator, User management, Changes, Logins and Logs all open.

   All of these now go through the new API (`config.js` has its address, check with `aws s3 cp "s3://$panelBucket/config.js" -`).
2. **Routes match the spec:**

   ```powershell
   aws apigatewayv2 get-routes --api-id (terraform output -raw api_id) --query "sort(Items[].RouteKey)" --output text
   ```

   It must list the same 15 routes as `dashboards\api\openapi.yaml`.
3. **Access log:** after using the panel for a minute,

   ```powershell
   aws logs tail "/aws/apigateway/aiwebdemo-control" --since 10m
   ```

   Each line has the route, the status and the caller's email. No line may contain `Bearer`, `eyJ` (the start of a token) or a request body.
4. **Tables kept their data:** User management shows the same people, roles and grants as before the cutover.
5. **The tables are protected:**

   ```powershell
   foreach ($t in 'panel_users', 'instance_entitlements', 'control_panel_events') {
     aws dynamodb describe-table --table-name $t --query "Table.DeletionProtectionEnabled"
   }
   ```

   All three print `true`.

### Open WebUI admin action test

**Pending manual execution.** Needs the lab running, `READY`, and section 2A applied. The panel pages have no button for this yet (phase 1, #59), so the test calls the document and the API directly.

1. **The document alone**, from `dashboards\api\terraform`:

   ```powershell
   $doc = terraform output -raw webui_admin_document
   $cmd = aws ssm send-command --document-name $doc --instance-ids $env:instance_id --parameters "action=status,expectedVersion=0.11.4" --query Command.CommandId --output text
   Start-Sleep 15
   aws ssm get-command-invocation --command-id $cmd --instance-id $env:instance_id --query "[Status, StandardOutputContent]" --output text
   ```

   Expected: `Success` and one JSON line with `"ok":true`, `"healthy":true`, `"version":"0.11.4"`, `"versionMatches":true`. If `version` is `null` while `healthy` is `true`, Open WebUI v0.11.4 does not serve `/api/version` without sign-in; record that in #59.

2. **The document refuses anything else:**

   ```powershell
   aws ssm send-command --document-name $doc --instance-ids $env:instance_id --parameters "action=whoami"
   aws ssm send-command --document-name $doc --instance-ids $env:instance_id --parameters "action=status,expectedVersion=1;id"
   ```

   Both must fail at once with an `InvalidParameters` error. Nothing runs on the instance.

3. **Through the API, as an administrator:** sign in to the panel as an administrator, press F12, open **Console**, and run:

   ```javascript
   const t = JSON.parse(sessionStorage.getItem('panel.tokens')).idToken;
   const api = (window.PANEL_CONFIG?.apiUrl || prompt('API address (terraform output api_url)')).replace(/\/+$/, '');
   const id = 'i-...'; // the lab instance ID
   const r = await fetch(api + '/admin/webui/actions', {method: 'POST', headers: {authorization: 'Bearer ' + t, 'content-type': 'application/json'}, body: JSON.stringify({action: 'status', instanceId: id})});
   const started = await r.json(); console.log(r.status, started);
   setTimeout(async () => console.log(await (await fetch(api + '/admin/webui/actions/' + started.commandId, {headers: {authorization: 'Bearer ' + t}})).json()), 20000);
   ```

   Expected:
   - The first log is `200` with a `commandId` and `status: "Pending"`.
   - Twenty seconds later the second shows `status: "Success"` and the same `result` as step 1.
   - **Logs** in the panel shows `Requested Open WebUI action 'status' on ...`, then `... done`.

   If `window.PANEL_CONFIG` is undefined, paste the `api_url` output when asked.

4. **Not for operators:** repeat step 3 signed in as an operator (for example `demo2@example.local`). The first request must return `403`.

### Lab unavailable page test

Needs `control_panel_url` set and the Cloudflare token to have **Account: Workers Scripts Edit**
and **Zone: Workers Routes Edit** (a 403 on the Worker resources during apply means it does not).

1. With the instance stopped (the commands above), open the lab address in a browser, for
   example `https://<domain_name>`, after signing in through Access. Expected: a redirect
   to `https://<control_panel_url>/`, not a Cloudflare or load balancer error.
2. The control panel opens. Start the lab from there.
3. When the lab is healthy again, the same address loads Open WebUI normally.
4. Calls that are not page loads are untouched: in a shell,
   `curl.exe -s -o NUL -w "%{http_code}" https://<domain_name>/health` while the lab is
   stopped should still return a plain error code (no HTML), because curl does not ask for
   HTML.

### Login with the admin and demo accounts

Initial login credentials are as follows unless you changed `open_webui_admin_email`:

```
admin@example.local
``` 

- Password is set in the pre-created Secrets Manager secret identified by the `open_webui_admin_password_secret_arn` output.
- Display name: `Lab Admin` unless you changed `open_webui_admin_name`.

The demo users are numbered 1 through 10, like:

```
demo1@example.local
``` 

They all use the value stored in the Secrets Manager secret identified by
`open_webui_demo_user_password_secret_arn`.

The Secrets Manager secret must already exist and contain the desired admin
password. The value may be plain text or a one-key key/value secret; bootstrap
extracts the single string value from either format. Terraform only grants the
instance role read access and retrieves the password during bootstrap. Terraform
does not create, update, or destroy this secret. To rotate an existing lab,
change the password in Open WebUI first, then update the matching value in the
AWS console.

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\set-cognito-passwords.ps1"
```

Retrieve the two initial passwords from Secrets Manager when you need them for
login verification. These commands print the values to the current terminal;
do not paste the output into tickets, source files, or logs:

```powershell
$adminPasswordSecretArn = "arn:aws:secretsmanager:us-east-1:394566733278:secret:openwebui-admin-pass-DuXz9K"
$demoPasswordSecretArn = "arn:aws:secretsmanager:us-east-1:394566733278:secret:open_webui_demo_user_password-2eFYcl"

aws secretsmanager get-secret-value `
  --secret-id $adminPasswordSecretArn `
  --query SecretString `
  --output text `
  --region $env:AWS_DEFAULT_REGION `
  --profile $env:AWS_PROFILE

aws secretsmanager get-secret-value `
  --secret-id $demoPasswordSecretArn `
  --query SecretString `
  --output text `
  --region $env:AWS_DEFAULT_REGION `
  --profile $env:AWS_PROFILE
```

The first value is used for `admin@example.local`, both its local Open WebUI
account and its Cognito user; the second is the password shared by the demo
users. With `enable_cognito = false`, each demo user should change it from
Profile after first login.

Where they sign in depends on `enable_cognito`:

- **`true` (default):** the admin is a local Open WebUI account and a Cognito
  user. The demo users are Cognito users only, with no local account. Sign in
  with **Continue with Cognito**; run `.\scripts\set-cognito-passwords.ps1` after
  `apply` first (see "Cognito sign-in test").
- **`false`:** the demo users are local Open WebUI accounts that sign in with
  the password form. Have each user open Profile and change that temporary
  password before using the account.

To turn local password sign-in off, set `open_webui_enable_local_login = false`
(see "Where each account lives" in the README).

==*Change the temporary password immediately*== after confirming access.

### Cognito sign-in test

Skip this section unless `enable_cognito` is `true` in `terraform.tfvars`.
Terraform creates the Cognito users without passwords. After `apply` completes,
set them from the same PowerShell window, with `AWS_PROFILE` and
`AWS_DEFAULT_REGION` already set:

Then, in a private window:

1. Open https://aiwebdemo.click. Access must send you straight to the Cognito
   sign-in page (no login-method choice).
2. Sign in with `demo1@example.local` and the demo password. You must reach Open
   WebUI.
3. If Open WebUI shows its own login page, choose **Continue with Cognito**. It
   must sign you in without asking for a password. The demo user has no local
   account, so Open WebUI creates it now with the `open_webui_default_user_role`
   role (default `user`, so there is no activation wait).
4. Sign out of Open WebUI. You must land on the Cognito sign-in page (or the
   site's sign-in), never on a Cognito "Client does not exist" error, and
   opening https://aiwebdemo.click again must ask for credentials. Then repeat
   with `admin@example.local` and the administrator
   password. Open WebUI must open the existing local admin account, and **Admin
   Panel** must be available.
5. Sign in as a user who is not in the Cognito pool. Access must deny it.

### SSM-only test

For an SSM-only run, set the mode explicitly before planning so a stale domain
environment variable cannot select the wrong test path:

```powershell
$env:TF_VAR_enable_domain_access = "false"
```

Then run this in a second PowerShell terminal:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --document-name AWS-StartPortForwardingSession `
  --parameters portNumber="8080",localPortNumber="8080"
```

Open: http://localhost:8080

If local port 8080 is already in use, keep the remote port at 8080 and use a
different local port:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --document-name AWS-StartPortForwardingSession `
  --parameters "portNumber=8080,localPortNumber=8081"
```

Then open http://localhost:8081.

### Outbound restriction test

The instance may make outbound connections only on TCP 443 and 80. In an SSM shell
on the instance (`aws ssm start-session --target $env:instance_id`):

```powershell
$env:instance_id = terraform output -raw instance_id
aws ssm start-session --target $env:instance_id
```

```bash
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 10 https://ollama.com
curl -sS --max-time 8 http://portquiz.net:8080 || echo "blocked"
```

The first command must print an HTTP status. The second must time out and print
`blocked`. If bootstrap itself failed on a download, check the bootstrap log for the
host it could not reach and add that destination to `extra_egress_cidrs`.

### Edge protections test

1. **Bot Fight Mode:** with it on, sign in through Cloudflare Access, send a chat message, and confirm the reply streams in. If any step fails, turn Bot Fight Mode off and re-test.
2. **DNSSEC:** run these in PowerShell. Google's resolver validates DNSSEC, so a bad key shows up as a failure.

```powershell
Clear-DnsClientCache
Resolve-DnsName <domain> -Type DS -Server 8.8.8.8
Resolve-DnsName <domain> -Type A -Server 8.8.8.8
```

The DS record's key tag must equal the one in the Route 53 DNSSEC keys table, and the A lookup must not return `DNS server failure`. In a browser, `https://dns.google/resolve?name=<domain>&type=A` should show `"AD": true`.

### Load balancer hardening test

Needs `enable_domain_access = true`. These are the #57 changes. `$projectName` comes from the "Control panel config test"; if this is a new window, set it first: `$projectName = '<project_name from terraform.tfvars>'`.

```powershell
$alb = aws elbv2 describe-load-balancers --names "$projectName-alb" --query "LoadBalancers[0]" | ConvertFrom-Json
aws elbv2 describe-listeners --load-balancer-arn $alb.LoadBalancerArn --query "Listeners[?Port==``443``].SslPolicy" --output text
aws elbv2 describe-load-balancer-attributes --load-balancer-arn $alb.LoadBalancerArn --query "Attributes[?Key=='routing.http.drop_invalid_header_fields.enabled'].Value" --output text
aws ec2 describe-security-groups --group-ids $alb.SecurityGroups --query "SecurityGroups[].IpPermissionsEgress"
```

Expected, in order:
1. `ELBSecurityPolicy-TLS13-1-2-2021-06`.
2. `true`.
3. One egress rule: TCP on the Open WebUI port (8080) to the VPC's ranges only.

With origin lockdown and the port 80 redirect both on, the port 80 rule allows only Cloudflare ranges. The site still loads through Cloudflare after all of this; run the Cloudflare Access test's chat step again.

### Grafana telemetry test

Telemetry is on by default; skip this if `enable_grafana_telemetry = false`. In an SSM shell on the instance:

```bash
sudo systemctl status alloy --no-pager
sudo journalctl -u alloy -n 50 --no-pager
```

Alloy must be `active (running)` and the log must not repeat authentication or connection errors (a `401` means the instance ID or token in the secret is wrong). If the bootstrap log printed `WARNING: Grafana telemetry setup failed`, run `grep -i -B5 WARNING /var/log/ai-lab-bootstrap.log`.

Then in Grafana Cloud, open **Explore** and check, after a few minutes:

- Metrics: query `node_load1` and `node_systemd_unit_state{name="ollama.service"}`. Both should return recent values.
- Traces: after you have used Open WebUI for a minute (sign in, send a chat message), open Explore with the Tempo traces data source and search for service `open-webui`. If nothing appears, check `sudo docker logs open-webui 2>&1 | grep -i otel`.
- Logs: pick the Loki logs data source and look for recent entries from the lab, such as the bootstrap log lines or Open WebUI container output. Label names in Grafana can differ from the ones in the Alloy config, so browse the available labels.

Confirm no secret appears in a log line, and that the instance still needs no inbound rule for this (`aws ec2 describe-security-groups` shows only the inbound rules you chose).

### Vulnerability MCP server test

Skip this if `vuln_mcp_table_name` is not set. The findings table (`aiwebdemo-vuln-findings`, created by `create_vuln_table.py` in the `sec-data` repository) and the token secret are built by hand, so do the first step before this section.

Before section 2B, create the token secret and put its ARN in `vuln_mcp_token_secret_arn` (in `terraform.tfvars.example` first, then `terraform.tfvars`):

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token = [Convert]::ToBase64String($bytes)
aws secretsmanager create-secret --name vuln-mcp-token-aiwebdemo --secret-string $token --region us-east-1 --query ARN --output text
```

After apply, call the function with the token (the `$token` variable above, if it is still set in this window) and without it:

```powershell
$url = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_url")   # local Windows PowerShell
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $token" } -Body $body | ConvertTo-Json -Depth 5
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Body $body
```

The first call must list six tools. The second must fail with `401`. Then check that a tool reads the table:

```powershell
$call = '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"summarize_findings","arguments":{"group_by":"severity","status":"open"}}}'
Invoke-RestMethod -Uri $url -Method Post -ContentType 'application/json' -Headers @{ Authorization = "Bearer $token" } -Body $call | ConvertTo-Json -Depth 10
```

With the standard test data this shows 9 open Critical findings. If a call returns an error, read the function's CloudWatch log group, whose name the `vuln_mcp_log_group` output gives:

```powershell
$logGroup = Invoke-TerraformWithCloudflareToken -Arguments @("output", "-raw", "vuln_mcp_log_group")
aws logs tail $logGroup --since 15m
```

Each tool call is one JSON `tool_call` line (tool, arguments, `ok`, size, time). No token or finding data is logged.

#### Automatic Open WebUI registration

The lab instance connects Open WebUI to the server by itself at the end of bootstrap; there is no endpoint or token to copy. The connection is added when `vuln_mcp_table_name` is set and removed when it is not. It needs `open_webui_enable_local_login = true` (Terraform warns otherwise). Because this changes the instance's cloud-init, the apply that introduces it, and later turning the feature on or off, replaces the instance.

Linux (SSM shell on the lab instance; open it with `aws ssm start-session --target $env:instance_id` from PowerShell, as in section 3):

```bash
grep -F '[register-vuln-mcp]' /var/log/ai-lab-bootstrap.log
sudo ai-lab-register-vuln-mcp --check
sudo ai-lab-register-vuln-mcp
sudo ai-lab-register-vuln-mcp
```

Expected, with the feature on:

- The bootstrap log shows `Open WebUI connected to the MCP server and listed 6 tools: find_hosts_by_vulnerability, get_data_dictionary, get_finding, get_host_findings, list_findings, summarize_findings` and `Connection registered.`
- `--check` ends with `Check only. Connection is already correct (feature true).`
- Each of the two plain runs ends with `Connection already correct; nothing to change.` (repeating adds no second connection).
- The log and the output contain no token or password.

Open WebUI (browser, signed in as the admin): **Admin Settings > External Tools** shows exactly one **Vulnerability Findings** connection (MCP, enabled), and its verify button succeeds.

With the feature off (`vuln_mcp_table_name = null`): `--check` ends with `Connection is already correct (feature false)` and **External Tools** has no Vulnerability Findings entry.

If the bootstrap log has `WARNING: Open WebUI vulnerability MCP registration did not finish`, read the lines above it and use the troubleshooting table in `lambda/vuln_mcp/README.md`, then run `sudo ai-lab-register-vuln-mcp` again. Exit code 3 means local login is off and the connection must be added by hand (also in that README).

#### Model-driven acceptance tests

Whether a model actually calls the tools is tested by hand in [docs/vuln-mcp-acceptance-tests.md](docs/vuln-mcp-acceptance-tests.md). They use a separate table loaded from the repository's test fixture so the answers are exact, and they require proof that the tool ran (chat tool-call entry plus a CloudWatch `tool_call` line), not just a plausible answer. All of them are pending manual execution.

### Auto-stop timer reset test

**All steps below are pending manual execution; nothing here has been run against a live lab.** Offline tests cover the logic with mocked AWS (`tests/test_idle_check_timer_reset.py`, `tests/test_auto_stop_watchdog.py`, `dashboards/api/tests/test_handler.py`); they do not prove the live SSM, IAM or API Gateway wiring this section checks.

Before starting, the panel must be on the Terraform-built API (section 2A), which has the reset route and its IAM permission. On the old hand-built API, do "Adding the timer reset route to an existing panel" in `dashboards/api/README.md` first. Apply the lab with a short hard limit, for example `auto_stop_max_uptime_minutes = 20` (the minimum is 15) and `auto_stop_idle_minutes = 0`.

Browser (control panel, signed in as an operator with a grant on the running lab):

1. A reset icon (circular arrow around a clock) sits between Play and Access. Hovering shows `Reset auto-stop timer`.
2. Press it. A dialog asks `Extend this lab for another full session?` and names the new stop time. **Cancel** changes nothing (the `Stops at about` line does not move).
3. Press it again and choose **Reset timer**. A toast says `Auto-stop timer reset. The lab has another 20 minutes.` Under the green Ready indicator the status shows a new `Stops at about ...` time 20 minutes ahead and a green `Timer reset at ...` line. A page refresh keeps both.
4. Open **Logs**. There is one new line from the control panel: `Reset the auto-stop timer for <name>: another 20 minutes, stopping at about <HH:MM> UTC`. As an administrator, **Logs** shows the same line with the operator's name.
5. The button is greyed out when the lab is stopped, and for a lab with no hard limit. A user with the operator role but no grant for the lab sees no lab and no button.

Local Windows PowerShell:

```powershell
$param = (terraform output -json auto_stop_reset_parameter | ConvertFrom-Json).name
aws ssm get-parameter --name $param --query Parameter.Value --output text
[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
```

The first number is the reset time and should be within a minute of the second just after pressing the button. Then run the watchdog once and read its result:

```powershell
$fn = terraform output -raw auto_stop_watchdog
aws lambda invoke --function-name $fn watchdog-out.json | Out-Null
Get-Content watchdog-out.json
```

Expected: `timer_reset_at` is present and `hard_limit_elapsed_minutes` is small (minutes since the reset, not since boot).

Linux (SSM shell on the lab instance):

```bash
sudo AI_LAB_DRY_RUN=1 /usr/local/sbin/ai-lab-idle-check
```

Expected: `hard_limit_minutes=N/20` where N is minutes since the reset, while `uptime_minutes` is larger.

Then, with the lab left running past its original 20 minute deadline and not reset again:

- It is still running at the original deadline, and the stop warning email (about 10 minutes before a short limit) arrives relative to the new deadline, not the old one.
- It stops about 20 minutes after the reset (the instance monitor within a minute, or the watchdog within about 5 more minutes).
- Start it again from the panel: no `Timer reset at` line, and `ai-lab-idle-check` counts `hard_limit_minutes` from the new boot (the earlier reset is ignored).

Terraform drift check, after a reset: `terraform plan` shows no change to `aws_ssm_parameter.auto_stop_reset`.

**A bad reset value cannot switch the limit off** (#56). With the lab running and past its hard limit's halfway point, write a far-future value by hand, as a mistake or a misused permission would:

```powershell
aws ssm put-parameter --name $param --value 9999999999 --overwrite
```

Expected:
- `sudo AI_LAB_DRY_RUN=1 /usr/local/sbin/ai-lab-idle-check` on the instance shows `hard_limit_minutes` counted from boot (equal to `uptime_minutes`), and the system log has `ignoring an invalid or future timer reset value`.
- Running the watchdog as above returns no `timer_reset_at`, and `hard_limit_elapsed_minutes` counts from launch.
- The panel shows no `Timer reset at` line.
- The lab stops on the original schedule.

Repeat with `99999999999999999999` and with `abc`; the result must be the same, and the watchdog must not error. Afterwards put a sane value back, or press Reset in the panel:

```powershell
aws ssm put-parameter --name $param --value 0 --overwrite
```

## 4. Stop or destroy the test system

Order: **the lab first, then the API stack** if you are removing it too. Destroying the lab points the panel's sign-in back at the holding pool, which needs the API to still exist. Rebuild the other way round: the API stack (section 2A, with `adopt_existing_tables = true` if the tables survived, which they do unless you turned deletion protection off and deleted them), copy the new `lab_tfvars` lines into the root `terraform.tfvars`, then the lab.

### The lab

If you may test again later, stop the instance to avoid ongoing compute charges:

```powershell
aws ec2 stop-instances `
  --instance-ids $env:instance_id
```

For permanent cleanup, review the destroy plan and then remove all Terraform-managed resources.
Use the wrapper from section 1; plain `terraform` cannot authenticate to Cloudflare:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-destroy", "-out=ai-lab-destroy.tfplan")
```

Review the plan, then apply it and remove the plan file:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("apply", "ai-lab-destroy.tfplan")
Remove-Item ai-lab-destroy.tfplan
```

After teardown, verify that the instance, ALB, target group, and security groups
are gone. The panel stays up and lists no instances; the API's sign-in is back on its holding pool (the `get-authorizer` check in "Control panel config test", step 5). When Cloudflare is enabled, also confirm the proxied apex CNAME and
the Access application are removed in the Cloudflare dashboard. Terraform does
not manage the ACM certificate, its DNS-only validation CNAME, or the Secrets
Manager secrets, so those remain. Remove `terraform.tfvars` if it contains a
real password, but keep `terraform.tfvars.example`.

### The control panel API stack

Usually left in place: the panel should keep working with or without a lab. To remove it, from `dashboards\api\terraform`:

```powershell
terraform plan -destroy -out=api-destroy.tfplan
terraform apply api-destroy.tfplan
Remove-Item api-destroy.tfplan
```

The three panel tables and the desired-state table have deletion protection, so the destroy stops with an error on them. They stay in AWS with your users and grants. That is intended. To really delete them, set `deletion_protection_enabled = false` in `tables.tf` and `webui_admin.tf`, apply, then destroy. Their data is then gone for good.

Rebuilding the API stack gives the API new IDs and a new address, so the lab must be applied again with the new `lab_tfvars` lines (section 2A step 3, then section 2B).

## Likely failure points

> - AWS credentials are missing, expired, or lack EC2, IAM, security-group, or SSM permissions.
> - The selected region has no default VPC.
> - The account lacks `c7i.4xlarge` quota, or the selected subnet's AZ does not offer that instance type.
> - The Session Manager plugin is not installed locally.
> - Bootstrap is still downloading packages, Ollama, the model, or the Open WebUI image.
> - Local port 8080 is already occupied; use local port 8081 for the tunnel.
> - Domain access requires an issued ACM certificate in the selected region, plus either a public Route 53 hosted zone or, with Cloudflare, an **Active** Cloudflare zone.
> - A Terraform command run without the wrapper fails with `403 Missing X-Auth-Email header` because no Cloudflare token is set. (The API stack in `dashboards\api\terraform` does not need the wrapper.)
> - The API stack plan shows a table **must be replaced**: the hand-built table's keys differ from `tables.tf`. Stop; do not apply.
> - After the cutover the panel answers 401 to everyone: the lab apply did not replace `terraform_data.control_panel_authorizer`, so the new authorizer is still on its holding pool. Apply the lab with `-replace='terraform_data.control_panel_authorizer["authorizer"]'`.
> - The panel still uses the old API: the pages are cached. Reload, or check `config.js` and the CloudFront invalidation.
> - An Open WebUI action fails with a 409 about Systems Manager: the instance has not registered with SSM yet. Wait a minute after it reaches `READY`.
