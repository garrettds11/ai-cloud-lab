# Control panel setup (built by hand, outside Terraform)

The control panel at `https://cp.aiwebdemo.click` is built and kept by hand. Terraform does not create it, change it or know about it. This page is the record of what exists, so nothing is a surprise later.

Run every command in PowerShell, in the same window where `AWS_PROFILE` and `AWS_DEFAULT_REGION` are set. Account 394566733278, region us-east-1.

## What Terraform provides (the contract)

The panel depends on these Terraform-owned things. Do not rename or replace them without updating the panel.

| Needed by the panel | Where it comes from |
|---|---|
| Cognito user pool ID | Terraform output `cognito_user_pool_id` |
| Instance ID | Terraform output `instance_id` |
| Groups `operators` and `user_mgrs` (exact names) | `cognito.tf` |
| Auto-stop setting `/<project_name>/auto-stop` and CloudWatch namespace `AILab` | `auto_stop.tf` and the instance's cloud-init |

Read the outputs through the repo's wrapper, not bare terraform:

```powershell
Invoke-TerraformWithCloudflareToken -Arguments @('output', '-raw', 'cognito_user_pool_id')
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

Fill in the "Created" column as each piece is built.

| Piece | Notes | Created |
|---|---|---|
| S3 bucket for the static files | Private. Block all public access. Only CloudFront reads it. | |
| CloudFront distribution | Origin access control to the bucket. Alias `cp.aiwebdemo.click`. Certificate in us-east-1 that covers that name. Response headers policy (CSP, HSTS). WAF if wanted. | |
| DNS record `cp.aiwebdemo.click` | CNAME to the CloudFront domain, in the Cloudflare zone for aiwebdemo.click. | |
| Cognito app client for the panel | Public client, no secret, authorization code with PKCE. Callback URL `https://cp.aiwebdemo.click/`. | |
| DynamoDB table `instance_entitlements` | Schema in the PRD, FR-4. | |
| Control API (API Gateway and Lambda) | Checks the sign-in token's groups and the entitlement before every Start. | |
| Roles for the API | Start only the listed instances; read and write the table; add and remove users in the two groups; read logs. | |

## Deploy the pages

```powershell
$bucket = '<bucket name>'
aws s3 sync C:\GitHub\ai-cloud-lab\dashboards "s3://$bucket" --exclude "*.md" --delete
aws cloudfront create-invalidation --distribution-id <distribution id> --paths "/*"
```

Before this goes live, `mock-api.js` is replaced by a module that calls the Control API (see README).
