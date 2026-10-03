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
| AWS Secrets Manager | Stores the Open WebUI passwords and Cloudflare API token | Yes for the current secure bootstrap flow | Secret ARNs in variables; secrets are pre-created outside Terraform |

## What an administrator must provide

### AWS details

The administrator needs:

1. An AWS profile or another AWS credential source with permission to create and destroy the lab.
2. An AWS region. The current example uses `us-east-1`.
3. An existing Secrets Manager secret for the Open WebUI administrator password.
4. An existing Secrets Manager secret for the four demo users' temporary password.
5. An issued ACM certificate covering the public hostname, in the same AWS region as the ALB.

The two Open WebUI password secrets are read by the EC2 instance through its IAM role. Terraform does not create, rotate, update, or destroy those secrets.

Relevant variables in `terraform.tfvars`:

```hcl
aws_region                              = "us-east-1"
aws_profile                             = null
open_webui_admin_password_secret_arn    = "arn:aws:secretsmanager:..."
open_webui_demo_user_password_secret_arn = "arn:aws:secretsmanager:..."
acm_certificate_arn                     = "arn:aws:acm:us-east-1:...:certificate/..."
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
6. The email addresses allowed through the Cloudflare Access application.
7. Cloudflare Zero Trust (Access) enabled for the account before applying the Access resource.

The API token should be limited to this account and zone, with an expiry date. The Terraform resources in this project need permission to read the zone, edit its DNS records, and edit Access applications and policies: **Zone: Zone Read**, **Zone: DNS Edit**, and **Account: Access: Apps and Policies Edit**.

The token itself must not be committed to the repository. In this project it is stored in AWS Secrets Manager and retrieved by the PowerShell Terraform wrapper into the temporary `CLOUDFLARE_API_TOKEN` environment variable.

Relevant variables in `terraform.tfvars`:

```hcl
enable_domain_access     = true
enable_cloudflare_access = true
domain_name               = "aiwebdemo.click"
cloudflare_account_id     = "<32-character Cloudflare account ID>"
cloudflare_api_token_secret_arn = "arn:aws:secretsmanager:..."
cloudflare_access_allowed_emails = ["admin@example.com"]
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
- Restrict the ALB to Cloudflare traffic.
- Configure a Cloudflare Tunnel.

Those actions must be completed before the corresponding Terraform resources can succeed, or by hand afterward.

## Cloudflare security settings (configured manually)

Do these in the Cloudflare dashboard after the zone is **Active**. They are listed in priority order.

1. **Set SSL/TLS encryption mode to Full (strict).** The ACM certificate on the ALB meets Cloudflare's requirements for this mode: it is unexpired, publicly trusted, and matches the hostname, and the ALB accepts HTTPS on port 443. Do not use **Flexible**: Cloudflare would connect to the ALB over HTTP on port 80, which the ALB does not open by default, so visitors get 522 errors (with `enable_alb_http_redirect = true` they would instead get a redirect loop). Automatic SSL/TLS may choose a mode on its own, so set it explicitly.
2. **Enforce HTTPS at the edge.** Turn on **Always Use HTTPS** and **Automatic HTTPS Rewrites**, set the minimum TLS version to 1.2, and keep TLS 1.3 enabled. Add HSTS only after the site works correctly over HTTPS, and start with a short max-age.
3. **Check the Access policy.** Confirm the application covers the whole hostname, that only the intended email addresses are allowed, and that a sign-in from an unlisted address is denied. Review the identity provider settings under Zero Trust > Settings > Authentication.
4. **Lock the origin to Cloudflare.** The ALB's security group currently allows ports 80 and 443 from the whole internet, and Access is enforced only at Cloudflare's edge. Anyone who learns the ALB's DNS name can reach Open WebUI without going through Access. Cloudflare's guidance lists several ways to close this: restrict the ALB security group to [Cloudflare's published IP ranges](https://www.cloudflare.com/ips/), use Authenticated Origin Pulls, or validate the Access JWT at the origin. Restricting the security group to Cloudflare's IP ranges is the simplest. It needs a Terraform change, so track it as a follow-up.
5. **Turn on WAF protections and bot defenses.** Enable the managed WAF rules and Bot Fight Mode if the plan includes them. Consider a rate-limiting rule for the sign-in path. With Access in front, these are a second layer rather than the first.
6. **Publish email protections even though the domain sends no mail.** This stops others from spoofing the domain: a null MX record (`0 .`), an SPF record of `v=spf1 -all`, and a DMARC record such as `v=DMARC1; p=reject`.
7. **Consider DNSSEC.** Enable it in Cloudflare and add the DS record at the registrar, if the registrar supports it for the domain's TLD.
8. **Protect the accounts.** Use two-factor authentication on the Cloudflare account and on the AWS and registrar accounts. Keep the API token scoped, set an expiry, and rotate the Secrets Manager copy when it changes. Keep the registrar's transfer lock on.

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
2. Create the two AWS Secrets Manager secrets.
3. Obtain and validate the ACM certificate in the ALB region.
4. Register the domain.
5. Add the domain to Cloudflare and note the two assigned nameservers.
6. Change the nameservers at the registrar (not in a hosted zone) to the Cloudflare pair.
7. Wait for the Cloudflare zone to become **Active**.
8. Create the ACM validation CNAME in Cloudflare as DNS-only.
9. Enable Cloudflare Zero Trust (Access).
10. Create a scoped Cloudflare API token and store it in Secrets Manager.
11. Copy and edit `terraform.tfvars.example`.
12. Run the smoke test plan from initialization through apply.
13. Set the Cloudflare SSL/TLS mode to Full (strict) and review the other security settings above.
14. Verify the ALB target health and the public HTTPS URL.

The lab can remain destroyed while waiting for domain delegation or Cloudflare activation. Those control-plane prerequisites do not require an EC2 instance or load balancer to be running.
