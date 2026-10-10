# Smoke tests: control panel

Tests for the control panel, its API stack and the auto-stop timer. Run the ones the change
touched; [the deploy runbook](../terraform-smoke-test-plan.md#step-5-test-what-changed) says which.

**Before you start:** use the PowerShell window from the runbook, after its step 1 (profile,
wrapper, `Get-TfVar`, `$projectName`, `$domainName`, `$panelBucket`) and step 3
(`$env:instance_id`). Commands are **Local Windows PowerShell** unless marked
**Linux (SSM shell on the lab instance)**; the SSM shell is a plain `sh` shell: paste **one command at a time**. Pasting several lines at once interleaves them (errors such as `er: not found`).
Commands that read the API stack's outputs use `terraform -chdir=dashboards/api/terraform`, so they
also run from the repository root. Load the API stack's settings once:

```powershell
$panelApi          = aws ssm get-parameter --name "/$projectName/control-panel-api/settings" --query Parameter.Value --output text | ConvertFrom-Json
$panelApiId        = $panelApi.api_id
$panelAuthorizerId = $panelApi.authorizer_id
```

## Control panel config test

*Verify that the control panel is connected to the Cognito pool and API for the deployment.*

Skip this section unless `enable_cognito` is `true` and `control_panel_url` is set
in `terraform.tfvars`. Terraform creates the control panel's Cognito app client
and publishes the panel's `config.js` into its bucket during `apply`, then clears
that file from CloudFront's cache. This needs `control_panel_bucket`, and
`control_panel_distribution_id` for the cache. There is nothing to write or upload
by hand.

Check:

1. `aws s3 cp "s3://$panelBucket/config.js" -` prints a
   file that starts with `// Written by Terraform` and holds the region, the user
   pool ID, an app client ID, the API address and the `redirectUri`
   `https://cp.aiwebdemo.click/`. A `null` for `apiUrl` means the lab found no API
   settings: the plan showed the `control_panel_api_settings_found` warning (see the runbook's
   step 3).
2. Open `https://cp.aiwebdemo.click`. The page must show a sign-in button, not a
   message about a missing `clientId` or a missing settings file. If it shows an old
   message, the cache clearing did not run: check the `apply` output for the
   `terraform_data.control_panel_config_cache` step.
3. In the Cognito console, the user pool has a second app client named
   `<project_name>-control-panel` with no client secret.
4. With the API's settings found, the users table (`$panelApi.users_table`) has a row for
   each of the 11 demo accounts: `demo1@example.local`, `demo3@example.local` and the
   other odd demo users with `operators` and `user_mgrs` in `roles`;
   `demo2@example.local` and the other even demo users with `operators`;
   `admin@example.local` with `operators` and `admin`. Only an administrator can change
   roles, and any administrator can make another.
   The grants table (`$panelApi.entitlements_table`) has one row per
   demo user (all 11, because all have `operators`) for the lab instance, with
   `status` `applied` and `grantedBy` `terraform`. Signed in as `demo1@example.local`
   (or any demo user), the Instances page lists the lab instance. Signed in as an
   administrator, it lists every managed instance with or without a grant.

5. With the API's settings found, `apply` pointed the control panel API's authorizer at
   this lab's pool. Check:

   ```powershell
   aws apigatewayv2 get-authorizer --api-id $panelApiId --authorizer-id $panelAuthorizerId --query JwtConfiguration
   ```

   The issuer must end with the lab's `cognito_user_pool_id` and the audience must be the
   control panel app client ID. After `destroy`, the same command must show the holding
   pool's issuer and the audience `holding-unused`.

6. The lab publishes its wiring for the panel. With `control_panel_url` set, `apply`
   creates its SSM parameters and tags the lab instance `control-panel=managed`.
   Check:

   ```powershell
   aws ssm get-parameters-by-path --path "/$projectName/control-panel" --query "Parameters[].[Name,Value]" --output table
   aws ec2 describe-instances --instance-ids $env:instance_id --query "Reservations[].Instances[].Tags[?Key=='control-panel']"
   ```

   The parameters are `instance-ids` (the lab instance ID), `open-webui-image` (the
   `open_webui_container_image` value), `target-group-arn` and `service-url`
   (`https://$domainName`). `target-group-arn` and `service-url` exist only when
   `enable_domain_access` is also `true`. The tag value is `managed`.

7. Sign in at https://cp.aiwebdemo.click as a user in the lab's Cognito pool. The
   panel must list the lab instance with no setting copied into the Lambda functions.
   Only a sign-in whose email is in `BOOTSTRAP_ADMINS` (`garrettds11@gmail.com`) can
   open the admin screens; other users get only the customer view.

   After `destroy`, the parameters are gone and the panel lists no instances. The
   `config.js` object is deleted too, so the page reports that it has no settings file.

## Control panel API stack test

After the runbook's step 2 and a lab apply. From the repository root.

1. **Panel end to end:** sign in at https://cp.aiwebdemo.click. Expected:
   - The instances are listed.
   - Start, Reset and Access behave as before.
   - As an administrator, User management, Changes, Logins and Logs all open.

   All of these go through the API whose address is in `config.js` (check with
   `aws s3 cp "s3://$panelBucket/config.js" -`).
2. **Routes match the spec:**

   ```powershell
   aws apigatewayv2 get-routes --api-id (terraform -chdir=dashboards/api/terraform output -raw api_id) --query "sort(Items[].RouteKey)" --output text
   ```

   It must list the same 15 routes as `dashboards\api\openapi.yaml`.
3. **Access log:** after using the panel for a minute,

   ```powershell
   aws logs tail "/aws/apigateway/aiwebdemo-control" --since 10m
   ```

   Each line has the route, the status and the caller's email. No line may contain `Bearer`, `eyJ` (the start of a token) or a request body.
4. **Tables kept their data:** after the API stack was rebuilt, User management shows the same
   people, roles and grants as before.
5. **The tables are protected:**

   ```powershell
   foreach ($t in 'panel_users', 'instance_entitlements', 'control_panel_events') {
     aws dynamodb describe-table --table-name $t --query "Table.DeletionProtectionEnabled"
   }
   ```

   All three print `true`.
6. **Before a lab exists, the API refuses everyone.** Right after a first build or rebuild, before
   the lab apply:

   ```powershell
   aws apigatewayv2 get-routes --api-id (terraform -chdir=dashboards/api/terraform output -raw api_id) --query "length(Items)"
   curl.exe -s -o NUL -w "%{http_code}\n" "$((terraform -chdir=dashboards/api/terraform output -raw api_url).TrimEnd('/'))/instances"
   ```

   The first prints `15`. The second prints `401`: the authorizer trusts only the empty holding
   pool until the lab apply points it at the lab's Cognito pool.

## Open WebUI admin action test

**Pending manual execution.** Needs the lab running, `READY`, and the API stack applied. The panel pages have no button for this yet (phase 1, #59), so the test calls the document and the API directly.

1. **The document alone**, from the repository root:

   ```powershell
   $doc = terraform -chdir=dashboards/api/terraform output -raw webui_admin_document
   $cmd = aws ssm send-command --document-name $doc --instance-ids $env:instance_id --parameters "action=status,expectedVersion=0.11.4" --query Command.CommandId --output text
   Start-Sleep 15
   aws ssm get-command-invocation --command-id $cmd --instance-id $env:instance_id --query "[Status, StandardOutputContent]" --output text
   ```

   Expected: `Success` and one JSON line with `"ok":true`, `"healthy":true`, `"version":"0.11.4"`, `"versionMatches":true`. If `version` is `null` while `healthy` is `true`, Open WebUI v0.11.4 does not serve `/api/version` without sign-in; record that in #59.

2. **The document refuses anything else:**

   ```powershell
   aws ssm send-command --document-name $doc --instance-ids $env:instance_id --parameters "action=whoami"
   aws ssm send-command --document-name $doc --instance-ids $env:instance_id --parameters "action=status,expectedVersion=1;id"
   ```

   Both must fail at once with an `InvalidParameters` error. Nothing runs on the instance.

3. **Through the API, as an administrator.** This step runs in the **browser**, not PowerShell:
   sign in to the panel as an administrator, press F12, open the **Console** tab, and paste this
   JavaScript there:

   ```javascript
   const t = JSON.parse(sessionStorage.getItem('panel.tokens')).idToken;
   const api = (window.PANEL_CONFIG?.apiUrl || prompt('API address (terraform output api_url)')).replace(/\/+$/, '');
   const id = 'i-...'; // the lab instance ID
   const r = await fetch(api + '/admin/webui/actions', {method: 'POST', headers: {authorization: 'Bearer ' + t, 'content-type': 'application/json'}, body: JSON.stringify({action: 'status', instanceId: id})});
   const started = await r.json(); console.log(r.status, started);
   setTimeout(async () => console.log(await (await fetch(api + '/admin/webui/actions/' + started.commandId, {headers: {authorization: 'Bearer ' + t}})).json()), 20000);
   ```

   Expected:
   - The first log is `200` with a `commandId` and `status: "Pending"`.
   - Twenty seconds later the second shows `status: "Success"` and the same `result` as step 1.
   - **Logs** in the panel shows `Requested Open WebUI action 'status' on ...`, then `... done`.

   If `window.PANEL_CONFIG` is undefined, paste the `api_url` output when asked.

4. **Not for operators:** repeat step 3 signed in as an operator (for example `demo2@example.local`). The first request must return `403`.

## Auto-stop timer reset test

**All steps below are pending manual execution; nothing here has been run against a live lab.** Offline tests cover the logic with mocked AWS (`tests/test_idle_check_timer_reset.py`, `tests/test_auto_stop_watchdog.py`, `dashboards/api/tests/test_handler.py`); they do not prove the live SSM, IAM or API Gateway wiring this section checks.

Apply the lab with a short hard limit, for example `auto_stop_max_uptime_minutes = 20` (the minimum is 15) and `auto_stop_idle_minutes = 0`.

Browser (control panel, signed in as an operator with a grant on the running lab):

1. A reset icon (circular arrow around a clock) sits between Play and Access. Hovering shows `Reset auto-stop timer`.
2. Press it. A dialog asks `Extend this lab for another full session?` and names the new stop time. **Cancel** changes nothing (the `Stops at about` line does not move).
3. Press it again and choose **Reset timer**. A toast says `Auto-stop timer reset. The lab has another 20 minutes.` Under the green Ready indicator the status shows a new `Stops at about ...` time 20 minutes ahead and a green `Timer reset at ...` line. A page refresh keeps both.
4. Open **Logs**. There is one new line from the control panel: `Reset the auto-stop timer for <name>: another 20 minutes, stopping at about <HH:MM> UTC`. As an administrator, **Logs** shows the same line with the operator's name.
5. The button is greyed out when the lab is stopped, and for a lab with no hard limit. A user with the operator role but no grant for the lab sees no lab and no button.

Local Windows PowerShell, from the repository root:

```powershell
$param = (terraform output -json auto_stop_reset_parameter | ConvertFrom-Json).name
aws ssm get-parameter --name $param --query Parameter.Value --output text
[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
```

The first number is the reset time and should be within a minute of the second just after pressing the button. Then run the watchdog once and read its result:

```powershell
$fn = terraform output -raw auto_stop_watchdog
aws lambda invoke --function-name $fn watchdog-out.json | Out-Null
Get-Content watchdog-out.json
```

Expected: `timer_reset_at` is present and `hard_limit_elapsed_minutes` is small (minutes since the reset, not since boot).

Linux (SSM shell on the lab instance):

```bash
sudo AI_LAB_DRY_RUN=1 /usr/local/sbin/ai-lab-idle-check
```

Expected: `hard_limit_minutes=N/20` where N is minutes since the reset, while `uptime_minutes` is larger.

Then, with the lab left running past its original 20 minute deadline and not reset again:

- It is still running at the original deadline, and the stop warning email (about 10 minutes before a short limit) arrives relative to the new deadline, not the old one.
- It stops about 20 minutes after the reset (the instance monitor within a minute, or the watchdog within about 5 more minutes).
- Start it again from the panel: no `Timer reset at` line, and `ai-lab-idle-check` counts `hard_limit_minutes` from the new boot (the earlier reset is ignored).

Terraform drift check, after a reset: `terraform plan` shows no change to `aws_ssm_parameter.auto_stop_reset`.

**A bad reset value cannot switch the limit off** (#56). With the lab running and past its hard limit's halfway point, write a far-future value by hand, as a mistake or a misused permission would:

```powershell
aws ssm put-parameter --name $param --value 9999999999 --overwrite
```

Expected:
- `sudo AI_LAB_DRY_RUN=1 /usr/local/sbin/ai-lab-idle-check` on the instance shows `hard_limit_minutes` counted from boot (equal to `uptime_minutes`), and the system log has `ignoring an invalid or future timer reset value`.
- Running the watchdog as above returns no `timer_reset_at`, and `hard_limit_elapsed_minutes` counts from launch.
- The panel shows no `Timer reset at` line.
- The lab stops on the original schedule.

Repeat with `99999999999999999999` and with `abc`; the result must be the same, and the watchdog must not error. Afterwards put a sane value back, or press Reset in the panel:

```powershell
aws ssm put-parameter --name $param --value 0 --overwrite
```
