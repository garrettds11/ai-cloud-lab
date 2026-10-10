# Smoke tests: access and network

Tests for how people reach the lab and what the network allows. Run the ones the change touched;
[the deploy runbook](../../terraform-smoke-test-plan.md#5-test-what-changed) says which.

**Before you start:** use the PowerShell window from the runbook, after its step 1 (profile,
wrapper, `Get-TfVar`, `$projectName`, `$domainName`) and step 3 (`$env:instance_id`). Commands are
**Local Windows PowerShell** unless marked **Linux (SSM shell on the lab instance)**. Open that
shell with `aws ssm start-session --target $env:instance_id`.

## Accounts and passwords

Initial login credentials are as follows unless you changed `open_webui_admin_email`:

```
admin@example.local
```

- Password is set in the pre-created Secrets Manager secret identified by the `open_webui_admin_password_secret_arn` output.
- Display name: `Lab Admin` unless you changed `open_webui_admin_name`.

The demo users are numbered 1 through 10, like:

```
demo1@example.local
```

They all use the value stored in the Secrets Manager secret identified by
`open_webui_demo_user_password_secret_arn`.

The Secrets Manager secret must already exist and contain the desired admin
password. The value may be plain text or a one-key key/value secret; bootstrap
extracts the single string value from either format. Terraform only grants the
instance role read access and retrieves the password during bootstrap. Terraform
does not create, update, or destroy this secret. To rotate an existing lab,
change the password in Open WebUI first, then update the matching value in the
AWS console.

With `enable_cognito = true`, Terraform creates the Cognito users without passwords. After each
`apply` that creates them, set them from the runbook's window:

```powershell
powershell.exe -ExecutionPolicy Bypass -File ".\scripts\set-cognito-passwords.ps1"
```

Retrieve the two initial passwords from Secrets Manager when you need them for
login verification. These commands print the values to the current terminal;
do not paste the output into tickets, source files, or logs:

```powershell
$adminPasswordSecretArn = "arn:aws:secretsmanager:us-east-1:394566733278:secret:openwebui-admin-pass-DuXz9K"
$demoPasswordSecretArn = "arn:aws:secretsmanager:us-east-1:394566733278:secret:open_webui_demo_user_password-2eFYcl"

aws secretsmanager get-secret-value `
  --secret-id $adminPasswordSecretArn `
  --query SecretString `
  --output text `
  --region $env:AWS_DEFAULT_REGION `
  --profile $env:AWS_PROFILE

aws secretsmanager get-secret-value `
  --secret-id $demoPasswordSecretArn `
  --query SecretString `
  --output text `
  --region $env:AWS_DEFAULT_REGION `
  --profile $env:AWS_PROFILE
```

The first value is used for `admin@example.local`, both its local Open WebUI
account and its Cognito user; the second is the password shared by the demo
users. With `enable_cognito = false`, each demo user should change it from
Profile after first login.

Where they sign in depends on `enable_cognito`:

- **`true` (default):** the admin is a local Open WebUI account and a Cognito
  user. The demo users are Cognito users only, with no local account. Sign in
  with **Continue with Cognito**; run `.\scripts\set-cognito-passwords.ps1` after
  `apply` first (see "Cognito sign-in test").
- **`false`:** the demo users are local Open WebUI accounts that sign in with
  the password form. Have each user open Profile and change that temporary
  password before using the account.

To turn local password sign-in off, set `open_webui_enable_local_login = false`
(see "Where each account lives" in the README).

==*Change the temporary password immediately*== after confirming access.

## Domain-access test

When `enable_domain_access` is `true`, do not start an *SSM port-forwarding*
session. The SSM shell is still used for readiness and troubleshooting;
the ALB is the public entry point for browser access.

- URL loads Open WebUI: https://aiwebdemo.click

> If `enable_cloudflare_access = false` this check returns a `500` internal error,
> otherwise it should show a branded Cognito login page.

The SSM shell is a Linux shell. Run Linux commands there; run PowerShell commands such as `curl.exe` from a separate Windows PowerShell window.

**The project’s EC2 host runs `Ubuntu Server 24.04 LTS` (amd64)** and [main.tf](https://github.com/garrettds11/ai-cloud-lab/blob/dev/main.tf) selects its AMI through AWS’s Canonical SSM parameter:

```text
/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
```

`Open WebUI` runs **inside a Docker container on that Ubuntu host**. The container has its own userspace, which can differ from Ubuntu, while sharing the host’s Linux kernel.

From your SSM shell, check the host OS with:

```bash
cat /etc/os-release
```

To check the Open WebUI container’s OS:

```bash
sudo docker ps --format '{{.Names}}' |
while read -r container_name; do
    printf '\nContainer: %s\n' "$container_name"
    sudo docker exec "$container_name" cat /etc/os-release
done
```

Ollama runs directly on the Ubuntu host as a systemd service. The model weights are stored on the host’s disk and loaded by Ollama for inference.

```bash
systemctl status ollama
ollama list
```

## Cloudflare Access test

When `enable_cloudflare_access` is `true`, Cloudflare Access sits in
front of Open WebUI and checks every visitor before the Open WebUI login page
loads. The login method depends on the other settings:

- **`enable_cognito = true` (this repo's default):** Cognito is the only login
  method on the Access app, so Access sends you straight to the Cognito sign-in
  page. There is no Access method-choice page and no one-time PIN.
- **`enable_cognito = false`:** Access shows the login methods you configured in
  Zero Trust. New accounts offer only the **Cloudflare** identity provider, which
  is limited to members of your Cloudflare account. Add One-time PIN under
  **Zero Trust > Integrations > Identity providers** if other people need to sign
  in.

In both cases the email must be in the Access policy: with Cognito, that is every
Cognito user's email plus `cloudflare_access_allowed_emails`; without Cognito, it
is `cloudflare_access_allowed_emails` only.

**Run these from a browser:**

1. In a private window, open https://aiwebdemo.click. You must land on a
   sign-in page (Cognito, or the Access page when Cognito is off), not the Open
   WebUI login page.
2. Sign in with a user that is **not** in the Access policy, if you have one.
   Access must deny it.
3. Sign in with an approved user. You must reach the Open WebUI login page.
   With Cognito, choose **Continue with Cognito**; it reuses the Cognito
   session, so there is no second password prompt.
4. In Open WebUI, confirm the security and acceptable use banner shows at the
   top (see `security_banner_text`), send a chat message, and confirm the reply
   streams in (this exercises API calls and websockets through Cloudflare).
5. Confirm the apex record is proxied: `nslookup aiwebdemo.click` must return
   Cloudflare addresses, not the ALB address.
6. In the Cloudflare dashboard, confirm SSL/TLS is **Full (strict)** and that
   Security > WAF shows the Free Managed Ruleset enabled.

This should return a `302` to Cognito or `cloudflareaccess.com` rather than an
Open WebUI page.

```powershell
curl.exe -I https://aiwebdemo.click
```

## Cognito sign-in test

Skip this section unless `enable_cognito` is `true` in `terraform.tfvars`.
Terraform creates the Cognito users without passwords. After `apply` completes,
set them with `.\scripts\set-cognito-passwords.ps1` (see
[Accounts and passwords](#accounts-and-passwords)).

Then, in a private window:

1. Open https://aiwebdemo.click. Access must send you straight to the Cognito
   sign-in page (no login-method choice).
2. Sign in with `demo1@example.local` and the demo password. You must reach Open
   WebUI.
3. If Open WebUI shows its own login page, choose **Continue with Cognito**. It
   must sign you in without asking for a password. The demo user has no local
   account, so Open WebUI creates it now with the `open_webui_default_user_role`
   role (default `user`, so there is no activation wait).
4. Sign out of Open WebUI. You must land on the Cognito sign-in page (or the
   site's sign-in), never on a Cognito "Client does not exist" error, and
   opening https://aiwebdemo.click again must ask for credentials. Then repeat
   with `admin@example.local` and the administrator
   password. Open WebUI must open the existing local admin account, and **Admin
   Panel** must be available.
5. Sign in as a user who is not in the Cognito pool. Access must deny it.

## SSM-only test

For an SSM-only run, turn domain access off in this run's settings before planning: open the copy
with `code terraform.tfvars`, set `enable_domain_access = false`, save, then plan and apply as in
the runbook. (A `TF_VAR_enable_domain_access` environment variable does not work here: the value in
`terraform.tfvars` wins over it.)

Then run this in a second PowerShell terminal:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --document-name AWS-StartPortForwardingSession `
  --parameters portNumber="8080",localPortNumber="8080"
```

Open: http://localhost:8080

If local port 8080 is already in use, keep the remote port at 8080 and use a
different local port:

```powershell
aws ssm start-session `
  --target $env:instance_id `
  --document-name AWS-StartPortForwardingSession `
  --parameters "portNumber=8080,localPortNumber=8081"
```

Then open http://localhost:8081.

## Origin lockdown test

Skip this section unless `enable_origin_lockdown = true`. Before enabling it,
confirm the public site works, and run the direct check below once to see the
"before" result: it connects and shows the Open WebUI login page.

After applying with lockdown on, from a network that is not in
`origin_lockdown_extra_cidrs`:

```powershell
$env:alb_dns_name = terraform output -raw open_webui_alb_dns_name
curl.exe -k -I --max-time 6 -H "Host: aiwebdemo.click" "https://$env:alb_dns_name"
```

The request must time out or fail to connect. It must not return a response from
Open WebUI. Then confirm the public path still works:

```powershell
curl.exe -I https://aiwebdemo.click
```

This should still return a `302` from Cloudflare Access.

## Outbound restriction test

The instance may make outbound connections only on TCP 443 and 80. In an SSM shell
on the instance (`aws ssm start-session --target $env:instance_id`):

```bash
curl -sS -o /dev/null -w "%{http_code}\n" --max-time 10 https://ollama.com
curl -sS --max-time 8 http://portquiz.net:8080 || echo "blocked"
```

The first command must print an HTTP status. The second must time out and print
`blocked`. If bootstrap itself failed on a download, check the bootstrap log for the
host it could not reach and add that destination to `extra_egress_cidrs`.

## Edge protections test

1. **Bot Fight Mode:** with it on, sign in through Cloudflare Access, send a chat message, and confirm the reply streams in. If any step fails, turn Bot Fight Mode off and re-test.
2. **DNSSEC:** run these in PowerShell. Google's resolver validates DNSSEC, so a bad key shows up as a failure.

```powershell
Clear-DnsClientCache
Resolve-DnsName <domain> -Type DS -Server 8.8.8.8
Resolve-DnsName <domain> -Type A -Server 8.8.8.8
```

The DS record's key tag must equal the one in the Route 53 DNSSEC keys table, and the A lookup must not return `DNS server failure`. In a browser, `https://dns.google/resolve?name=<domain>&type=A` should show `"AD": true`.

## Load balancer hardening test

Needs `enable_domain_access = true`. `$projectName` comes from the runbook's step 1.

```powershell
$alb = aws elbv2 describe-load-balancers --names "$projectName-alb" --query "LoadBalancers[0]" | ConvertFrom-Json
aws elbv2 describe-listeners --load-balancer-arn $alb.LoadBalancerArn --query "Listeners[?Port==``443``].SslPolicy" --output text
aws elbv2 describe-load-balancer-attributes --load-balancer-arn $alb.LoadBalancerArn --query "Attributes[?Key=='routing.http.drop_invalid_header_fields.enabled'].Value" --output text
aws ec2 describe-security-groups --group-ids $alb.SecurityGroups --query "SecurityGroups[].IpPermissionsEgress"
```

Expected, in order:
1. `ELBSecurityPolicy-TLS13-1-2-2021-06`.
2. `true`.
3. One egress rule: TCP on the Open WebUI port (8080) to the VPC's ranges only.

With origin lockdown and the port 80 redirect both on, the port 80 rule allows only Cloudflare ranges. The site still loads through Cloudflare after all of this; run the Cloudflare Access test's chat step again.

## Lab unavailable page test

Needs `control_panel_url` set and the Cloudflare token to have **Account: Workers Scripts Edit**
and **Zone: Workers Routes Edit** (a 403 on the Worker resources during apply means it does not).

1. With the instance stopped (`aws ec2 stop-instances --instance-ids $env:instance_id`), open the
   lab address in a browser, for example `https://<domain_name>`, after signing in through Access.
   Expected: a redirect to `https://<control_panel_url>/`, not a Cloudflare or load balancer error.
2. The control panel opens. Start the lab from there.
3. When the lab is healthy again, the same address loads Open WebUI normally.
4. Calls that are not page loads are untouched: in a shell,
   `curl.exe -s -o NUL -w "%{http_code}" https://<domain_name>/health` while the lab is
   stopped should still return a plain error code (no HTML), because curl does not ask for
   HTML.
