# Terraform Smoke Test Plan

This plan deploys the AI Cloud Lab, verifies bootstrap and the selected access path, then safely dismantles the test system.

## 0. Open the repository directory

Run Terraform from the directory containing `main.tf`, `variables.tf`, and the other configuration files:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
Get-ChildItem *.tf
```

If `Get-ChildItem *.tf` returns no files, stop and change to the correct repository directory before continuing.

## 1. Set the test variables

Set the AWS profile and region once at the beginning. AWS CLI commands will use
`AWS_PROFILE` and `AWS_DEFAULT_REGION`, while Terraform receives the same values
through `TF_VAR_aws_profile` and `TF_VAR_aws_region`.

```powershell
$env:AWS_PROFILE = "ai-cloud-lab"
$env:AWS_DEFAULT_REGION = "us-east-1"
$env:TF_VAR_aws_profile = $env:AWS_PROFILE
$env:TF_VAR_aws_region = $env:AWS_DEFAULT_REGION
$env:TF_VAR_open_webui_admin_password_secret_arn = "arn:aws:secretsmanager:us-east-1:394566733278:secret:openwebui-admin-pass-DuXz9K"
$env:TF_VAR_open_webui_demo_user_password = "YourTemporaryDemoPasswordHere"
```

The Secrets Manager secret must already exist and contain the desired admin
password. Terraform only grants the instance role read access and retrieves the
password during bootstrap. Terraform does not create, update, or destroy this
secret. To rotate an existing lab, change the password in Open WebUI first,
then update the matching value in the AWS console.

For the domain-access variant, set these additional values before planning:

```powershell
$env:TF_VAR_enable_domain_access = "true"
$env:TF_VAR_domain_name = "aiwebdemo.click"
$env:TF_VAR_route53_zone_name = "aiwebdemo.click"
```

Confirm the certificate is `ISSUED`, belongs to the same region as
`TF_VAR_aws_region`, and covers the exact `domain_name` before applying.
The project already supplies the issued `aiwebdemo.click` certificate ARN.
Set `TF_VAR_acm_certificate_arn` only if you need to override that default.

## 2. Validate and preview

```powershell
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan
```

Optional: save a plan when you want Terraform to apply exactly the actions you reviewed:

```powershell
terraform plan -out ai-lab.tfplan

terraform apply ai-lab.tfplan
```

The plan file can contain sensitive values. Do not commit it; remove it after use:

```powershell
Remove-Item ai-lab.tfplan
```

## 3. Deploy

```powershell
terraform apply
```

Apply completes when EC2 is running, not necessarily when Ollama, the model, and Open WebUI are ready.

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

They all start with the value assigned to `$env:TF_VAR_open_webui_demo_user_password`.
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
