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
enable_domain_access   = true
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

The first value is used for `admin@example.local`; the second is the temporary
password shared by the four demo accounts. Each demo user should change it from
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

Use the wrapper for every Terraform command that initializes or applies the
Cloudflare provider:

The complete command sequence is in **2. Validate and preview** and **3.
Deploy** below. Do not run a separate unsaved `plan` or `apply` here.

The wrapper also supports teardown with
`Invoke-TerraformWithCloudflareToken @("destroy")`.

Confirm the certificate is `ISSUED`, belongs to the same region as
`TF_VAR_aws_region`, and covers the exact `domain_name` before applying.
The current demo values are in `terraform.tfvars`; replace them there when
using another account, domain, zone, certificate, or Cloudflare account.

## 2. Validate and preview

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @("init")
Invoke-TerraformWithCloudflareToken -Arguments @("fmt", "-check", "-recursive")
Invoke-TerraformWithCloudflareToken -Arguments @("validate")
Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-out=ai-lab.tfplan")
```

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

## 4. Set the unique SSM target

After apply, capture the instance ID in an environment variable:

```powershell
$env:instance_id = terraform output -raw instance_id
```

Use that unique target for the SSM session:

```powershell
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

The EC2 target should report `healthy`. Then verify both:

- `http://aiwebdemo.click` redirects to HTTPS.
- `https://aiwebdemo.click` loads Open WebUI and accepts the admin and four demo accounts.

### Cloudflare Access test

When `TF_VAR_enable_cloudflare_access` is `true`, the Access check comes before
the Open WebUI login. Run these from a browser:

1. In a private window, open `https://aiwebdemo.click`. You must land on the
   Cloudflare Access sign-in page, not the Open WebUI login page.
2. Enter an email that is **not** in `cloudflare_access_allowed_emails`. Access
   must not accept it (no code arrives, or access is denied).
3. Enter an approved email, then the one-time PIN Cloudflare emails to it. You
   must reach the Open WebUI login page.
4. Sign in to Open WebUI, send a chat message, and confirm the reply streams in
   (this exercises API calls and websockets through Cloudflare).
5. Confirm the apex record is proxied: `nslookup aiwebdemo.click` must return
   Cloudflare addresses, not the ALB address.
6. In the Cloudflare dashboard, confirm SSL/TLS is **Full (strict)** and that
   Security > WAF shows the Free Managed Ruleset enabled.

`curl.exe -I https://aiwebdemo.click` should return a `302` to
`cloudflareaccess.com` rather than an Open WebUI page.

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

Open:

```text
http://localhost:8080
```

If local port 8080 is already in use, keep the remote port at 8080 and use a
different local port:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --document-name AWS-StartPortForwardingSession `
  --parameters "portNumber=8080,localPortNumber=8081"
```

Then open `http://localhost:8081`.

Initial login credentials:

- Email: `admin@example.local` unless you changed `open_webui_admin_email`.
- Password: the current value in the pre-created Secrets Manager secret identified by the `open_webui_admin_password_secret_arn` output.
- Display name: `Lab Admin` unless you changed `open_webui_admin_name`.

The bootstrap also creates these four local demo accounts:

- `demo1@example.local`
- `demo2@example.local`
- `demo3@example.local`
- `demo4@example.local`

They all start with the value stored in the Secrets Manager secret identified by
`open_webui_demo_user_password_secret_arn`.
Have each user sign in, open Profile, and change that temporary password before
using the account. These are local Open WebUI accounts; Cognito/OIDC is not
required for this demonstrable use case.

Change the temporary password immediately after confirming access.

## 6. Stop or dismantle the test system

If you may test again later, stop the instance to avoid ongoing compute charges:

```powershell
aws ec2 stop-instances `
  --instance-ids $env:instance_id
```

For permanent cleanup, review the destroy plan and then remove all Terraform-managed resources:

```powershell
terraform plan -destroy

terraform destroy
```

After teardown, verify that the instance, ALB, target group, security groups,
and Route 53 alias are gone. Remove `terraform.tfvars` if it contains a real
password, but keep `terraform.tfvars.example`.

## Likely failure points

- AWS credentials are missing, expired, or lack EC2, IAM, security-group, or SSM permissions.
- The selected region has no default VPC.
- The account lacks `c7i.4xlarge` quota, or the selected subnet's AZ does not offer that instance type.
- The Session Manager plugin is not installed locally.
- Bootstrap is still downloading packages, Ollama, the model, or the Open WebUI image.
- Local port 8080 is already occupied; use local port 8081 for the tunnel.
- Domain access requires a public Route 53 hosted zone and an issued ACM certificate in the selected region.
