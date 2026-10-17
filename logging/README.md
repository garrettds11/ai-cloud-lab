# Logging bake-off: five independent runs

Each folder under this directory is a separate Terraform root and state. Run one track at a time against the already deployed repository-root lab host. A track apply creates its backend, a private raw-event S3 archive, an SSM Distributor package, and an SSM State Manager association that installs that track's collector on the root Ubuntu host. The track receives the host ID, security group ID, and IAM role name from root outputs; it does not read or own root state.

The five tracks are Wazuh, Graylog, VictoriaLogs, Quickwit, and Splunk. The same host journal and Open WebUI container logs are collected for every track. Wazuh uses the Wazuh agent for analysis and a Vector package for the separate S3 archive. The other four use a Vector package which archives to S3 and forwards the same events through the track's private HTTPS intake adapter to the selected backend. Each backend UI is only reachable through an SSM port-forwarding session.

## One-time lab preparation

1. Deploy the repository-root lab and ensure its Ubuntu host is online in Systems Manager. Its instance profile already includes `AmazonSSMManagedInstanceCore`.
2. Enable `enable_wazuh_agent_egress = true` in the repository-root Terraform variables before the Wazuh run. It allows only TCP 1514 and 1515 to the default VPC CIDR; each logging backend security group still accepts agent traffic only from the lab host security group. This is an SG-only root change, though Terraform must confirm that in the root plan before applying.
3. Open a PowerShell window prepared as in step 2 of
   [the Terraform smoke test plan](../docs/runbook/terraform-smoke-test-plan.md): `AWS_PROFILE` and
   `AWS_DEFAULT_REGION` set to the lab account, with the account check passing. The logging tracks
   use only the AWS provider, so they run plain `terraform`; the Cloudflare token wrapper is needed
   only for the repository-root lab.
4. Read the root outputs and use their values in the selected track's ignored `terraform.tfvars`:

```powershell
Set-Location C:\GitHub\ai-cloud-lab
terraform output -raw instance_id
terraform output -raw instance_security_group_id
terraform output -raw instance_iam_role_name
terraform output -raw open_webui_container_name
```

Also set `aws_region` to the root deployment region and `aws_profile` to the named AWS CLI profile used by the repository-root deployment when applicable. Do not commit populated `terraform.tfvars` files; they contain live resource identifiers.

The root Terraform state stays in the repository-root workflow. Each track has local state in its own directory by default, so its apply and destroy do not destroy the lab host.

## Five apply / test / destroy cycles

Repeat the following complete cycle for each of `wazuh`, `graylog`, `victorialogs`, `quickwit`, and `splunk`. Do not have two tracks installed on the same host at once. This keeps each end-to-end result isolated and makes the five destroys unambiguous.

```powershell
$track = 'wazuh' # change for each cycle
Set-Location "C:\GitHub\ai-cloud-lab\logging\$track"
Copy-Item terraform.tfvars.example terraform.tfvars
# Fill in the root output values and region in terraform.tfvars.
terraform init
terraform fmt -check
terraform validate
terraform plan -out=track.tfplan
terraform show -no-color track.tfplan | Out-File track-plan.txt
# Stop here and present `track-plan.txt` for review. Continue only after review.
terraform apply track.tfplan
```

Wait for the `AWS-ConfigureAWSPackage` association to report success in Systems Manager State Manager before testing. For Wazuh, also confirm the Wazuh agent is active and enrolled.

Read the UI sign-in for Graylog, VictoriaLogs (Grafana), Quickwit (Grafana) and Splunk. The backend generates a random password at boot and keeps it only in `/root/logging-bakeoff-credentials` on the backend instance, so it never enters Terraform state. Read it through SSM Run Command:

```powershell
$backend = terraform output -raw backend_instance_id
$command = aws ssm send-command --instance-ids $backend --document-name AWS-RunShellScript --parameters "commands=cat /root/logging-bakeoff-credentials" --query Command.CommandId --output text
aws ssm wait command-executed --command-id $command --instance-id $backend
aws ssm get-command-invocation --command-id $command --instance-id $backend --query StandardOutputContent --output text
```

The user name is `admin`. Run Command keeps the output in the account's command history, which the track destroy does not remove; the password stops working when the backend is destroyed. Wazuh does not use this file: its dashboard uses the default credentials of the pinned `wazuh-docker` single-node release.

Open the backend UI with the `ui_tunnel_command` output, then browse to `http://localhost:18080`. Follow [END-TO-END-TEST.md](END-TO-END-TEST.md) for the same unique host and container markers, each product's exact search and dashboard check, and S3 archive verification.

Before each destroy, uninstall the track package from the root host through Distributor. The track README gives the package name and command. Review `terraform plan -destroy -out=destroy.tfplan` and `terraform show -no-color destroy.tfplan` first; then destroy that track only:

```powershell
terraform apply destroy.tfplan
```

Destroy removes that track's archive bucket and its raw objects (`force_destroy` is enabled to support the five-cycle teardown), backend instance and volume, SSM package document, package attachment bucket, association, and archive permission on the root host role. This intentionally deletes all test archive data for that cycle. Save or export any results you want before this step. The root lab host remains deployed. Confirm the host has no remaining track agent before starting the next cycle.

## Cost and licensing

Only one backend runs at a time in this sequence. Each track adds one backend EC2 instance, its encrypted gp3 volume, a temporary Distributor package bucket, and an S3 archive bucket with the configured retention period. Default backend sizes are:

| Track | Default EC2 | gp3 volume |
|---|---:|---:|
| Wazuh | `t3.2xlarge` | 120 GiB |
| Graylog | `t3.2xlarge` | 80 GiB |
| VictoriaLogs | `t3.large` | 80 GiB |
| Quickwit | `t3.large` | 80 GiB |
| Splunk | `t3.xlarge` | 80 GiB |

Estimate each run as `backend instance hourly rate × running hours + gp3 GiB-month + public IPv4 hours + S3 compressed GB-month + S3 requests + internet transfer/CPU credits`. The root lab host already exists and remains running during each cycle, so its cost is common to all five results. Check the selected region's [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/), [S3](https://aws.amazon.com/s3/pricing/), and [public IPv4](https://aws.amazon.com/vpc/pricing/) rates. An instance stops billing for compute when stopped, but its EBS volume and allocated public IPv4 may still cost money. Destroying a track deletes its archive objects and package bucket so the next run does not accumulate old resources.

License and product caveats:

- Splunk Enterprise starts with a 60-day trial and a 500 MB/day index volume; the subsequent free license is also limited to 500 MB/day and disables some features. This can affect dashboard and search testing if the limit is crossed. [Splunk trial and free-license details](https://www.splunk.com/en_us/download.html).
- Wazuh components include GPLv2-licensed code; review the [Wazuh license](https://github.com/wazuh/wazuh/blob/main/LICENSE) and the licenses of its bundled components.
- Graylog Open is under SSPL; see [Graylog's current license description](https://graylog.org/products/source-available/). MongoDB and OpenSearch are additional components with their own licenses.
- VictoriaLogs is Apache 2.0; see the [VictoriaLogs documentation](https://docs.victoriametrics.com/victorialogs/). Quickwit is Apache 2.0; verify the pinned [Quickwit release license](https://github.com/quickwit-oss/quickwit/blob/main/LICENSE).

These are self-managed single-node lab stacks, not production sizing recommendations. The Wazuh and Graylog stacks use the most memory; start with VictoriaLogs or Quickwit if minimizing the incremental hourly compute cost is the first concern.

## State and safety

Never run Terraform from `logging/` itself; select one track directory. Keep its `.tfstate` separate from root state and from the other four tracks. The plan is the review gate: inspect instance type, ingress rules, S3 lifecycle, IAM actions, package contents, SSM association target, and destroy behavior. Do not apply without reviewing the plan. No track performs an AWS apply automatically.
