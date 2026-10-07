# Origin Lockdown Recovery

Use this when `enable_origin_lockdown = true` and the lab stops being reachable, or you
need a way in that does not go through Cloudflare. For how lockdown works and the
rollout order, see "Origin lockdown" in `cloudflare-and-domain-requirements.md`.

Run every Terraform command through the wrapper:
`Invoke-TerraformWithCloudflareToken -Arguments @(...)`.

Changing the lockdown settings updates the ALB security group in place. It does not
replace the instance or the ALB.

## What lockdown does, and what it does not

With lockdown on, the ALB accepts HTTPS (port 443) only from Cloudflare's published IP
ranges plus `origin_lockdown_extra_cidrs`. Everything else times out.

It does **not** affect the EC2 instance's own access paths. SSM Session Manager and SSM
port forwarding go through AWS, not through the ALB, so you can always reach the
instance and Open WebUI that way (scenario 1). Keep this in mind before you reach for
the last-resort option.

## Quick diagnosis

Run from your own network, which is not in the allowed ranges:

```powershell
$env:alb_dns_name = terraform output -raw open_webui_alb_dns_name
curl.exe -k -I --max-time 10 -H "Host: aiwebdemo.click" "https://$env:alb_dns_name"
```

| Result | Meaning |
| --- | --- |
| Timeout | Lockdown is working. This is the expected result from your own network. |
| `HTTP/1.1 200 OK` | Lockdown is not applied, or your IP is in `origin_lockdown_extra_cidrs`. |

Then check the public site in a browser at your domain:

| Public site | Likely cause |
| --- | --- |
| Loads and shows the Cloudflare Access login | Healthy. |
| 520, 521, 522 or 524 from Cloudflare | Cloudflare cannot reach the ALB. Go to scenario 2. |
| Cloudflare Access error or login loop | Not a lockdown problem. See "Cloudflare Access test" in `terraform-smoke-test-plan.md`. |

## Scenario 1: Get into Open WebUI right now, without touching Terraform

Use SSM port forwarding. It does not use the ALB, Cloudflare, Access or lockdown.

```powershell
$env:instance_id = terraform output -raw instance_id
terraform output -raw ssm_open_webui_port_forward_command
```

Run the command that the second line prints, leave it running, and open the local URL it
reports (normally `http://localhost:8080`). To get a shell on the instance instead:

```powershell
aws ssm start-session --target $env:instance_id
```

## Scenario 2: The public site returns 52x after enabling lockdown

Most likely the Cloudflare IP ranges in `variables.tf` are out of date, or lockdown was
turned on before the site worked through Cloudflare.

1. Compare the repo's ranges with Cloudflare's current lists:

   ```powershell
   curl.exe -s https://www.cloudflare.com/ips-v4
   curl.exe -s https://www.cloudflare.com/ips-v6
   ```

   Compare them with `cloudflare_ipv4_cidrs` and `cloudflare_ipv6_cidrs` in
   `variables.tf`.

2. If they differ, override the variables in `terraform.tfvars` with the current lists,
   for example:

   ```hcl
   cloudflare_ipv4_cidrs = [
     "173.245.48.0/20",
     # ...the full current list...
   ]
   ```

3. Apply:

   ```powershell
   Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-out=ai-lab.tfplan")
   Invoke-TerraformWithCloudflareToken -Arguments @("apply", "ai-lab.tfplan")
   Remove-Item ai-lab.tfplan
   ```

4. Reload the public site. If it still fails, use scenario 1 to confirm Open WebUI itself
   is healthy, then check "Domain-access test" in `terraform-smoke-test-plan.md`.

## Scenario 3: You need to reach the ALB directly for a short time

Add your public address as a `/32`. Do this only for a short debugging window.

```powershell
$myip = (curl.exe -s https://checkip.amazonaws.com).Trim()
"origin_lockdown_extra_cidrs = [""$myip/32""]"
```

Put that line in `terraform.tfvars`, then plan and apply as in scenario 2. The direct
check in "Quick diagnosis" will now return `200 OK` from your network.

When you are done, set `origin_lockdown_extra_cidrs = []` (or delete the line), apply, and
re-run the direct check. It must time out again.

## Scenario 4: Last resort, turn lockdown off

This reopens the ALB to the whole internet, so anyone who learns the ALB name can reach
the Open WebUI login page without Cloudflare Access or the WAF. Use it only if scenarios
1 to 3 do not fix the problem, and turn lockdown back on as soon as you can.

1. In `terraform.tfvars`, set `enable_origin_lockdown = false` (or comment the line out).
2. Plan and apply as in scenario 2.
3. Fix the underlying problem, set `enable_origin_lockdown = true` again, and apply.
4. Re-run the direct check from "Quick diagnosis". It must time out.

## After any recovery

- `origin_lockdown_extra_cidrs` is empty unless you are mid-debugging.
- The direct HTTPS check times out from your network.
- The public site loads through Cloudflare and shows the Access login.
- `Remove-Item ai-lab.tfplan` has been run.
