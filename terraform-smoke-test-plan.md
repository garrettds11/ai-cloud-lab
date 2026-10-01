# Terraform Smoke Test Plan

This plan deploys the AI Cloud Lab, verifies bootstrap and private access, then safely dismantles the test system.

## 0. Open the repository directory

Run Terraform from the directory containing `main.tf`, `variables.tf`, and the other configuration files:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
Get-ChildItem *.tf
```

If `Get-ChildItem *.tf` returns no files, stop and change to the correct repository directory before continuing.

## 1. Set the test variables

Set the AWS profile, region, and temporary Open WebUI password before running Terraform. The password is required and has no default.

```powershell
$env:TF_VAR_aws_profile = "ai-cloud-lab"
$env:TF_VAR_aws_region = "us-east-1"
$env:TF_VAR_open_webui_admin_password = "YourTemporaryStrongPasswordHere"
```

You can also pass non-secret variables directly:

```powershell
terraform plan `
  -var="aws_profile=ai-cloud-lab" `
  -var="aws_region=us-east-1"
```

## 2. Validate and preview

```powershell
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan
```

For an exact plan/apply pair:

```powershell
terraform plan `
  -out ai-lab.tfplan `
  -var="aws_profile=ai-cloud-lab" `
  -var="aws_region=us-east-1"

terraform apply ai-lab.tfplan
```

The plan file can contain sensitive values. Do not commit it; remove it after use:

```powershell
Remove-Item ai-lab.tfplan
```

## 3. Deploy

```powershell
terraform apply `
  -var="aws_profile=ai-cloud-lab" `
  -var="aws_region=us-east-1"
```

Apply completes when EC2 is running, not necessarily when Ollama, the model, and Open WebUI are ready.

## 4. Set the unique SSM target

After apply, capture the instance ID in an environment variable:

```powershell
$env:instance_id = terraform output -raw instance_id
```

Use that unique target for the SSM session:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --region us-east-1 `
  --profile ai-cloud-lab
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

Wait for `READY` before opening the browser tunnel. If the result is `FAILED`, inspect the bootstrap log before retrying.

The SSM shell is a Linux shell. Run Linux commands there; run PowerShell commands such as `curl.exe` from a separate Windows PowerShell window.

## 5. Open the private browser tunnel

Run this in a second PowerShell terminal:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --document-name AWS-StartPortForwardingSession `
  --parameters portNumber="8080",localPortNumber="8080" `
  --region us-east-1 `
  --profile ai-cloud-lab
```

Open:

```text
http://localhost:8080
```

Initial login credentials:

- Email: `admin@example.local` unless you changed `open_webui_admin_email`.
- Password: the value assigned to `$env:TF_VAR_open_webui_admin_password` before apply.
- Display name: `Lab Admin` unless you changed `open_webui_admin_name`.

Change the temporary password immediately after confirming access.

If local port 8080 is already in use, keep the remote port at 8080 and use a different local port:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --document-name AWS-StartPortForwardingSession `
  --parameters "portNumber=8080,localPortNumber=8081" `
  --region us-east-1 `
  --profile ai-cloud-lab
```

Then open `http://localhost:8081`.

## 6. Stop or dismantle the test system

If you may test again later, stop the instance to avoid ongoing compute charges:

```powershell
aws ec2 stop-instances `
  --instance-ids $env:instance_id `
  --region us-east-1 `
  --profile ai-cloud-lab
```

For permanent cleanup, review the destroy plan and then remove all Terraform-managed resources:

```powershell
terraform plan -destroy `
  -var="aws_profile=ai-cloud-lab" `
  -var="aws_region=us-east-1"

terraform destroy `
  -var="aws_profile=ai-cloud-lab" `
  -var="aws_region=us-east-1"
```

After teardown, verify that the instance and security group are gone. Remove `terraform.tfvars` if it contains a real password, but keep `terraform.tfvars.example`.

## Likely failure points

- AWS credentials are missing, expired, or lack EC2, IAM, security-group, or SSM permissions.
- The selected region has no default VPC.
- The account lacks `c7i.4xlarge` quota, or the selected subnet's AZ does not offer that instance type.
- The Session Manager plugin is not installed locally.
- Bootstrap is still downloading packages, Ollama, the model, or the Open WebUI image.
- Local port 8080 is already occupied; use local port 8081 for the tunnel.
