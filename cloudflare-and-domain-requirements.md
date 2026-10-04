# Cloudflare and Domain Requirements

This document explains what is required to publish the AI lab through a public HTTPS domain, which service owns each responsibility, why Cloudflare sits in front of AWS, and where those settings are represented in Terraform.

## Architecture at a glance

```text
User
  |
  | HTTPS: 443, Cloudflare-managed edge certificate
  v
Cloudflare DNS, proxy, and Cloudflare Access
  |
  | HTTPS: 443, ACM certificate on the AWS ALB
  v
AWS internet-facing Application Load Balancer
  |
  | HTTP: 8080, private security-group path
  v
EC2 instance running Open WebUI and Ollama
```

Cloudflare and AWS certificates have different jobs. Cloudflare encrypts the user-to-Cloudflare connection. The ACM certificate encrypts and identifies the AWS load balancer to Cloudflare. With Cloudflare set to **Full (strict)**, both connections are encrypted and the origin certificate is validated.

The Cloudflare encryption mode is a Cloudflare dashboard setting. Terraform does not manage it. See [Cloudflare security settings](#cloudflare-security-settings-configured-manually).

## Why Cloudflare is in front of AWS

| Benefit | What it means for this lab |
| --- | --- |
| Authentication before AWS | The Cloudflare Access application allows only the listed email addresses. Visitors who are not approved are stopped at Cloudflare's edge and their requests never reach the ALB or the EC2 instance. |
| Origin is hidden | The public hostname resolves to Cloudflare addresses, not to the ALB. The ALB hostname is not published in public DNS while the record is proxied (this is not the same as the ALB being unreachable; see [Lock the origin to Cloudflare](#cloudflare-security-settings-configured-manually)). |
| DDoS and bot mitigation | Cloudflare filters attack and bot traffic at its edge before it reaches AWS, which also limits load and cost on the ALB and instance. |
| Free edge TLS | Cloudflare issues a universal certificate that covers the apex domain and first-level subdomains, and handles TLS 1.3 and HTTP/2 to visitors. |
| DNS at the apex | Cloudflare flattens a CNAME at the zone apex, so `aiwebdemo.click` can point at the ALB's DNS name. Route 53 does this with an alias record instead. |
| WAF, rate limiting, caching, analytics | Available from the same dashboard. Which features are included depends on the Cloudflare plan. |
| One place for controls | DNS, TLS mode, access policy, and traffic rules are managed in one console, separate from the AWS account that runs the lab. |

### Trade-offs to know about

- **Proxy timeout.** Cloudflare returns a 524 error if the origin does not start responding within 125 seconds (the default Proxy Read Timeout; it can be changed only on Enterprise zones). A CPU-only model that is slow to produce its first response, for example right after a cold model load, can hit this limit. Streaming responses that begin sooner are not affected.
- **Access blocks non-browser clients.** Scripts and API clients that call the hostname directly will be stopped by Access unless a service token or another Access policy is added.
- **Cloudflare becomes a dependency.** An outage or an account compromise at Cloudflare affects the lab. Use two-factor authentication on the account.
- **The ACM validation record must live in Cloudflare.** Once Cloudflare is the authoritative DNS, certificate renewal depends on a record there (see [ACM certificate details](#acm-certificate-details)).
- **Access only protects traffic that goes through Cloudflare.** If the ALB is reachable directly, requests that bypass Cloudflare also bypass Access.

## Responsibilities by service

| Component | Responsibility | Required for domain access? | Terraform implementation |
| --- | --- | ---: | --- |
| Domain registrar | Owns the domain registration and delegates nameservers | Yes | External; not managed by this repository |
| Cloudflare zone | Authoritative DNS, proxying, edge TLS, and optional Access policy | Yes when Cloudflare is enabled | `cloudflare_dns_record.domain`, `cloudflare_zero_trust_access_application.domain` |
| AWS Route 53 | Provides the hosted zone when Cloudflare is disabled; may also retain an old zone after moving DNS to Cloudflare | Only for the Route 53 DNS path | `data.aws_route53_zone.public`, `aws_route53_record.domain` |
| AWS Certificate Manager | Provides the origin certificate used by the ALB HTTPS listener | Yes for the current AWS ALB design | `var.acm_certificate_arn`, `aws_lb_listener.https` |
| AWS Application Load Balancer | Public AWS entry point and HTTPS termination at the origin | Yes | `aws_lb.domain`, listeners, target group, security group |
| EC2 | Runs Open WebUI and Ollama | Yes | `aws_instance.ai_lab` |
| AWS Secrets Manager | Stores the Open WebUI administrator and demo-user passwords and the Cloudflare API token | Yes for the current secure bootstrap flow | Secret ARNs in variables; secrets are pre-created outside Terraform |
| Amazon Cognito (optional) | User directory and hosted sign-in page for Cloudflare Access and Open WebUI | Only when `enable_cognito = true` | `cognito.tf`, `cloudflare_zero_trust_access_identity_provider` |
| Control panel hosting (optional) | S3 bucket, CloudFront distribution and the `cp` DNS record that serve the panel's pages | Only for the control panel | Built by hand; see `dashboards/SETUP.md` |
| Control panel API (optional) | API Gateway, two Lambdas, DynamoDB tables and IAM roles behind the panel | Only for the control panel | Built by hand; Terraform adds only the demo rows and the authorizer update (`cognito.tf`, `control_panel_api.tf`) |
| Control panel sign-in (optional) | Signs people in to the panel through any OpenID Connect provider (Cognito, Okta, Entra ID) | Only for the control panel | Terraform creates the panel's app client in the lab's Cognito pool (`control_panel.tf`) |
| Auto-stop (on by default) | Stops the instance when nobody is using it and emails alerts through SNS | No; independent of domain access | `auto_stop.tf` (SSM parameter, Lambda watchdog, EventBridge rule, SNS topic) |

## What an administrator must provide

### AWS details

The administrator needs:

1. An AWS profile or another AWS credential source with permission to create and destroy the lab.
2. An AWS region. The current example uses `us-east-1`.
3. An existing Secrets Manager secret for the Open WebUI administrator password.
4. An existing Secrets Manager secret for the demo users' password.
5. An issued ACM certificate covering the public hostname, in the same AWS region as the ALB.
6. When Cloudflare is enabled, an existing Secrets Manager secret for the Cloudflare API token (see [Cloudflare details](#cloudflare-details)).
7. An email address that can receive auto-stop alerts (`auto_stop_alert_email`). After the apply, AWS sends one confirmation link to it and no alerts arrive until it is clicked. Auto-stop is on by default; with both `auto_stop_idle_minutes` and `auto_stop_max_uptime_minutes` set to `0` it is off, and then no address is needed.

Terraform does not create, rotate, update, or destroy any of the secrets. Create them first, in the same region, as plain text or as a one-key JSON object. Who reads them:

- The **EC2 instance** reads the administrator password through its IAM role to create the local administrator account. It reads the demo-user password only when local demo accounts are created (Cognito off, or local sign-in on without Cognito).
- **`scripts/set-cognito-passwords.ps1`**, which you run after `apply` when Cognito is on, reads both password secrets with your own AWS credentials. The administrator gets the administrator secret and every other Cognito user gets the demo-user secret. Cognito's password policy requires at least 8 characters including a lowercase letter and a number, so both passwords must meet it.
- The **PowerShell Terraform wrapper** reads the Cloudflare token secret and exposes it only for the run.

The identity that runs Terraform needs permission to create the EC2, IAM, security group, load balancer, SSM parameter, Lambda, SNS and EventBridge resources (and the Cognito resources when enabled), to read the ACM certificate, and to read the secrets.

When the control panel variables are set, the identity that runs Terraform also needs permission to update the panel API's authorizer (`apigateway:PATCH` on the API) and to write the demo rows to the `panel_users` table.

Relevant variables in `terraform.tfvars`:

```hcl
aws_region                              = "us-east-1"
aws_profile                             = null
open_webui_admin_password_secret_arn    = "arn:aws:secretsmanager:..."
open_webui_demo_user_password_secret_arn = "arn:aws:secretsmanager:..."
acm_certificate_arn                     = "arn:aws:acm:us-east-1:...:certificate/..."
auto_stop_idle_minutes                  = 0  # Demo: no idle shutdown
auto_stop_max_uptime_minutes            = 90 # Demo: hard stop 90 minutes after boot
auto_stop_alert_email                   = "you@yourdomain.com"
```

The actual AWS profile is selected in the PowerShell environment used for the smoke test. Keep the AWS CLI profile and `TF_VAR_aws_profile` value aligned.

### Domain and registrar details

The administrator needs:

1. A registered domain, such as `aiwebdemo.click`.
2. Access to the registrar's nameserver settings.
3. The ability to delegate the domain to the Cloudflare-assigned nameservers.
4. A hostname to publish, currently the zone apex `aiwebdemo.click`.

The registrar can be AWS Route 53, another registrar, or a separate domain provider. The registrar does not need to host DNS after delegation. When Cloudflare is used, the registrar only needs to point the domain's authoritative nameservers at Cloudflare.

**Delegate at the registrar, not in a DNS zone.** The nameserver change must be made where the domain is registered. For a domain registered through AWS, that is **Route 53 > Registered domains > the domain > Name servers**. Editing the NS record inside a Route 53 hosted zone does not change delegation. Resolvers keep following the registrar's nameservers, Cloudflare never confirms the delegation, and the Cloudflare zone stays in **Pending Nameserver Update**.

Use exactly the two nameservers shown on the Cloudflare zone's Overview page, and remove the previous provider's nameservers.

To confirm the change took effect:

- The Route 53 operation history shows **Update name servers** as successful.
- The registration data lookup (RDAP) at <https://lookup.icann.org/> lists the Cloudflare nameservers.
- The Cloudflare zone changes to **Active** and Cloudflare sends a confirmation email.
- `nslookup -type=ns <domain> 1.1.1.1` returns the Cloudflare nameservers. Public lookup sites can show the Cloudflare names before delegation moves if a stale NS record still exists in the old hosted zone, so do not rely on them alone.

If DNSSEC was ever enabled at the previous DNS provider, remove the DS records at the registrar before changing nameservers, or the zone will not activate.

These settings are represented by:

```hcl
domain_name       = "aiwebdemo.click"
route53_zone_name = "aiwebdemo.click"
```

`route53_zone_name` is used only when `enable_domain_access = true` and `enable_cloudflare_access = false`. When Cloudflare is enabled, Terraform looks up the Cloudflare zone instead and creates the DNS record there.

### ACM certificate details

The current implementation expects an already-issued ACM certificate. It must:

- Cover `domain_name`.
- Be in the same AWS region as the ALB.
- Have status `ISSUED`.
- Be usable by the AWS account running Terraform.

The certificate ARN is supplied through:

```hcl
acm_certificate_arn = "arn:aws:acm:us-east-1:...:certificate/..."
```

`acm_certificate_arn` has a default value in `variables.tf` that points at the demo certificate. Leaving it out of `terraform.tfvars` therefore still uses that ARN. When cloning this project for another account or domain, either set your own ARN or set the variable to `null`. With `null`, the configuration searches ACM for the most recent issued certificate matching `domain_name`. It never creates one.

The current Terraform configuration does not request or validate an ACM certificate. Certificate request, DNS validation, and certificate issuance are an administrative prerequisite.

**Keep the validation record after moving DNS to Cloudflare.** ACM validates and renews a DNS-validated certificate through a CNAME record in the domain's public DNS. If that record was created in Route 53 before the domain moved to Cloudflare, it stops resolving after the move, and the certificate cannot renew. Create the same CNAME in the Cloudflare zone:

| Field | Value |
| --- | --- |
| Type | CNAME |
| Name | The validation name shown in the ACM console, without the domain suffix (Cloudflare appends it) |
| Target | The validation value shown in the ACM console, without the trailing dot |
| Proxy status | **DNS only** (grey cloud). Do not proxy it. |

Leave this record out of Terraform's proxied record. Cloudflare may show a banner suggesting that all records be proxied; ignore it for this record.

### Cloudflare details

The administrator needs:

1. A Cloudflare account.
2. The Cloudflare zone for the domain added to that account, with status **Active**.
3. The domain delegated to Cloudflare nameservers.
4. A Cloudflare account ID.
5. A Cloudflare API token (see below).
6. Cloudflare Zero Trust (Access) enabled for the account before applying the Access resource, with a team domain such as `your-team.cloudflareaccess.com` (the host shown on the Access login page). Terraform needs it as `cloudflare_access_team_domain` when Cognito is on.
7. A sign-in method for Access. Either Cognito (`enable_cognito = true`, see [Optional: Amazon Cognito sign-in](#optional-amazon-cognito-sign-in)), where Terraform creates the Access login method for you, or a method you already configured under Zero Trust > Settings > Authentication, such as One-time PIN.
8. The email addresses allowed through the Access application. Without Cognito these are exactly `cloudflare_access_allowed_emails`, and at least one is required. With Cognito the policy also allows every Cognito user's email, so the list can stay empty.

The API token should be limited to this account and zone, with an expiry date. Create it at <https://dash.cloudflare.com/profile/api-tokens> with these permissions:

| Scope | Permission | Needed for |
| --- | --- | --- |
| Zone (this domain) | **Zone: Zone Read** | Looking up the zone |
| Zone (this domain) | **Zone: DNS Edit** | The proxied record for the hostname |
| Account | **Account: Access: Apps and Policies Edit** | The Access application and its allow policy |
| Account | **Account: Access: Organizations, Identity Providers, and Groups Edit** | The Cognito login method. Needed only when `enable_cognito = true`; without it the apply fails with a 403 when creating the identity provider |

Editing the permissions of an existing token does not change its value, so the secret in Secrets Manager does not need to be updated. If the 403 persists right after a permission change, wait a minute and plan again.

The token itself must not be committed to the repository. In this project it is stored in AWS Secrets Manager and retrieved by the PowerShell Terraform wrapper into the temporary `CLOUDFLARE_API_TOKEN` environment variable.

Relevant variables in `terraform.tfvars`:

```hcl
enable_domain_access     = true
enable_cloudflare_access = true
domain_name               = "aiwebdemo.click"
cloudflare_account_id     = "<32-character Cloudflare account ID>"
cloudflare_api_token_secret_arn = "arn:aws:secretsmanager:..."
cloudflare_access_allowed_emails = [] # With Cognito; otherwise list the allowed emails
cloudflare_access_team_domain    = "your-team.cloudflareaccess.com" # Used with Cognito
```

The API token secret should contain either the raw token or a one-key JSON object with the key `CLOUDFLARE_API_TOKEN`. The smoke-test wrapper supports both forms and trims whitespace from the key and token value.

## What Terraform creates

With domain and Cloudflare access enabled, Terraform creates:

- An internet-facing AWS Application Load Balancer.
- An AWS target group pointing to Open WebUI on the EC2 host port, currently 8080.
- An HTTPS listener on port 443 using `acm_certificate_arn`.
- Nothing on public port 80 by default. The optional `enable_alb_http_redirect = true` adds a port 80 listener that redirects to HTTPS; it is off because the first HTTP request is unencrypted.
- A proxied Cloudflare CNAME from `domain_name` to the ALB DNS name.
- A Cloudflare Access self-hosted application for `domain_name`.
- An Access allow policy for `cloudflare_access_allowed_emails`.

With Cloudflare disabled but domain access enabled, Terraform instead creates a Route 53 alias A record to the ALB. That path requires a public Route 53 hosted zone named by `route53_zone_name`.

## What Terraform does not create

Terraform does not currently:

- Register or renew the domain.
- Change the registrar's nameservers.
- Request, validate, or issue the ACM certificate, or create its validation record in Cloudflare.
- Create or rotate the Secrets Manager secrets.
- Enable Cloudflare Zero Trust at the account level.
- Create a Cloudflare account or add a domain to it.
- Set the Cloudflare SSL/TLS encryption mode or any other zone setting.
- Configure Cloudflare WAF, rate limiting, or email-related DNS records.
- Restrict the ALB to Cloudflare traffic, unless you set `enable_origin_lockdown = true`.
- Set the Cognito users' passwords (run `scripts/set-cognito-passwords.ps1` after `apply`).
- Confirm the auto-stop email subscription (click the AWS confirmation link once).
- Configure a Cloudflare Tunnel.
- Create the control panel's hosting, its `cp` DNS record, API, Lambdas, tables or roles (see [Optional: control panel](#optional-control-panel)).

Those actions must be completed before the corresponding Terraform resources can succeed, or by hand afterward.

## Cloudflare security settings (configured manually)

Do these in the Cloudflare dashboard after the zone is **Active**. They are listed in priority order.

1. **Set SSL/TLS encryption mode to Full (strict).** The ACM certificate on the ALB meets Cloudflare's requirements for this mode: it is unexpired, publicly trusted, and matches the hostname, and the ALB accepts HTTPS on port 443. Do not use **Flexible**: Cloudflare would connect to the ALB over HTTP on port 80, which the ALB does not open by default, so visitors get 522 errors (with `enable_alb_http_redirect = true` they would instead get a redirect loop). Automatic SSL/TLS may choose a mode on its own, so set it explicitly.
2. **Enforce HTTPS at the edge.** Turn on **Always Use HTTPS** and **Automatic HTTPS Rewrites**, set the minimum TLS version to 1.2, and keep TLS 1.3 enabled. Add HSTS only after the site works correctly over HTTPS, and start with a short max-age.
3. **Check the Access policy.** Confirm the application covers the whole hostname, that only the intended email addresses are allowed, and that a sign-in from an unlisted address is denied. Review the identity provider settings under Zero Trust > Settings > Authentication.
4. **Lock the origin to Cloudflare.** By default the ALB's security group allows port 443 from the whole internet, and Access is enforced only at Cloudflare's edge. Anyone who learns the ALB's DNS name can reach Open WebUI's login page without going through Access or the WAF. Set `enable_origin_lockdown = true` to restrict the ALB to [Cloudflare's published IP ranges](https://www.cloudflare.com/ips/). See "Origin lockdown" below for the rollout order and recovery path. Authenticated Origin Pulls or validating the Access JWT at the origin are stronger options that this repo does not implement.
5. **Turn on WAF protections and bot defenses.** With Access in front, these are a second layer rather than the first. Decisions for the Free plan:
   - **Free managed ruleset:** always active.
   - **Bot Fight Mode: on** (Security > Settings > Bot traffic). It cannot be exempted per path, so after enabling it confirm that the Access sign-in, a chat message and streaming replies still work. If one breaks, turn it off.
   - **Sign-in rate limit: skipped.** The Free plan allows one rate-limiting rule (per IP, 10-second window, 10-second block), and that slot is used by a "Leaked credential check" rule that blocks requests with leaked passwords. That rule is the more useful one to keep. Whether Cloudflare's leaked-credential detection recognises Open WebUI's sign-in request is not verified. Replacing it with a sign-in rate limit would be a weak speed bump, not brute-force protection. Cloudflare Access and Cognito sit in front of the sign-in path.
6. **Publish email protections even though the domain sends no mail.** This stops others from spoofing the domain: a null MX record (`0 .`), an SPF record of `v=spf1 -all`, and a DMARC record such as `v=DMARC1; p=reject`.
7. **DNSSEC: on.** See "DNSSEC" below. A wrong key at the registrar makes the whole domain fail to resolve for validating resolvers, so follow the steps and checks exactly.
8. **Protect the accounts.** Use two-factor authentication on the Cloudflare account and on the AWS and registrar accounts. Keep the API token scoped, set an expiry, and rotate the Secrets Manager copy when it changes. Keep the registrar's transfer lock on.

## DNSSEC

The domain is registered in Route 53 and its DNS is hosted at Cloudflare, so the signing key comes from Cloudflare and the DS record is registered at Route 53.

1. In Cloudflare, open DNS > Settings and choose **Enable DNSSEC**. Cloudflare shows the DS record details (key tag, algorithm, digest type, digest) and the public key. Do not click **Confirm** yet.
2. In Route 53, open Registered domains > your domain > **DNSSEC keys** > **Add key**. Set **Key type** to **257 - KSK** (matching Cloudflare's flags of 257), **Algorithm** to **13 - ECDSAP256SHA256**, and paste the **public key** from Cloudflare. Route 53 takes the public key, not the DS digest.
3. Compare the key tag and digest that Route 53 then lists with the ones Cloudflare shows. They must match exactly. Then click **Confirm** in Cloudflare.
4. Wait for the registry to publish the DS record (resolvers may cache the old value for up to 15 minutes), then run the DNSSEC check in the smoke test.

**Do not add the key as 256 - ZSK.** The flags are part of the DS calculation, so the registered DS will not match Cloudflare's key. Once the zone is signed, validating resolvers such as 8.8.8.8 and 1.1.1.1 return `SERVFAIL` and the site is unreachable for most visitors. To recover, remove the wrong key at Route 53 and add the correct one.

**Rollback order.** Remove the key at Route 53 first and wait for the DS record to expire, then disable DNSSEC in Cloudflare. Disabling it in Cloudflare first leaves a DS record with no matching key, which causes the same outage. Remove the DS record at the registrar before moving DNS to another provider.

## Origin lockdown

Set `enable_origin_lockdown = true` in `terraform.tfvars` to make the ALB accept HTTPS only from Cloudflare's published IPv4 and IPv6 ranges. Direct requests to the ALB's `*.elb.amazonaws.com` name then time out instead of reaching Open WebUI. The instance's own security group already accepts the application port only from the ALB, and the ALB has no public port 80 by default. The feature needs `enable_cloudflare_access = true` and no paid Cloudflare plan.

The ranges are the `cloudflare_ipv4_cidrs` and `cloudflare_ipv6_cidrs` variables in `variables.tf`, copied from https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6. Override them in `terraform.tfvars` when Cloudflare publishes changes.

**Safe rollout order**

1. Confirm the site works through Cloudflare: `nslookup <domain>` returns Cloudflare addresses, the Access sign-in works, and Open WebUI loads.
2. Run the direct-origin check once and note that it still connects (the "before" result).
3. Set `enable_origin_lockdown = true`, then plan and apply.
4. Re-run the public check and the direct-origin check (see the smoke test). The public site must still work and the direct request must time out.

**Recovery**

- If visitors get Cloudflare 522 or 523 errors after Cloudflare adds ranges, copy the current lists into `cloudflare_ipv4_cidrs` and `cloudflare_ipv6_cidrs` and apply again.
- If you are locked out of direct testing or need a short-term way in, add your address as a `/32` to `origin_lockdown_extra_cidrs` and apply; remove it afterwards.
- As a last resort, set `enable_origin_lockdown = false` and apply. That reopens the ALB to the internet, so treat it as temporary and turn lockdown back on once the ranges are fixed.
- Editing the security group in the AWS console also works in an emergency, but Terraform reverts the change at the next apply.

## Optional: Amazon Cognito sign-in

Set `enable_cognito = true` to use an Amazon Cognito user pool as the user
directory for both Cloudflare Access and Open WebUI. Without it, Access uses the
login methods already configured in your Zero Trust organization (for example
One-time PIN or the Cloudflare login) and Open WebUI uses its local accounts.

With Cognito enabled, Terraform creates:

- A Cognito user pool with no self-service sign-up, a hosted sign-in domain
  (`cognito_domain_prefix`), and one app client used by Cloudflare and Open WebUI.
- A Cognito user for the Open WebUI administrator (`open_webui_admin_email`), one
  for each of the `open_webui_demo_users`, and one for each of the optional
  `cognito_extra_users`; duplicate emails are merged. They are created without
  passwords and with the email marked verified, so no email is sent. Run
  `scripts/set-cognito-passwords.ps1` after `apply` (and again after adding users):
  the administrator gets the administrator password secret and everyone else gets the
  demo-user password secret.
- A Cloudflare Access login method of type OpenID Connect that points at the pool.
  The Access application offers only this login method and sends visitors straight
  to it. The Access policy allows the emails of the Cognito users.
- Open WebUI single sign-on through the same pool. The container reads the app
  client secret from Cognito at boot through the instance role, so the secret is
  not in user-data. An Open WebUI account is matched to the Cognito sign-in by email,
  so the administrator's local account and Cognito sign-in reach the same account.
  New Cognito users get the role in `open_webui_default_user_role` (`user` by
  default; `pending` makes an administrator approve each new user first).
- The administrator is the only person with a local Open WebUI password. Demo
  users exist only in Cognito. The password form stays on for the administrator
  while `open_webui_enable_local_login` is `true` (the default). Set it to `false`
  to allow Cognito sign-in only; it requires `enable_cognito = true` and replaces the
  EC2 instance. The README section "Local accounts and turning them off" explains
  who can sign in each way.

Requirements and cautions:

- `enable_cognito` requires `enable_domain_access`, because the sign-in callbacks use
  the public domain name, and `enable_cloudflare_access` for the Access login method.
- Set `cloudflare_access_team_domain` to the host shown on the Access login page
  (for example `your-team.cloudflareaccess.com`). Terraform adds
  `https://<team domain>/cdn-cgi/access/callback` as a Cognito callback URL.
- The Cloudflare API token needs the **Identity Providers** permission listed under
  [Cloudflare details](#cloudflare-details), or creating the login method fails with a 403.
- `cognito_domain_prefix` must be unique within the AWS region.
- The app client secret is stored in Terraform state, as is the Cloudflare login
  method that uses it. Keep the state file private.
- The administrator's email is added automatically. Use `cognito_extra_users` only for
  other people who need access; they are created the same way and get the demo-user
  password.
- Cognito users with `example.local` addresses cannot receive mail, so password
  resets are administrator-only by design.

### Sign-in banner and branding

- `security_banner_text` sets the security and acceptable-use banner. Open WebUI
  shows it inside the app after sign-in, because Cognito's hosted sign-in page can only
  change its logo and styling, not add text.
- The Cognito sign-in page logo and styling come from the files in `branding/`; see
  `branding/README.md` for how to replace them. Keep Open WebUI's own branding visible
  unless you meet the Open WebUI license terms for removing it.

## Optional: control panel

The control panel (`dashboards/`) is a separate web app where customers start the instances they are granted, and administrators manage who may start what. It lives on its own address (`https://cp.aiwebdemo.click`) and is built and kept by hand, so it does not depend on the lab being deployed. Without a lab it shows no instances. Without users it shows no users. See `dashboards/SETUP.md` for what exists and `dashboards/api/README.md` for the API.

### What an administrator must provide

- **AWS permissions** to create the S3 bucket, CloudFront distribution, DynamoDB tables, IAM roles, Lambda functions and API Gateway API.
- **A DNS record** in the Cloudflare zone: a CNAME from `cp` to the CloudFront domain, set to **DNS only** (not proxied), because CloudFront already terminates TLS for it.
- **A certificate** in us-east-1 that covers the panel's host name. The existing wildcard certificate for the domain does.
- **A sign-in provider**, one of Cognito, Okta, Entra ID or any OpenID Connect provider, with a single-page app client (public, no secret, PKCE), the panel's address registered as a redirect URI, and a verified `email` claim in the ID token. Entra ID needs `email` added as an optional claim. The panel needs the provider's issuer URL and the app client ID.
- **Bootstrap administrators**: the email addresses in the API's `BOOTSTRAP_ADMINS` setting. They always have the user manager role, so the panel can be opened before any user exists.

### Who owns what

| Piece | Owner |
|---|---|
| Hosting, DNS record, API, Lambdas, tables, IAM roles, the authorizer and its routes | Built by hand, kept when the lab is destroyed |
| The panel's app client in the lab's Cognito pool | Terraform (`control_panel.tf`) |
| The demo users' rows in `panel_users` (every demo user as `operators`, plus `user_mgrs` for the odd demo users and `admin@example.local`), when `control_panel_users_table` is set | Terraform |
| The lab's instance, target group and service address, as SSM parameters, and the `control-panel=managed` tag on the instance | Terraform (`control_panel_lab.tf`) |
| The authorizer's issuer and audience | Terraform on apply and destroy, when `control_panel_api_id`, `control_panel_authorizer_id` and `control_panel_holding_pool_id` are set (`control_panel_api.tf`) |
| Real users, their roles and instance grants | The control panel. Terraform never touches them |

Until a provider is wired, the authorizer trusts an empty holding Cognito pool that can never issue a token, so every API route answers 401. Apply points the authorizer at the lab's pool, and destroy points it back at the holding pool. The routes are never recreated. To use another provider, update the authorizer's issuer and audience by hand and upload a `config.js` for it.

### Instance wiring

The panel learns which instance and ALB target group to manage from SSM parameters that Terraform writes under `/<project_name>/control-panel/` (`instance-ids`, `target-group-arn`, `service-url`) when `control_panel_url` is set. The Lambdas read them live, so a new lab is picked up with nothing to copy, and a destroyed lab leaves the panel with no instances. Terraform also tags the instance `control-panel=managed`; the customer role can start only instances with that tag. The Lambdas' roles need read access to that parameter path.

## Can the providers be swapped?

### Domain registrar

Yes. The registrar is largely provider-independent. Any registrar can be used if it supports delegation to Cloudflare's nameservers. No Terraform code change is required merely because the domain was registered somewhere other than AWS.

### DNS provider

Partially. The current code supports two DNS paths:

- Cloudflare DNS when `enable_cloudflare_access = true`.
- AWS Route 53 when `enable_cloudflare_access = false`.

Using another DNS provider would require replacing the Route 53 or Cloudflare resources and provider configuration in `main.tf`. It is not a variable-only substitution.

### Certificate provider

Partially. The current ALB listener is AWS-specific and consumes an ACM certificate ARN. A certificate from another public certificate authority could be used only after it is imported into ACM or the Terraform implementation is changed to manage the alternate certificate and attach it to the ALB. The certificate still needs to match the hostname and be available to the load balancer.

### Control panel sign-in provider

Yes. The panel and its API accept any OpenID Connect provider. Changing it means updating the authorizer's issuer and audience and uploading a new `config.js`. No code changes.

### Hosting provider

Not by changing variables alone. The lab currently depends on AWS-specific services and APIs:

- EC2
- the default VPC and subnets
- security groups
- IAM and SSM
- Secrets Manager
- Application Load Balancer
- ACM
- optionally Route 53

Moving the application to another hosting provider would require a separate Terraform implementation for that provider, including compute, networking, TLS termination, secret retrieval, health checks, and administration access. Cloudflare could remain in front of it if the replacement provider exposes a reachable HTTPS origin.

## Recommended prerequisite order

1. Choose the AWS account and region.
2. Create the two Open WebUI password secrets (administrator and demo user) in AWS Secrets Manager.
3. Obtain and validate the ACM certificate in the ALB region.
4. Register the domain.
5. Add the domain to Cloudflare and note the two assigned nameservers.
6. Change the nameservers at the registrar (not in a hosted zone) to the Cloudflare pair.
7. Wait for the Cloudflare zone to become **Active**.
8. Create the ACM validation CNAME in Cloudflare as DNS-only.
9. Enable Cloudflare Zero Trust (Access) and note the team domain.
10. Create a scoped Cloudflare API token, with the Identity Providers permission if you will use Cognito, and store it in Secrets Manager.
11. Choose the email address for auto-stop alerts.
12. Copy `terraform.tfvars.example` to `terraform.tfvars` and replace every value that belongs to the author's account, such as the account ID, secret and certificate ARNs, domain, and team domain.
13. Run the smoke test plan from initialization through apply.
14. After the apply: run `scripts/set-cognito-passwords.ps1` if Cognito is on, and click the SNS confirmation link in the alert mailbox.
15. Set the Cloudflare SSL/TLS mode to Full (strict) and review the other security settings above.
16. Verify the ALB target health and the public HTTPS URL.

17. Optional, for the control panel: build it as described in `dashboards/SETUP.md`, add the `cp` CNAME in Cloudflare as DNS only, and set the control panel variables in `terraform.tfvars`. The panel can be built and left in place before the lab exists.

The lab can remain destroyed while waiting for domain delegation or Cloudflare activation. Those control-plane prerequisites do not require an EC2 instance or load balancer to be running.
