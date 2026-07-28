# AI Cloud Lab

Terraform-managed AWS lab for experimenting with a local chatbot stack on EC2.

The initial lab creates a single Ubuntu EC2 instance with:

- Ollama as the local model runtime
- A CPU-friendly local model such as `llama3.2:3b`
- PyGPT as the graphical chatbot interface
- XFCE and XRDP for remote desktop access
- AWS Systems Manager Session Manager for tunneled access
- A security group with no inbound rules

## Architecture

```text
Your workstation
      |
      | AWS Systems Manager port-forwarding session
      v
EC2 Ubuntu instance
      |
      v
XFCE desktop + PyGPT
      |
      | http://127.0.0.1:11434
      v
Ollama
      |
      v
Local model
```

Ollama is intentionally bound to `127.0.0.1:11434`. The EC2 security group does not expose SSH, XRDP, or the Ollama API to the public internet.

## Files

```text
.
├── main.tf
├── variables.tf
├── outputs.tf
├── cloud-init.sh.tpl
├── terraform.tfvars.example
└── README.md
```

## Prerequisites

On your workstation:

- Terraform installed
- AWS CLI installed and configured
- AWS Session Manager plugin installed
- An AWS profile with permission to create EC2, IAM, security group, and EBS resources
- A default VPC in the selected AWS region, or modify the Terraform to use a custom VPC/subnet

## Configure

Copy the example variables file:

```bash
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` for your AWS profile, region, instance type, and model choice.

Example:

```hcl
aws_region  = "us-east-1"
aws_profile = "garrett_gspear"

project_name = "pygpt-ollama-lab"

instance_type    = "t3.xlarge"
root_volume_size = 80
ollama_model     = "llama3.2:3b"
```

## Deploy

```bash
terraform init
terraform plan
terraform apply
```

Get the desktop password:

```bash
terraform output -raw desktop_password
```

> The generated desktop password is stored in Terraform state. Protect your state file. Do not commit `terraform.tfstate` or `terraform.tfvars`.

## Connect with RDP over SSM

Start a port-forwarding session:

```bash
aws ssm start-session \
  --target <instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters portNumber="3389",localPortNumber="13389" \
  --region us-east-1 \
  --profile garrett_gspear
```

Then open Remote Desktop and connect to:

```text
localhost:13389
```

Credentials:

```text
Username: ubuntu
Password: <terraform output desktop_password>
```

## Test from the EC2 desktop

Open a terminal in the remote desktop and run:

```bash
ai-lab-status
ollama list
ollama run llama3.2:3b
```

Launch PyGPT from the desktop shortcut or terminal:

```bash
pygpt
```

Configure PyGPT to use Ollama and select the installed model.

## Stop or destroy

To avoid unnecessary EC2 charges, stop the instance when not in use.

To remove the lab completely:

```bash
terraform destroy
```

## Notes

This is a learning and testing environment. It is not initially designed for production use, public users, regulated data, or multi-user access.
