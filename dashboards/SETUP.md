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
| S3 bucket for the static files | Private. Block all public access. Only CloudFront reads it. | 2026-10-04: `aiwebdemo-control-panel-394566733278` |
| ACM certificate for `cp.aiwebdemo.click` | Must be in us-east-1 for CloudFront. The existing `aiwebdemo.click` certificate does not cover this name. Validate it with the CNAME below. | 2026-10-04, `arn:aws:acm:us-east-1:394566733278:certificate/52e00bc0-78f6-4c11-909c-aacb16d471c8`, waiting for DNS validation |
| CloudFront distribution | Origin access control to the bucket. Alias `cp.aiwebdemo.click`. Certificate in us-east-1 that covers that name. Response headers policy (CSP, HSTS). WAF if wanted. | |
| DNS record `cp.aiwebdemo.click` | CNAME to the CloudFront domain, in the Cloudflare zone for aiwebdemo.click. | |
| DynamoDB tables `instance_entitlements` and `control_panel_events` | Keys and TTL in `api/README.md`. | 2026-10-04 (TTL on `expiresAt` set) |
| Control API (Lambda and API Gateway HTTP API) | Source in `api/handler.py`. Steps, settings and routes in `api/README.md`. | |
| Role for the API | Least-privilege policy in `api/README.md`. It can start one instance and cannot stop any. | |

## Still to build

These need things Terraform creates when the lab is deployed (the instance, target group, user pool and panel app client), so they wait for `apply`:

- Role for the API, the Lambda, the API Gateway routes and JWT authorizer.
- CloudFront distribution (also waits for the certificate to validate).
- DNS record `cp.aiwebdemo.click`, a CNAME to the CloudFront domain.

## Certificate validation record

Add this in Cloudflare (DNS only, not proxied):

| Type | Name | Value |
|---|---|---|
| CNAME | `_b10f8b3db7ab139a6871da908eaad469.cp` | `_7959b1b55044055f3e50b35a49be3d58.wzccmgtwzk.acm-validations.aws` |

## Deploy the pages

```powershell
$bucket = '<bucket name>'
aws s3 sync C:\GitHub\ai-cloud-lab\dashboards "s3://$bucket" --exclude "*.md" --exclude "config.example.js" --exclude "api/*" --delete
aws cloudfront create-invalidation --distribution-id <distribution id> --paths "/*"
```

