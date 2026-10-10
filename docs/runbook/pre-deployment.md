# Pre-deployment

Everything the lab needs that Terraform does not create. Do it once per AWS account and domain,
before the first deploy, then follow [the deploy runbook](terraform-smoke-test-plan.md) from step 1.

On the demo account all of it already exists. The **Demo** column in the checklist shows the value
in use, which is also what the committed `terraform.tfvars.example` files hold. Nothing here is
repeated on a normal deploy.

Commands are **Local Windows PowerShell**, in a window where `$env:AWS_PROFILE` and
`$env:AWS_DEFAULT_REGION` are set (runbook step 1).

## Checklist

| # | Item | Needed for | Demo |
|---|---|---|---|
| 1 | [Tools on your PC](#1-tools-on-your-pc) | Everything | Installed |
| 2 | [AWS account, region and permissions](#2-aws-account-region-and-permissions) | Everything | `394566733278`, `us-east-1`, GPU quota |
| 3 | [Secrets in Secrets Manager](#3-secrets-in-secrets-manager) | Passwords, Cloudflare, Grafana, the vulnerability tool | Five secrets, all created |
| 4 | [Domain and DNS](#4-domain-and-dns) | Public access | `aiwebdemo.click`, zone **Active** in Cloudflare |
| 5 | [TLS certificate](#5-tls-certificate) | Public access | ACM `1163bb42-...`, covers the apex and `*.` |
| 6 | [Cloudflare account, Zero Trust and API token](#6-cloudflare-account-zero-trust-and-api-token) | Public access, Cognito sign-in | Team domain `green-union-d3da.cloudflareaccess.com` |
| 7 | [Grafana Cloud](#7-grafana-cloud) | Telemetry (on by default) | Stack `1846719` |
| 8 | [Control panel hosting](#8-control-panel-hosting) | The control panel | `cp.aiwebdemo.click` on CloudFront `E2799OUDXX2ED3` |
| 9 | [Vulnerability findings table](#9-vulnerability-findings-table-optional) | The vulnerability tool (optional) | `aiwebdemo-vuln-findings` |
| 10 | [Auto-stop alert email](#10-auto-stop-alert-email) | Auto-stop alerts | Set |
| 11 | [The two settings files](#11-the-two-settings-files) | Every deploy | Committed |
| 12 | [One-time settings after the first deploy](#12-one-time-settings-after-the-first-deploy) | Hardening | Done |

## 1. Tools on your PC

- **Terraform 1.7 or later** (the lab needs 1.6, the control panel API stack and the offline tests
  need 1.7; CI uses 1.9.8).
- **AWS CLI v2**, signed in with a profile for the lab's account.
- **AWS Session Manager plugin**, for `aws ssm start-session` (shells and port forwarding).
- **Windows PowerShell 5.1 or PowerShell 7**.
- **Git**, and Python 3 only if you run the repository's tests.

## 2. AWS account, region and permissions

1. An AWS account and a CLI profile for it. Both settings files keep `aws_profile = null`, so the
   profile comes from `$env:AWS_PROFILE` in each window (runbook step 1).
2. A region. The demo uses `us-east-1`. The certificate, the secrets and the lab must all be in it.
3. A **default VPC** in that region, or a Terraform change to use another VPC and subnets.
4. Permission for the identity that runs Terraform to create and destroy the EC2, IAM, security
   group, load balancer, SSM parameter, Lambda, SNS and EventBridge resources (and Cognito when
   enabled), to read the ACM certificate and the secrets, and, for the control panel, to manage
   API Gateway, Lambda, DynamoDB and IAM for the API stack.
5. Quota for the instance type in the region. The demo uses `g6.xlarge`, an NVIDIA GPU type, which
   needs the EC2 quota **Running On-Demand G and VT instances** to be at least its 4 vCPUs. New
   accounts often have 0. Check it, and request more if needed (approval can take a day):

   ```powershell
   aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA --query Quota.Value
   aws service-quotas request-service-quota-increase --service-code ec2 --quota-code L-DB2E81BA --desired-value 8
   ```

   CPU types such as `c7i.4xlarge` use the **Standard instances** quota instead.

## 3. Secrets in Secrets Manager

Terraform never creates, rotates or deletes these. It only reads their ARNs, so no secret value is
ever in Terraform state, user data or the repository. Create each one in the lab's region, as plain
text or as a one-key key/value secret.

**Use the full ARN.** Secrets Manager ends every ARN with a hyphen and six random characters, and
IAM matches ARNs exactly. A shortened ARN gives the readers no access, and plan rejects it.

| Secret | Setting | Read by | Needed when |
|---|---|---|---|
| Open WebUI administrator password | `open_webui_admin_password_secret_arn` | The instance (local admin account), `scripts/set-cognito-passwords.ps1` | Always |
| Demo users' password | `open_webui_demo_user_password_secret_arn` | The instance (local demo accounts), `scripts/set-cognito-passwords.ps1` | Demo users exist |
| Cloudflare API token | `cloudflare_api_token_secret_arn` | The runbook's Terraform wrapper | Cloudflare is on |
| Grafana Cloud access token | `grafana_credentials_secret_arn` | The instance (Alloy) | Telemetry is on (default) |
| Vulnerability tool bearer token | `vuln_mcp_token_secret_arn` | The instance (registration) and the tool's Lambda | `vuln_mcp_table_name` is set |

**Passwords** must meet Cognito's policy: at least 8 characters, with a lowercase letter and a
number. To create a password or token secret without the value appearing on screen or in your
history (change the name each time):

```powershell
$sec   = Read-Host "Value" -AsSecureString
$value = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
$file  = New-TemporaryFile
[IO.File]::WriteAllText($file.FullName, $value)
aws secretsmanager create-secret --name <secret-name> --secret-string "file://$($file.FullName)" --region us-east-1 --query ARN --output text
Remove-Item $file.FullName
Remove-Variable value, sec
```

The last command prints the ARN to put in the setting.

**The Cloudflare token** comes from step 6. The wrapper accepts the raw token or a one-key secret
with the key `CLOUDFLARE_API_TOKEN`.

**The Grafana token** comes from step 7.

**The vulnerability tool token** is generated, not chosen. This creates the secret with a random
token and prints its ARN:

```powershell
$bytes = New-Object byte[] 32
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
$token = [Convert]::ToBase64String($bytes)
aws secretsmanager create-secret --name vuln-mcp-token-aiwebdemo --secret-string $token --region us-east-1 --query ARN --output text
```

If the secret already exists, `create-secret` fails with `ResourceExistsException`; keep the
existing one.

**Rotating the admin password later:** change it in Open WebUI first, then update the secret.
Changing only the secret does not change an Open WebUI account that already exists.

## 4. Domain and DNS

1. **Register the domain**, at any registrar (the demo's is Route 53).
2. **Add it to Cloudflare** and note the two nameservers on the zone's Overview page.
3. **Delegate at the registrar, not in a DNS zone.** For a domain registered through AWS, that is
   **Route 53 > Registered domains > the domain > Name servers**. Editing the NS record inside a
   Route 53 hosted zone does not change delegation, and the Cloudflare zone stays in
   **Pending Nameserver Update**. Use exactly the two Cloudflare nameservers and remove the old
   ones. If DNSSEC was ever on at the previous DNS provider, remove its DS record at the registrar
   first, or the zone will not activate.
4. **Wait for the zone to be Active.** Check:
   - Route 53's operation history shows **Update name servers** as successful.
   - <https://lookup.icann.org/> lists the Cloudflare nameservers.
   - Cloudflare shows the zone **Active** and sends a confirmation email.
   - `nslookup -type=ns <domain> 1.1.1.1` returns the Cloudflare nameservers.

Terraform creates the record for the lab's own host name (`domain_name`) on every apply and removes
it on destroy. Do not create it by hand.

Settings: `domain_name`, and `route53_zone_name` only when Cloudflare is off.

## 5. TLS certificate

The load balancer needs an **issued ACM certificate** that:

- covers `domain_name` (the demo's covers `aiwebdemo.click` and `*.aiwebdemo.click`, which also
  covers the control panel's `cp` name);
- is in the lab's region;
- has status `ISSUED`.

Request it in ACM with DNS validation. **Create the validation record in Cloudflare**, as Cloudflare
is now the domain's DNS. Without it, ACM cannot renew the certificate:

| Field | Value |
| --- | --- |
| Type | CNAME |
| Name | The validation name shown in ACM, without the domain suffix (Cloudflare appends it) |
| Target | The validation value shown in ACM, without the trailing dot |
| Proxy status | **DNS only** (grey cloud). Ignore Cloudflare's suggestion to proxy it. |

Setting: `acm_certificate_arn`. With `null`, Terraform uses the newest issued certificate for
`domain_name`; it never requests one.

## 6. Cloudflare account, Zero Trust and API token

1. **Enable Cloudflare Zero Trust (Access)** for the account and note the team domain, the host
   shown on the Access login page (for example `your-team.cloudflareaccess.com`).
2. **Note the account ID** (32 characters).
3. **Create an API token** at <https://dash.cloudflare.com/profile/api-tokens>, limited to this
   account and zone, with an expiry date:

   | Scope | Permission | Needed for |
   | --- | --- | --- |
   | Zone (this domain) | **Zone: Zone Read** | Looking up the zone |
   | Zone (this domain) | **Zone: DNS Edit** | The proxied record for the host name |
   | Account | **Account: Workers Scripts Edit** | The "lab unavailable" Worker. Only with `control_panel_url` set |
   | Zone (this domain) | **Zone: Workers Routes Edit** | That Worker's route. Only with `control_panel_url` set |
   | Account | **Account: Access: Apps and Policies Edit** | The Access application and its policy |
   | Account | **Account: Access: Organizations, Identity Providers, and Groups Edit** | The Cognito login method. Only with `enable_cognito = true`; without it apply fails with a 403 |

4. **Store the token** as a secret (step 3). Never put it in a file.

Changing an existing token's permissions keeps its value, so the secret does not change. If a 403
persists right after a change, wait a minute and plan again.

Settings: `cloudflare_account_id`, `cloudflare_api_token_secret_arn`,
`cloudflare_access_team_domain` (with Cognito), and `cloudflare_access_allowed_emails` (without
Cognito: at least one; with Cognito it can stay empty, because every Cognito user is allowed).

## 7. Grafana Cloud

Telemetry is on by default. Either set it up, or set `enable_grafana_telemetry = false`.

From the stack's **OTLP Endpoint** page:

1. The **OTLP endpoint**, for example `https://otlp-gateway-prod-us-east-3.grafana.net/otlp`.
2. The **OTLP instance ID** (a number). Not a secret.
3. An **access policy token** with `metrics:write`, `logs:write` and `traces:write` (the
   predefined `stack-<id>-otlp-write` policy works). Choose an expiry and note the date:
   telemetry stops when the token expires. Store it as a secret (step 3).

Settings: `grafana_otlp_endpoint`, `grafana_otlp_instance_id`, `grafana_credentials_secret_arn`.
What it sends and what it costs: [grafana-telemetry.md](../../grafana-telemetry.md).

## 8. Control panel hosting

The panel's pages are served from S3 through CloudFront, built by hand and kept when the lab is
destroyed. Its API is **not** built here: the runbook's step 2 deploys it with Terraform.

1. **S3 bucket** for the pages. Private, with all public access blocked.
2. **CloudFront distribution** in front of it: origin access control to the bucket (the bucket
   policy allows only this distribution), alias `cp.<domain>`, HTTPS only, TLS 1.2 minimum, a
   response headers policy with CSP, HSTS and no framing. The step 5 certificate covers the alias
   when it includes `*.<domain>`.
3. **DNS record** in Cloudflare: CNAME `cp` to the CloudFront domain, **DNS only** (CloudFront
   already terminates TLS).
4. **Sign-in provider:** with the lab's Cognito pool (the demo), Terraform creates the panel's app
   client, so there is nothing to do here. With Okta, Entra ID or another OpenID Connect provider,
   create a single-page app client (public, no secret, PKCE) with the panel's address as a redirect
   URI and a verified `email` claim in the ID token (Entra ID needs `email` added as an optional
   claim), and note the issuer URL and client ID.
5. **Bootstrap administrators:** the email addresses that always get the panel's admin screens, so
   it can be opened before any user exists. They go in the API stack's `bootstrap_admins` (step 11).

What exists on the demo account, with its IDs: [SETUP.md](../../SETUP.md).

Settings: `control_panel_url`, `control_panel_bucket`, `control_panel_distribution_id` in the root
file, and the API stack's own file (step 11).

## 9. Vulnerability findings table (optional)

Only if you use the vulnerability tool. Create the findings table with `create_vuln_table.py` in the
`sec-data` repository (the demo's is `aiwebdemo-vuln-findings`), and the tool's token secret
(step 3).

Settings: `vuln_mcp_table_name`, `vuln_mcp_token_secret_arn`.

## 10. Auto-stop alert email

An address that receives auto-stop warnings and alerts. Auto-stop is on by default; with
`auto_stop_idle_minutes` and `auto_stop_max_uptime_minutes` both `0` it is off and no address is
needed. AWS emails a confirmation link after each apply that creates the alert topic, and no alerts
arrive until it is clicked.

Setting: `auto_stop_alert_email`.

## 11. The two settings files

Put the values from the steps above in the two examples, then **commit them**. Every deploy copies
them over the working `terraform.tfvars` (runbook step 1), so they are the source of truth. Never
put a password or token in either file.

**Root `terraform.tfvars.example`:**

- `aws_region`, `instance_type`, `root_volume_size`, `llm_model`, `ollama_context_length`
- `open_webui_admin_password_secret_arn`, `open_webui_demo_user_password_secret_arn`
- `domain_name`, `route53_zone_name`, `acm_certificate_arn`
- `cloudflare_account_id`, `cloudflare_api_token_secret_arn`, `cloudflare_access_allowed_emails`,
  `cloudflare_access_team_domain`
- `cognito_domain_prefix` (unique in the region; must not contain `aws`, `amazon` or `cognito`)
- `grafana_otlp_endpoint`, `grafana_otlp_instance_id`, `grafana_credentials_secret_arn`
- `control_panel_url`, `control_panel_bucket`, `control_panel_distribution_id`
- `vuln_mcp_table_name`, `vuln_mcp_token_secret_arn` (optional)
- `auto_stop_alert_email`

**API stack `dashboards\api\terraform\terraform.tfvars.example`:**

- `lab_project_name`: the same value as the root `project_name`. The API publishes its settings
  under it, and the lab looks for them under `project_name`; a mismatch leaves the lab without the
  API (the `control_panel_api_settings_found` warning).
- `panel_origin`: the control panel's address (the root `control_panel_url`).
- `bootstrap_admins`: the email addresses that get the panel's admin screens.
- `adopt_existing_tables`: `false` on a new account, so the panel's tables are created.

## 12. One-time settings after the first deploy

Done once, in the Cloudflare dashboard, after the first lab apply. The details and the reasons are in
[cloudflare-and-domain-requirements.md](../../cloudflare-and-domain-requirements.md#cloudflare-security-settings-configured-manually).

1. SSL/TLS encryption mode **Full (strict)**.
2. **Always Use HTTPS**, **Automatic HTTPS Rewrites**, minimum TLS 1.2.
3. Check the Access policy covers the whole host name and denies an unlisted address.
4. Optionally lock the load balancer to Cloudflare (`enable_origin_lockdown = true`).
5. Bot Fight Mode, with a sign-in and chat check afterwards.
6. Null MX, SPF and DMARC records, as the domain sends no mail.
7. [DNSSEC](../../cloudflare-and-domain-requirements.md#dnssec), following its steps exactly.
8. Two-factor authentication on the Cloudflare, AWS and registrar accounts, and the registrar's
   transfer lock.
