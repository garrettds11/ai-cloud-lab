# Logging: VictoriaLogs log store

This folder adds a log store to the lab so MCP tools in Open WebUI have real logs to query. The current track is VictoriaLogs. Other logging tools are kept in [issue #92](https://github.com/garrettds11/ai-cloud-lab/issues/92) and come back one at a time as their own tracks; Splunk is planned as the final one.

Each folder under this directory is a separate Terraform root and state. A track runs against the already deployed repository-root lab host. Its apply creates a backend instance, a private raw-event S3 archive, an SSM Distributor package, and an SSM State Manager association that installs Vector on the root Ubuntu host. The track receives the host ID, security group ID and IAM role name from root outputs; it does not read or own root state.

Vector collects the host journal and the Open WebUI container logs. It writes every event to the S3 archive and forwards the same events through the backend's private HTTPS intake to VictoriaLogs. The VictoriaLogs API and its built-in web UI (vmui) are reachable only through an SSM port-forwarding session.

## One-time preparation

1. Deploy the repository-root lab and make sure its Ubuntu host is online in Systems Manager. Its instance profile already includes `AmazonSSMManagedInstanceCore`.
2. Open a PowerShell window prepared as in Step 1 of [the Terraform smoke test plan](../docs/runbook/terraform-smoke-test-plan.md): `AWS_PROFILE` and `AWS_DEFAULT_REGION` set to the lab account, with the account check passing. The logging track uses only the AWS provider, so it runs plain `terraform`; the Cloudflare token wrapper is needed only for the repository-root lab.
3. Read the root outputs and put their values in the track's git-ignored `terraform.tfvars`:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
terraform output -raw instance_id
terraform output -raw instance_security_group_id
terraform output -raw instance_iam_role_name
terraform output -raw open_webui_container_name
```

Also set `aws_region` to the root deployment region, and `aws_profile` to the named AWS CLI profile of the root deployment when you use one. Do not commit a populated `terraform.tfvars`; it holds live resource IDs.

## Apply

```powershell
Set-Location C:\GitHub\ai-cloud-lab\logging\victorialogs
Copy-Item terraform.tfvars.example terraform.tfvars
# Fill in the root output values and region in terraform.tfvars.
terraform init
terraform fmt -check
terraform validate
terraform plan -out=track.tfplan
terraform show -no-color track.tfplan | Out-File track-plan.txt
# Stop here and review track-plan.txt. Continue only after review.
terraform apply track.tfplan
```

Wait for the `AWS-ConfigureAWSPackage` association to report success in Systems Manager State Manager.

## Open vmui

Start the tunnel in its own PowerShell window and leave it running:

```powershell
Invoke-Expression (terraform output -raw ui_tunnel_command)
```

Then browse to `http://localhost:18080/select/vmui/`. VictoriaLogs has no sign-in; only the tunnel reaches it. The same address serves the query API (`http://localhost:18080/select/logsql/query`) that an MCP tool uses.

Follow [END-TO-END-TEST.md](END-TO-END-TEST.md) to send test events and check vmui and the S3 archive.

## Destroy

First uninstall Vector from the root host with the command from `terraform output -json uninstall_agent_commands`, and wait for it to finish. Then review and apply a destroy plan:

```powershell
terraform plan -destroy -out=destroy.tfplan
terraform show -no-color destroy.tfplan
terraform apply destroy.tfplan
```

Destroy removes the archive bucket and its raw objects (`force_destroy` is on), the backend instance and volume, the SSM package document, the package bucket, the association and the archive permission on the root host role. Save any results you want first. The root lab host stays deployed.

## Cost and licence

The track adds one `t3.large` backend with an encrypted 80 GiB gp3 volume and a public IPv4 address, a small package bucket, and an S3 archive kept for `archive_retention_days` (30 by default). Estimate a run as `instance hourly rate × hours + gp3 GiB-month + public IPv4 hours + S3 GB-month + S3 requests`, using the region's [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/), [S3](https://aws.amazon.com/s3/pricing/) and [public IPv4](https://aws.amazon.com/vpc/pricing/) rates. A stopped instance still pays for its volume and public IPv4 address.

VictoriaLogs is Apache 2.0; see the [VictoriaLogs documentation](https://docs.victoriametrics.com/victorialogs/). This is a single-node lab stack, not production sizing.

## State and safety

Never run Terraform from `logging/` itself; run it in the track folder. Its local `.tfstate` is separate from the root state. The plan is the review gate: check the instance type, ingress rule, S3 lifecycle, IAM actions, package contents, association target and destroy behaviour before applying. Nothing here applies automatically.
