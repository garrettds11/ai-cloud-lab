# AI Cloud Lab

Terraform-managed AWS lab for running a private local-model chatbot on EC2.

The active lab provisions:

- One Ubuntu EC2 instance in the selected region's default VPC
- Ollama as the local model runtime
- A configurable bootstrap model such as `llama3.1:8b`
- Open WebUI as the private browser-based chat interface
- Docker for running Open WebUI with a persistent `open-webui` volume
- AWS Systems Manager Session Manager for shell access and port forwarding
- An IAM instance profile with `AmazonSSMManagedInstanceCore`
- A security group with no public inbound access to Open WebUI or Ollama

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

## Prerequisites

- Terraform installed
- AWS CLI installed and configured
- AWS Session Manager plugin installed
- An AWS profile with permission to create EC2, IAM, security group, and EBS resources
- A default VPC in the selected AWS region, or a Terraform change to use a custom VPC/subnet

## Secure Admin Password

Open WebUI creates the first local admin account during container startup using:

- `open_webui_admin_email`
- `open_webui_admin_name`
- `open_webui_admin_password`

The password variable is sensitive and has a temporary test-lab default:

```text
ChangeMeBeforeUse123!
```

Use it only long enough to verify the private tunnel and first login, then update the Open WebUI admin password before using the lab. For a less disposable run, override it before apply. Do not commit a real password in committed files.

PowerShell:

```powershell
$env:TF_VAR_open_webui_admin_password = "<strong-local-password>"
```

Linux/macOS:

```bash
export TF_VAR_open_webui_admin_password="<strong-local-password>"
```

You may also use a local `terraform.tfvars` file for secrets. It is ignored by `.gitignore`; do not commit it.

## Deploy

PowerShell example using profile `garrett_gspear`:

```powershell
terraform init
terraform fmt -recursive
terraform validate
terraform plan `
  -var="aws_profile=garrett_gspear" `
  -var="aws_region=us-east-1" `
  -var="instance_type=t3.xlarge" `
  -var="root_volume_size=80" `
  -var="ollama_model=llama3.1:8b"

terraform apply `
  -var="aws_profile=garrett_gspear" `
  -var="aws_region=us-east-1" `
  -var="instance_type=t3.xlarge" `
  -var="root_volume_size=80" `
  -var="ollama_model=llama3.1:8b"
```

Terraform uses `user_data_replace_on_change = true`, so bootstrap template changes replace the EC2 instance on the next apply.

## Connect With SSM Port Forwarding

This is the preferred private browser access path because it requires no inbound rules.

PowerShell:

```powershell
aws ssm start-session `
  --target <instance-id> `
  --document-name AWS-StartPortForwardingSession `
  --parameters portNumber="8080",localPortNumber="8080" `
  --profile garrett_gspear `
  --region us-east-1
```

Linux/macOS:

```bash
aws ssm start-session \
  --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber="8080",localPortNumber="8080" \
  --profile garrett_gspear \
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
  -var="aws_profile=garrett_gspear" `
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
aws ssm start-session --target <instance-id> --region us-east-1 --profile garrett_gspear
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
  --targets "Key=tag:Name,Values=ollama-open-webui-lab" `
  --parameters commands='["ollama pull qwen2.5:7b", "ollama list"]' `
  --comment "Pull selected Ollama model" `
  --profile garrett_gspear `
  --region us-east-1
```

PowerShell one-liner:

```powershell
aws ssm send-command --document-name "AWS-RunShellScript" --targets "Key=tag:Name,Values=ollama-open-webui-lab" --parameters commands='["ollama pull qwen2.5:7b", "ollama list"]' --comment "Pull selected Ollama model" --profile garrett_gspear --region us-east-1
```

Linux/macOS:

```bash
aws ssm send-command \
  --document-name "AWS-RunShellScript" \
  --targets "Key=tag:Name,Values=ollama-open-webui-lab" \
  --parameters commands='["ollama pull qwen2.5:7b", "ollama list"]' \
  --comment "Pull selected Ollama model" \
  --profile garrett_gspear \
  --region us-east-1
```

Use `terraform output -raw instance_id` or the `Name` tag value from `var.project_name` if you customize the project name.

## Verify Services

From an SSM shell:

```bash
systemctl status ollama
docker ps
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:8080
ai-lab-status
```

## Troubleshooting Open WebUI And Ollama

If Open WebUI does not show Ollama models:

```bash
systemctl status ollama
curl http://127.0.0.1:11434/api/tags
docker logs --tail 200 open-webui
docker inspect open-webui --format '{{range .Config.Env}}{{println .}}{{end}}' | grep OLLAMA_BASE_URL
ollama list
```

This lab runs Open WebUI with host networking so the Docker container can reach the localhost-bound Ollama service at `http://127.0.0.1:11434`. Do not change Ollama to `0.0.0.0` unless you also understand the exposure risk and add compensating controls.

## Stop Or Destroy

Stop the instance when not in use:

```powershell
aws ec2 stop-instances --instance-ids <instance-id> --region us-east-1 --profile garrett_gspear
```

Destroy all Terraform-managed lab resources:

```powershell
terraform destroy `
  -var="aws_profile=garrett_gspear" `
  -var="aws_region=us-east-1" `
  -var="instance_type=t3.xlarge" `
  -var="root_volume_size=80" `
  -var="ollama_model=llama3.1:8b"
```

Do not commit `.terraform/`, `terraform.tfstate`, `terraform.tfvars`, or generated private keys.
