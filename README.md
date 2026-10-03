# AI Cloud Lab

Terraform-managed AWS lab for running a private local-model chatbot on EC2.

The active lab provisions:

- One Ubuntu EC2 instance in the selected region's default VPC
- Ollama as the local model runtime
- A configurable bootstrap model such as `llama3.2:3b`
- Open WebUI as the private browser-based chat interface
- Docker for running Open WebUI with a persistent `open-webui` volume
- AWS Systems Manager Session Manager for shell access and port forwarding
- An IAM instance profile with `AmazonSSMManagedInstanceCore`
- A security group with no public inbound access to Open WebUI or Ollama (the instance may still have a public IP for outbound bootstrap traffic)
- Optional public HTTPS access through an Application Load Balancer and ACM, fronted by Cloudflare (DNS, proxy, and Cloudflare Access), or by Route 53 when Cloudflare is disabled

PyGPT was removed because this lab is intended to be administered and used through private browser access on a headless EC2 instance. A desktop GUI, XFCE, XRDP, and PyGPT add extra bootstrap time and attack surface without helping the private web chat workflow.

## Architecture

```text
Your workstation
      |
      | SSM port forwarding, or optional SSH tunnel
      v
http://localhost:8080
      |
      v
EC2 Ubuntu instance
      |
      v
Open WebUI Docker container
      |
      | http://127.0.0.1:11434
      v
Ollama systemd service
      |
      v
Local model
```

Ollama listens only on `127.0.0.1:11434`. Open WebUI runs on the instance at `localhost:8080`. The Terraform security group does not expose ports `8080` or `11434` to the public internet. SSH is disabled by default; if enabled, TCP/22 is limited to `var.allowed_ssh_cidr`.

## Optional Domain Access And Cloudflare

Cloudflare is a major part of the public-access architecture. With
`enable_domain_access = true` and `enable_cloudflare_access = true`, traffic
flows like this:

```text
User
  |  HTTPS, Cloudflare edge certificate
  v
Cloudflare DNS, proxy, and Cloudflare Access (approved emails only)
  |  HTTPS, ACM certificate on the ALB (Full (strict))
  v
AWS Application Load Balancer
  |  HTTP 8080, security-group path from the ALB only
  v
EC2 instance running Open WebUI and Ollama
```

Putting Cloudflare in front adds:

- **Authentication before AWS:** only the emails in
  `cloudflare_access_allowed_emails` get through Cloudflare Access. Everyone
  else is stopped at Cloudflare's edge.
- **A hidden origin:** the public hostname resolves to Cloudflare, not to the ALB.
  The ALB is still reachable directly unless you set
  `enable_origin_lockdown = true`, which limits it to Cloudflare's IP ranges (see
  "Origin lockdown" in `cloudflare-and-domain-requirements.md`).
- **DDoS and bot mitigation, and free edge TLS**, plus optional WAF, rate
  limiting, and analytics.

Terraform then creates an internet-facing ALB that listens only on HTTPS port 443
(no public port 80 unless `enable_alb_http_redirect = true`), an EC2 rule allowing the app port only from the ALB,
a proxied Cloudflare CNAME for `domain_name` pointing at the ALB, and a
Cloudflare Access application with an email allow policy.

Set `enable_domain_access = true` only after the ACM certificate is issued in
the same AWS region. For the Cloudflare path, the domain must also be delegated
to Cloudflare at the registrar and the Cloudflare zone must be **Active**.
Without Cloudflare (`enable_cloudflare_access = false`), Terraform creates a
Route 53 alias record instead and needs a public Route 53 hosted zone.

The project defaults to the issued `aiwebdemo.click` certificate ARN in
`us-east-1`. Set `acm_certificate_arn` only when intentionally changing the
certificate. When cloning for another account, replace it or set it to `null`.

When Cloudflare is enabled, `terraform plan`, `apply`, and `destroy` need the
Cloudflare API token in `CLOUDFLARE_API_TOKEN`. Run them through the wrapper in
[terraform-smoke-test-plan.md](terraform-smoke-test-plan.md) rather than plain
`terraform`, or they fail with `403 Missing X-Auth-Email header`.

Set `enable_cognito = true` to use an Amazon Cognito user pool as the sign-in for
both Cloudflare Access and Open WebUI; see the Cognito section of
[cloudflare-and-domain-requirements.md](cloudflare-and-domain-requirements.md).
After `apply`, run `scripts/set-cognito-passwords.ps1` once to set the user
passwords from Secrets Manager.

A few Cloudflare settings are not managed by Terraform. The most important is
setting SSL/TLS to **Full (strict)**; **Flexible** makes Cloudflare connect over
HTTP port 80, which the ALB does not open, so visitors get 522 errors. See
[cloudflare-and-domain-requirements.md](cloudflare-and-domain-requirements.md)
for the full prerequisites, security settings, and trade-offs.

The EC2 instance does not receive a public application ingress rule. When domain
access is enabled, use `https://<domain_name>` instead of SSM port forwarding.

## Cost Guardrail: Auto-Stop

The lab stops itself when nobody is using it, and it never stops an instance
that has active users. It is on by default. Four settings in `terraform.tfvars`
control it:

| Setting | Meaning |
|---|---|
| `enable_auto_stop` | `true` (default) or `false` to leave the instance running |
| `auto_stop_idle_minutes` | Minutes of no activity before it stops itself (default 60; the example uses 90) |
| `auto_stop_max_uptime_hours` | Hours of uptime before an email alert, repeating hourly (default 8) |
| `auto_stop_alert_email` | Where alerts go; AWS emails a confirmation link to click once |

Changing these updates in place (an SSM parameter and a Lambda setting), so it
does not replace the instance.

**Layer 1: idle monitor on the instance.** A systemd timer runs
`scripts/ai-lab-idle-check.sh` every minute. The lab counts as active when
Open WebUI reports a user active in the last 3 minutes (its own definition, read
from its database) or a reply is being generated (an open connection to Ollama).
If activity cannot be determined, it counts as active. After
`auto_stop_idle_minutes` with no activity, the instance powers off and EC2 stops
it. The idle clock starts when first-boot setup finishes and after every restart,
so a restarted instance gets a full idle window. It does nothing while setup is
still running.

**Layer 2: independent watchdog.** An EventBridge rule runs the Lambda function
`lambda/auto_stop_watchdog.py` every 5 minutes. It stops the instance only if the
idle monitor already reports it idle but it is still running (the shutdown
failed), or if the monitor has gone silent and the load balancer shows no
requests for the whole idle window. Otherwise it emails an alert and leaves the
instance running: when the monitor is not reporting, and when the instance has run
longer than `auto_stop_max_uptime_hours`.

What to know:

- **It is a guardrail, not a hard cap.** An instance with active users keeps
  running past the maximum uptime; you get an email each hour and it stops once
  everyone is idle.
- The idle monitor publishes `ActiveUsers`, `IdleMinutes`, `UptimeMinutes` and
  `Heartbeat` to CloudWatch (namespace `AILab`). To see what it sees on the
  instance, run `sudo AI_LAB_DRY_RUN=1 /usr/local/sbin/ai-lab-idle-check`.
- An open browser tab with no activity stops counting after about 3 minutes, so
  it does not keep the lab up.
- Stopping saves compute charges only. EBS storage, the ALB, and any NAT or
  endpoint charges continue until you destroy the lab.
- The Lambda function is packaged with the `hashicorp/archive` provider, so run
  `terraform init -upgrade` once after pulling this change.
- The identity that runs Terraform needs permission to create the Lambda
  function, its IAM roles, the SNS topic, the EventBridge rule, and the SSM
  parameter.
- `terraform output auto_stop_watchdog` shows the Lambda function name. Everything
  is removed on `terraform destroy`.
- Python tests for the watchdog logic: `python -m unittest discover -s tests`
  (needs `boto3`).

## Prerequisites

- Terraform installed
- AWS CLI installed and configured
- AWS Session Manager plugin installed
- An AWS profile with permission to create EC2, IAM, security group, and EBS resources
- A default VPC in the selected AWS region, or a Terraform change to use a custom VPC/subnet
- For public access through Cloudflare: a Cloudflare account with the domain added and **Active**, the domain's nameservers set to Cloudflare at the registrar, an issued ACM certificate, and a scoped Cloudflare API token stored in Secrets Manager (see `cloudflare-and-domain-requirements.md`)

## Secure Admin Password

Open WebUI creates the first local admin account during container startup using:

- `open_webui_admin_email`
- `open_webui_admin_name`
- the password in `open_webui_admin_password_secret_arn`

Create the Secrets Manager secret before running Terraform and store the desired
admin password in it. The secret may be plain text or a one-key key/value secret;
bootstrap extracts the single string value from either format. Terraform only
validates and references the existing secret, grants the EC2 instance role
permission to read it, and retrieves the value at runtime. Terraform does not
create, update, or destroy this secret, and the password is not embedded in EC2
user-data.

To rotate an existing lab, change the password in Open WebUI first, then update
the matching value in the AWS console. Changing the secret alone does not change
the already-initialized Open WebUI account.

The demo users use:

- the password in `open_webui_demo_user_password_secret_arn`
- `open_webui_demo_users` (ten demo users by default; any number from 1 to 25)

The demo-user password must be stored in a second pre-created Secrets Manager
secret and is retrieved through the EC2 role during bootstrap or by
`scripts/set-cognito-passwords.ps1`. Neither password is kept in Terraform
variables, user-data, or the repository.

### Where each account lives

| Account | `enable_cognito = true` (default) | `enable_cognito = false` |
|---|---|---|
| Administrator (`open_webui_admin_email`) | Local Open WebUI account **and** a Cognito user with the same email | Local Open WebUI account only |
| Demo users (`open_webui_demo_users`) | Cognito users only | Local Open WebUI accounts |
| Extra users (`cognito_extra_users`) | Cognito users only | Not used |

With Cognito on, Open WebUI creates an account for each Cognito-only user the first time they choose **Continue with Cognito**. New accounts get the role in `open_webui_default_user_role` (default `user`); set it to `pending` if an administrator should approve each person in **Admin Panel > Users**. When a Cognito sign-in uses the same email as an existing local account (the administrator), Open WebUI signs you in to that account, so the administrator keeps the admin role. `scripts/set-cognito-passwords.ps1` gives the administrator's Cognito user the admin password secret and everyone else the demo-user password secret. Local and Cognito passwords are separate after creation, so changing one does not change the other.

### Local accounts and turning them off

Local accounts are Open WebUI's own email and password logins. They are unrelated to AWS Systems Manager, which uses IAM and never these accounts. By default the administrator's local login stays on as a break-glass sign-in. Sign-in still passes through Cloudflare Access first when it is enabled.

To allow Cognito sign-in only, set this in `terraform.tfvars`:

```hcl
enable_cognito                = true
open_webui_enable_local_login = false
```

That setting hides the password form and turns off password authentication in Open WebUI. The administrator can then sign in only through Cognito, which still works because the administrator is also a Cognito user. Terraform refuses the combination with `enable_cognito = false`. Changing it replaces the EC2 instance, so Open WebUI's saved data resets. In code, the switch is `open_webui_enable_local_login` in `variables.tf`, and it sets `ENABLE_LOGIN_FORM` and `ENABLE_PASSWORD_AUTH` in `cloud-init.sh.tpl`.

PowerShell:

```powershell
$env:TF_VAR_open_webui_demo_user_password_secret_arn = "<demo-password-secret-arn>"
```

Linux/macOS:

```bash
export TF_VAR_open_webui_demo_user_password_secret_arn="<demo-password-secret-arn>"
```

You may also use a local `terraform.tfvars` file for secrets. It is ignored by `.gitignore`; do not commit it.

## Quick Start

Copy the example variables file, set the pre-created secret ARN and demo
password locally, and deploy:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
Copy-Item terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars and set your region, model, and other values.
$env:TF_VAR_open_webui_admin_password_secret_arn = "<secret-arn>"
$env:TF_VAR_open_webui_demo_user_password_secret_arn = "<demo-password-secret-arn>"
terraform init
terraform fmt -recursive
terraform validate
terraform apply
```

`aws_profile = null` is the default, so Terraform uses environment credentials, SSO, or an instance/CI role. Set `aws_profile` in `terraform.tfvars` only when you intentionally want a named local profile.

## Deploy

PowerShell example using an explicit profile:

```powershell
terraform init
terraform fmt -recursive
terraform validate
terraform plan `
  -var="aws_profile=<your-profile>" `
  -var="aws_region=us-east-1" `
  -var="instance_type=c7i.4xlarge" `
  -var="root_volume_size=80" `
  -var="ollama_model=llama3.2:3b"

terraform apply `
  -var="aws_profile=<your-profile>" `
  -var="aws_region=us-east-1" `
  -var="instance_type=c7i.4xlarge" `
  -var="root_volume_size=80" `
  -var="ollama_model=llama3.2:3b"
```

Terraform uses `user_data_replace_on_change = true`, so bootstrap template changes replace the EC2 instance on the next apply.

## Wait For Bootstrap

`terraform apply` completes when EC2 reports the instance running; package, model, and container setup continues afterward and can take several minutes. From an SSM shell, run:

```bash
if test -f /var/lib/ai-lab/ready; then echo READY; elif test -f /var/lib/ai-lab/failed; then echo FAILED; else echo NOT_READY; fi
tail -n 100 /var/log/ai-lab-bootstrap.log
ai-lab-status
```

The bootstrap writes `ready` only after Ollama and Open WebUI respond successfully, and writes `failed` when a command aborts.

## Connect With SSM Port Forwarding

This is the preferred private browser access path because it requires no inbound rules.

PowerShell:

```powershell
aws ssm start-session `
  --target <instance-id> `
  --document-name AWS-StartPortForwardingSession `
  --parameters portNumber="8080",localPortNumber="8080" `
  --profile <your-profile> `
  --region us-east-1
```

Linux/macOS:

```bash
aws ssm start-session \
  --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber="8080",localPortNumber="8080" \
  --profile <your-profile> \
  --region us-east-1
```

Then open:

```text
http://localhost:8080
```

You can also use the generated output:

```powershell
terraform output -raw ssm_open_webui_port_forward_command
```

## Optional SSH Tunnel

SSH is disabled by default. To enable it, pass an existing EC2 key pair name and a narrow source CIDR:

```powershell
terraform apply `
  -var="aws_profile=<your-profile>" `
  -var="enable_ssh=true" `
  -var="ssh_key_name=<existing-key-pair-name>" `
  -var="allowed_ssh_cidr=<your-ip>/32"
```

Tunnel command:

```bash
ssh -i <path-to-key.pem> -L 8080:localhost:8080 ubuntu@<public-ip>
```

Then open `http://localhost:8080`.

## Session Manager Shell

```powershell
aws ssm start-session --target <instance-id> --region us-east-1 --profile <your-profile>
```

The equivalent Terraform output is:

```powershell
terraform output -raw ssm_shell_command
```

## Pull Another Model With SSM Run Command

PowerShell multiline:

```powershell
aws ssm send-command `
  --document-name "AWS-RunShellScript" `
  --targets "Key=tag:Name,Values=aiwebdemo" `
  --parameters commands='["ollama pull qwen2.5:7b", "ollama list"]' `
  --comment "Pull selected Ollama model" `
  --profile <your-profile> `
  --region us-east-1
```

PowerShell one-liner:

```powershell
aws ssm send-command --document-name "AWS-RunShellScript" --targets "Key=tag:Name,Values=aiwebdemo" --parameters commands='["ollama pull qwen2.5:7b", "ollama list"]' --comment "Pull selected Ollama model" --profile <your-profile> --region us-east-1
```

Linux/macOS:

```bash
aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=aiwebdemo" \
  --parameters commands='["ollama pull qwen2.5:7b", "ollama list"]' \
  --comment "Pull selected Ollama model" \
  --profile <your-profile> \
  --region us-east-1
```

Use `terraform output -raw instance_id` or the `Name` tag value from `var.project_name` if you customize the project name.

## Verify Services

From an SSM shell:

```bash
systemctl status ollama
sudo docker ps
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:8080
ai-lab-status
```

## Troubleshooting Open WebUI And Ollama

If Open WebUI does not show Ollama models:

```bash
systemctl status ollama
curl http://127.0.0.1:11434/api/tags
sudo docker logs --tail 200 open-webui
sudo docker inspect open-webui --format '{{range .Config.Env}}{{println .}}{{end}}' | grep OLLAMA_BASE_URL
ollama list
```

This lab runs Open WebUI with host networking so the Docker container can reach the localhost-bound Ollama service at `http://127.0.0.1:11434`. Do not change Ollama to `0.0.0.0` unless you also understand the exposure risk and add compensating controls.

## Stop Or Destroy

Stop the instance when not in use:

```powershell
aws ec2 stop-instances --instance-ids <instance-id> --region us-east-1 --profile <your-profile>
```

Destroy all Terraform-managed lab resources:

```powershell
terraform destroy `
  -var="aws_profile=<your-profile>" `
  -var="aws_region=us-east-1" `
  -var="instance_type=c7i.4xlarge" `
  -var="root_volume_size=80" `
  -var="ollama_model=llama3.2:3b"
```

Do not commit `.terraform/`, `terraform.tfstate`, `terraform.tfvars`, or generated private keys.
