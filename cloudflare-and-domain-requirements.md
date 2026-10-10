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
- **The ACM validation record must live in Cloudflare.** Once Cloudflare is the authoritative DNS, certificate renewal depends on a record there (see [pre-deployment.md, step 5](docs/runbook/pre-deployment.md#5-tls-certificate)).
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
| Control panel hosting (optional) | S3 bucket, CloudFront distribution and the `cp` DNS record that serve the panel's pages | Only for the control panel | Built by hand; see `SETUP.md` |
| Control panel API (optional) | API Gateway, two Lambdas, DynamoDB tables and IAM roles behind the panel | Only for the control panel | Built by hand; Terraform adds only the demo rows and the authorizer update (`cognito.tf`, `control_panel_api.tf`) |
| Control panel sign-in (optional) | Signs people in to the panel through any OpenID Connect provider (Cognito, Okta, Entra ID) | Only for the control panel | Terraform creates the panel's app client in the lab's Cognito pool (`control_panel.tf`) |
| Auto-stop (on by default) | Stops the instance when nobody is using it and emails alerts through SNS | No; independent of domain access | `auto_stop.tf` (SSM parameter, Lambda watchdog, EventBridge rule, SNS topic) |

## What an administrator must provide

Everything to set up before the first deploy (AWS account and permissions, secrets, domain
delegation, the ACM certificate and its validation record, Cloudflare Zero Trust and the API token)
is in [docs/runbook/pre-deployment.md](docs/runbook/pre-deployment.md), in order.

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
4. Wait for the registry to publish the DS record (resolvers may cache the old value for up to 15 minutes), then run the DNSSEC check in [Edge protections test](docs/runbook/smoke-tests/access-and-network.md#edge-protections-test).

**Do not add the key as 256 - ZSK.** The flags are part of the DS calculation, so the registered DS will not match Cloudflare's key. Once the zone is signed, validating resolvers such as 8.8.8.8 and 1.1.1.1 return `SERVFAIL` and the site is unreachable for most visitors. To recover, remove the wrong key at Route 53 and add the correct one.

**Rollback order.** Remove the key at Route 53 first and wait for the DS record to expire, then disable DNSSEC in Cloudflare. Disabling it in Cloudflare first leaves a DS record with no matching key, which causes the same outage. Remove the DS record at the registrar before moving DNS to another provider.

## Origin lockdown

Set `enable_origin_lockdown = true` in `terraform.tfvars` to make the ALB accept HTTPS only from Cloudflare's published IPv4 and IPv6 ranges. Direct requests to the ALB's `*.elb.amazonaws.com` name then time out instead of reaching Open WebUI. The instance's own security group already accepts the application port only from the ALB, and the ALB has no public port 80 by default. The feature needs `enable_cloudflare_access = true` and no paid Cloudflare plan.

The ranges are the `cloudflare_ipv4_cidrs` and `cloudflare_ipv6_cidrs` variables in `variables.tf`, copied from https://www.cloudflare.com/ips-v4 and https://www.cloudflare.com/ips-v6. Override them in `terraform.tfvars` when Cloudflare publishes changes.

**Safe rollout order**

1. Confirm the site works through Cloudflare: `nslookup <domain>` returns Cloudflare addresses, the Access sign-in works, and Open WebUI loads.
2. Run the direct-origin check once and note that it still connects (the "before" result).
3. Set `enable_origin_lockdown = true`, then plan and apply.
4. Re-run the public check and the direct-origin check (see [Origin lockdown test](docs/runbook/smoke-tests/access-and-network.md#origin-lockdown-test)). The public site must still work and the direct request must time out.

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
  [pre-deployment.md, step 6](docs/runbook/pre-deployment.md#6-cloudflare-account-zero-trust-and-api-token), or creating the login method fails with a 403.
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

The control panel (`dashboards/`) is a separate web app where customers start the instances they are granted, and administrators manage who may start what. It lives on its own address (`https://cp.aiwebdemo.click`) and is built and kept by hand, so it does not depend on the lab being deployed. Without a lab it shows no instances. Without users it shows no users. See `SETUP.md` for what exists and `dashboards/api/README.md` for the API.

### What an administrator must provide

The panel's hosting, DNS record and sign-in provider are set up before the first deploy:
[pre-deployment.md, step 8](docs/runbook/pre-deployment.md#8-control-panel-hosting). Its API is
deployed with Terraform (runbook step 2).

### Who owns what

| Piece | Owner |
|---|---|
| Hosting, DNS record, API, Lambdas, tables, IAM roles, the authorizer and its routes (including `POST /instances/{instanceId}/reset-timer`, the timer reset route, and the customer role's `ssm:GetParameter` and `ssm:PutParameter` on the reset parameter) | Built by hand, kept when the lab is destroyed |
| The panel's app client in the lab's Cognito pool | Terraform (`control_panel.tf`) |
| The demo users' rows in `panel_users` (every demo user as `operators`, plus `user_mgrs` for the odd demo users and `admin` for `admin@example.local`), when `control_panel_users_table` is set | Terraform |
| The lab's instance, target group and service address, as SSM parameters, and the `control-panel=managed` tag on the instance | Terraform (`control_panel_lab.tf`) |
| The timer reset parameter `/<project_name>/auto-stop/reset-at` | Terraform creates it with the value `0` and never overwrites the value (`auto_stop.tf`); the panel's reset button writes it, and the instance monitor and watchdog read it |
| The authorizer's issuer and audience | Terraform on apply and destroy, once the lab knows the API's IDs, read from the API stack's `/<project_name>/control-panel-api/settings` parameter or set in `terraform.tfvars` (`control_panel_api.tf`) |
| Real users, their roles and instance grants | The control panel. Terraform never touches them |

Until a provider is wired, the authorizer trusts an empty holding Cognito pool that can never issue a token, so every API route answers 401. Apply points the authorizer at the lab's pool, and destroy points it back at the holding pool. The routes are never recreated. To use another provider, update the authorizer's issuer and audience by hand and upload a `config.js` for it.

### Instance wiring

The panel learns which instance and ALB target group to manage from SSM parameters that Terraform writes under `/<project_name>/control-panel/` (`instance-ids`, `target-group-arn`, `service-url`) when `control_panel_url` is set. The Lambdas read them live, so a new lab is picked up with nothing to copy, and a destroyed lab leaves the panel with no instances. Terraform also tags the instance `control-panel=managed`; the customer role can start only instances with that tag. The Lambdas' roles need read access to that parameter path.

The reset button writes one more parameter, `/<project_name>/auto-stop/reset-at` (epoch seconds of the last timer reset). Terraform creates it with the value `0` and never overwrites its value, and the lab's instance monitor and watchdog read it. The customer Lambda's role needs `ssm:GetParameter` on it and on `/<project_name>/auto-stop`, and `ssm:PutParameter` on `reset-at` only; the new API route is added by hand. Apply Terraform first so the parameter exists before the panel writes it. The exact steps are in "Adding the timer reset route to an existing panel" in `dashboards/api/README.md`.

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

See the checklist in [docs/runbook/pre-deployment.md](docs/runbook/pre-deployment.md). The lab can stay
destroyed while waiting for domain delegation or Cloudflare activation; those prerequisites do not
need an instance or load balancer.
