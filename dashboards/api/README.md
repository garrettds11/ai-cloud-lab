# Control API

Two Lambda functions behind the control panel, sharing `handler.py` (Python 3.12, no packages beyond what Lambda already includes). They sit behind one API Gateway HTTP API with a JWT authorizer. The page calls only these routes. It never reads DynamoDB or calls AWS itself.

| Function | Entry point | Routes | Can do |
|---|---|---|---|
| Customer | `customer.lambda_handler` | everything except `/admin/*` | Read roles and grants, start an instance, write the caller's own sign-in and log rows, create a user row with no roles |
| Admin | `admin.lambda_handler` | `/admin/*` | Write roles and grants and the change history. It has no route and no IAM permission to start an instance |

Each answers only its own routes (the other's return 404), and each has its own IAM role, so the split holds even if the code is wrong. API Gateway sends `/admin/*` to the admin function and everything else to the customer function. It is built by hand, outside Terraform (see `../SETUP.md`).

## Rules they enforce

- Every route needs a signed-in user, identified by their verified email. Roles are read from the `panel_users` table on every call and the roles in the token are ignored, so removing a role takes effect at once. If the table cannot be read, the call fails closed (503).
- A customer sees and starts only the instances they hold an active grant for, and only with the `operators` role. Nobody can stop an instance through this API; there is no stop route.
- Admin routes need `user_mgrs`. Saving changes writes `panel_users` roles and `instance_entitlements` only. It never changes IAM, tags or instances.
- Addresses in `BOOTSTRAP_ADMINS` always have `user_mgrs`, so the panel can be opened with an empty table. They cannot be demoted.
- An admin cannot remove their own `user_mgrs` role. Removing someone from `operators` revokes all their grants. A user who is not an operator cannot be granted an instance.
- Everything is validated before anything is written. Errors never include internal detail.

## Routes

All routes need `Authorization: Bearer <ID token>`. Times are milliseconds since the epoch.

| Route | Who | Returns |
|---|---|---|
| `POST /session` | anyone signed in | Records this sign-in once, returns `{userId, name, email, roles, signedInAt}` |
| `GET /instances` | anyone | The caller's granted instances (empty unless an operator) |
| `POST /instances/{instanceId}/start` | operator with a grant | `{ok: true}`. 403 without a grant, 404 unknown, 409 not stopped, 503 no capacity |
| `GET /logins` | anyone | The caller's last 10 panel sign-ins |
| `GET /logs?source=&severity=&windowMinutes=` | anyone | The caller's own log lines, plus EC2 start/stop activity for instances they hold |
| `GET /admin/users` | `user_mgrs` | Every user in `panel_users` with `roles` |
| `GET /admin/instances` | `user_mgrs` | The managed instances |
| `GET /admin/users/{userId}/grants` | `user_mgrs` | Instance IDs the user holds |
| `GET /admin/changes` | `user_mgrs` | The last 12 access changes |
| `PUT /admin/users/{userId}` | `user_mgrs` | Body `{roles:[{role,member}], grants:[{instanceId,grant}]}`; returns `{applied}` |

An instance in `GET /instances` has `phase` (`stopped`, `pending`, `initializing`, `ready`, `stopping`), `checks` (`ec2`, `http`), `launchedAt`, the shared auto-stop `rule`, and `autoStop` while running. The Access button turns on at `ready`, which needs both EC2 status checks `ok` and the ALB target `healthy`.

## What it reads

| Fact | Source |
|---|---|
| State, type, name, launch time | EC2 `DescribeInstances` (the `Name` tag) |
| EC2 status checks | EC2 `DescribeInstanceStatus` |
| HTTP health | ALB target health of `TARGET_GROUP_ARN` (Terraform output `open_webui_target_group_arn`). Without it, ready means the status checks passed |
| Auto-stop rule | SSM parameter `AUTO_STOP_PARAMETER` (`/<project_name>/auto-stop`) |
| Idle time, active users, heartbeat | CloudWatch namespace `AILab`, dimension `InstanceId` |
| EC2 start/stop activity | CloudTrail `LookupEvents`, no user names shown |
| Panel sign-ins and log lines | The events table below |

Sign-ins are recorded when the page loads after sign-in. Failed sign-ins are not visible to the panel.

## Tables

Create all three in DynamoDB (on-demand billing).

| Table | Keys | Notes |
|---|---|---|
| `panel_users` | `email` (string, partition) | One row per person: `name`, `sub`, `roles` (list of `operators` / `user_mgrs`), `source`, `firstSeenAt`, `lastSeenAt`. The customer function creates a row with no `roles` attribute on first sign-in; only the admin function writes `roles` |
| `instance_entitlements` | `userId` (string, partition), `instanceId` (string, sort) | Schema in the PRD, FR-4. Rows are never deleted: revoking sets `status` to `revoked` |
| `control_panel_events` | `pk` (string, partition), `sk` (string, sort) | Turn on TTL for the attribute `expiresAt`. Holds `user#<id>` sign-ins and log lines, and the `changes` history |

## Settings (Lambda environment variables)

| Variable | Value | Function |
|---|---|---|
| `BOOTSTRAP_ADMINS` | Comma-separated emails that always have `user_mgrs`, for example `garrettds11@gmail.com` | both |
| `USERS_TABLE`, `ENTITLEMENTS_TABLE`, `EVENTS_TABLE` | Table names (defaults `panel_users`, `instance_entitlements`, `control_panel_events`) | both |
| `INSTANCE_IDS` | Comma-separated instance IDs the panel manages | both |
| `TARGET_GROUP_ARN` | ALB target group (Terraform output `open_webui_target_group_arn`) | customer |
| `AUTO_STOP_PARAMETER` | `/<project_name>/auto-stop` | customer |
| `SERVICE_URL` | Where the Access button goes, for example `https://aiwebdemo.click` | customer |
| `EVENT_TTL_DAYS` | Optional, default 90 | both |

## Permissions for the two roles

Replace the placeholders. Add the usual `AWSLambdaBasicExecutionRole` to each for logs.

**Customer role**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": "ec2:StartInstances",
      "Resource": "arn:aws:ec2:us-east-1:394566733278:instance/<instance id>" },
    { "Effect": "Allow",
      "Action": ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "elasticloadbalancing:DescribeTargetHealth",
                 "cloudwatch:GetMetricStatistics", "cloudtrail:LookupEvents"],
      "Resource": "*" },
    { "Effect": "Allow", "Action": "ssm:GetParameter",
      "Resource": "arn:aws:ssm:us-east-1:394566733278:parameter/<project_name>/auto-stop" },
    { "Effect": "Allow", "Action": "dynamodb:GetItem",
      "Resource": "arn:aws:dynamodb:us-east-1:394566733278:table/panel_users" },
    { "Effect": "Allow", "Action": ["dynamodb:PutItem", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:394566733278:table/panel_users",
      "Condition": { "ForAllValues:StringEquals": { "dynamodb:Attributes":
        ["email", "name", "sub", "source", "firstSeenAt", "lastSeenAt"] } } },
    { "Effect": "Allow", "Action": "dynamodb:Query",
      "Resource": "arn:aws:dynamodb:us-east-1:394566733278:table/instance_entitlements" },
    { "Effect": "Allow", "Action": ["dynamodb:Query", "dynamodb:PutItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:394566733278:table/control_panel_events" }
  ]
}
```

The attribute condition means this role can never write `roles` in `panel_users`, and it cannot write grants at all.

**Admin role**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow", "Action": ["ec2:DescribeInstances"], "Resource": "*" },
    { "Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:Scan", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:394566733278:table/panel_users" },
    { "Effect": "Allow", "Action": ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:394566733278:table/instance_entitlements" },
    { "Effect": "Allow", "Action": ["dynamodb:Query", "dynamodb:PutItem"],
      "Resource": "arn:aws:dynamodb:us-east-1:394566733278:table/control_panel_events" }
  ]
}
```

The admin role has no EC2 start permission, so it can never start an instance. Neither role can stop one.

## API Gateway

1. (Built 2026-10-04: API `65j334bc19`, all ten routes, authorizer `pnlj78`.) Create an HTTP API with two Lambda integrations (payload format 2.0), one for each function, and a `$default` stage.
2. Add a JWT authorizer. Until a provider exists it trusts the empty holding pool `us-east-1_xcTOLNQJM` with the audience `holding-unused`, so every route answers 401. Terraform points it at the lab's pool on apply and back at the holding pool on destroy (`control_panel_api.tf`). For another provider, update it by hand with `aws apigatewayv2 update-authorizer`. Issuer `https://cognito-idp.us-east-1.amazonaws.com/<user pool id>`. Audience: the control panel app client ID from `control_panel_config`.
3. Create the ten routes in the table above, each using the authorizer: the five `/admin/*` routes go to the admin function, the other five to the customer function. The page sends the **ID token**, which carries the name and verified email.
4. CORS: allow origin `https://cp.aiwebdemo.click`, methods `GET, POST, PUT, OPTIONS`, header `authorization, content-type`.
5. Put the API's address in `control_panel_api_url` in `terraform.tfvars`, apply, and run `.\scripts\make-panel-config.ps1`.

## Package and deploy (PowerShell)

Both functions use the same zip. Only the handler and the role differ.

```powershell
cd C:\GitHub\ai-cloud-lab\dashboards\api
Compress-Archive -Path handler.py, customer.py, admin.py -DestinationPath control-api.zip -Force
aws lambda create-function --function-name ai-lab-control-customer --runtime python3.12 `
  --handler customer.lambda_handler --timeout 15 --memory-size 256 `
  --role <customer role arn> --zip-file fileb://control-api.zip `
  --environment "Variables={BOOTSTRAP_ADMINS=<email>,INSTANCE_IDS=<instance id>,TARGET_GROUP_ARN=<target group arn>,AUTO_STOP_PARAMETER=/<project_name>/auto-stop,SERVICE_URL=https://aiwebdemo.click}"
aws lambda create-function --function-name ai-lab-control-admin --runtime python3.12 `
  --handler admin.lambda_handler --timeout 15 --memory-size 256 `
  --role <admin role arn> --zip-file fileb://control-api.zip `
  --environment "Variables={BOOTSTRAP_ADMINS=<email>,INSTANCE_IDS=<instance id>}"
```

To update after a change, rebuild the zip and run `aws lambda update-function-code --zip-file fileb://control-api.zip` for each function name.

## Tests

The tests replace AWS with in-memory fakes, so no account is needed. They cover the rules above.

```powershell
cd C:\GitHub\ai-cloud-lab\dashboards\api
python -m pip install boto3 pytest
python -m pytest -q
```
