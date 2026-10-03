# Terraform Smoke Test Plan

This plan deploys the AI Cloud Lab, verifies bootstrap and the selected access path, then safely dismantles the test system.

## 0. Open the repository directory

Run Terraform from the directory containing `main.tf`, `variables.tf`, and the other configuration files:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
Get-ChildItem *.tf
```

If `Get-ChildItem *.tf` returns no files, stop and change to the correct repository directory before continuing.

Create the local variables file once:

```powershell
Copy-Item terraform.tfvars.example terraform.tfvars
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
- `instance_type`, `root_volume_size`, and `ollama_model` for the intended test

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

## 1. Set the test variables

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

The Secrets Manager secret must already exist and contain the desired admin
password. The value may be plain text or a one-key key/value secret; bootstrap
extracts the single string value from either format. Terraform only grants the
instance role read access and retrieves the password during bootstrap. Terraform
does not create, update, or destroy this secret. To rotate an existing lab,
change the password in Open WebUI first, then update the matching value in the
AWS console.

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

The complete command sequence is in **2. Validate and preview** and **3.
Deploy** below. Do not run a separate unsaved `plan` or `apply` here.

Confirm the certificate is `ISSUED`, belongs to the same region as
`TF_VAR_aws_region`, and covers the exact `domain_name` before applying.
The current demo values are in `terraform.tfvars`; replace them there when
using another account, domain, zone, certificate, or Cloudflare account.

## 2. Validate and preview

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

## 3. Deploy

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("apply", "ai-lab.tfplan")
```

Apply completes when EC2 is running, not necessarily when Ollama, the model, and Open WebUI are ready.

The plan file can contain sensitive values. Do not commit it. After the apply
completes, remove the local plan file:

```powershell
Remove-Item ai-lab.tfplan
```

## 4. Status Check the Open WebUI Server

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

## 5. Open Open WebUI

Choose exactly one access path based on `TF_VAR_enable_domain_access`.

### Domain-access test

When `TF_VAR_enable_domain_access` is `true`, do not start an SSM port-forwarding
session. The SSM shell in section 4 is still used for readiness and troubleshooting;
the ALB is the public entry point for browser access.

Check ALB target health from PowerShell:

```powershell
$env:alb_target_group_arn = terraform output -raw open_webui_target_group_arn
aws elbv2 describe-target-health `
  --target-group-arn $env:alb_target_group_arn
```

The EC2 target should report `healthy`. 

### Verify

- URL loads Open WebUI: https://aiwebdemo.click

- The ALB has no public port 80 (the default). Plain `http://` is not a supported
  application URL; with Cloudflare, **Always Use HTTPS** sends visitors who type
  `http://` to HTTPS at the edge before they reach AWS.

To confirm the ALB itself no longer listens on port 80, run:

```powershell
$env:alb_dns_name = terraform output -raw open_webui_alb_dns_name
curl.exe -I --max-time 10 "http://$env:alb_dns_name"
```

The request must time out or fail to connect, not return a redirect.

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

Run these from a browser:

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

### Cognito sign-in test

Skip this section unless `enable_cognito` is `true` in `terraform.tfvars`.
Terraform creates the Cognito users without passwords. After `apply` completes,
set them from the same PowerShell window, with `AWS_PROFILE` and
`AWS_DEFAULT_REGION` already set:

```powershell
.\scripts\set-cognito-passwords.ps1
```

Then, in a private window:

1. Open https://aiwebdemo.click. Access must send you straight to the Cognito
   sign-in page (no login-method choice).
2. Sign in with `demo1@example.local` and the demo password. You must reach Open
   WebUI.
3. If Open WebUI shows its own login page, choose **Continue with Cognito**. It
   must sign you in without asking for a password. The demo user has no local
   account, so Open WebUI creates it now with the `open_webui_default_user_role`
   role (default `user`, so there is no activation wait).
4. Sign out, then repeat with `admin@example.local` and the administrator
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

### Login with the admin and demo accounts

Initial login credentials are as follows unless you changed `open_webui_admin_email`:

```
admin@example.local
``` 

- Password is set in the pre-created Secrets Manager secret identified by the `open_webui_admin_password_secret_arn` output.
- Display name: `Lab Admin` unless you changed `open_webui_admin_name`.

The demo users are `demo1@example.local` through `demo10@example.local`.

They all use the value stored in the Secrets Manager secret identified by
`open_webui_demo_user_password_secret_arn`.

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

Change the temporary password immediately after confirming access.

## 6. Stop or dismantle the test system

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
are gone. When Cloudflare is enabled, also confirm the proxied apex CNAME and
the Access application are removed in the Cloudflare dashboard. Terraform does
not manage the ACM certificate, its DNS-only validation CNAME, or the Secrets
Manager secrets, so those remain. Remove `terraform.tfvars` if it contains a
real password, but keep `terraform.tfvars.example`.

## Likely failure points

- AWS credentials are missing, expired, or lack EC2, IAM, security-group, or SSM permissions.
- The selected region has no default VPC.
- The account lacks `c7i.4xlarge` quota, or the selected subnet's AZ does not offer that instance type.
- The Session Manager plugin is not installed locally.
- Bootstrap is still downloading packages, Ollama, the model, or the Open WebUI image.
- Local port 8080 is already occupied; use local port 8081 for the tunnel.
- Domain access requires an issued ACM certificate in the selected region, plus either a public Route 53 hosted zone or, with Cloudflare, an **Active** Cloudflare zone.
- A Terraform command run without the wrapper fails with `403 Missing X-Auth-Email header` because no Cloudflare token is set.
