# Control panel setup (built by hand, outside Terraform)

The control panel at `https://cp.aiwebdemo.click` is built and kept by hand. Terraform does not create it, change it or know about it. This page is the record of what exists, so nothing is a surprise later.

Run every command in PowerShell, in the same window where `AWS_PROFILE` and `AWS_DEFAULT_REGION` are set. Account 394566733278, region us-east-1.

## What Terraform provides (the contract)

The panel depends on these Terraform-owned things. Do not rename or replace them without updating the panel.

| Needed by the panel | Where it comes from |
|---|---|
| Region, user pool ID, app client ID, API address | Terraform output `control_panel_config`, written to `dashboards/config.js` by `scripts/make-panel-config.ps1` |
| Instance ID | Terraform output `instance_id` |
| Groups `operators` and `user_mgrs` (exact names) | `cognito.tf` |
| Auto-stop setting `/<project_name>/auto-stop` and CloudWatch namespace `AILab` | `auto_stop.tf` and the instance's cloud-init |

Terraform needs `control_panel_url` set in `terraform.tfvars` (and `control_panel_api_url` once the API exists). After an apply, write the config file, then upload it with the pages:

```powershell
.\scripts\make-panel-config.ps1
```

Read other outputs through the repo's wrapper, not bare terraform:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @('output', '-raw', 'instance_id')
```

Terraform changes that break the panel: replacing the user pool (every user ID and group is lost), replacing the instance (new instance ID), renaming the groups. Check the plan for these before every apply.

## Who owns what

| | Owner |
|---|---|
| Demo users, and which demo users are in `operators` (odd) and `user_mgrs` (`admin@example.local`) | Terraform. Every apply puts them back. |
| Real users and their groups | The control panel. Terraform never touches them. |
| Who may start which instance (`instance_entitlements`) | The control panel, through the Control API. |

## What is built by hand

The panel's Cognito app client is not on this list: Terraform creates it (`control_panel.tf`).

Fill in the "Created" column as each piece is built.

| Piece | Notes | Created |
|---|---|---|
| S3 bucket for the static files | Private. Block all public access. Only CloudFront reads it. | |
| CloudFront distribution | Origin access control to the bucket. Alias `cp.aiwebdemo.click`. Certificate in us-east-1 that covers that name. Response headers policy (CSP, HSTS). WAF if wanted. | |
| DNS record `cp.aiwebdemo.click` | CNAME to the CloudFront domain, in the Cloudflare zone for aiwebdemo.click. | |
| DynamoDB tables `instance_entitlements` and `control_panel_events` | Keys and TTL in `api/README.md`. | |
| Control API (Lambda and API Gateway HTTP API) | Source in `api/handler.py`. Steps, settings and routes in `api/README.md`. | |
| Role for the API | Least-privilege policy in `api/README.md`. It can start one instance and cannot stop any. | |

## Deploy the pages

```powershell
$bucket = '<bucket name>'
aws s3 sync C:\GitHub\ai-cloud-lab\dashboards "s3://$bucket" --exclude "*.md" --exclude "config.example.js" --exclude "api/*" --delete
aws cloudfront create-invalidation --distribution-id <distribution id> --paths "/*"
```

Before this goes live, `mock-api.js` is replaced by a module that calls the Control API (see README).
