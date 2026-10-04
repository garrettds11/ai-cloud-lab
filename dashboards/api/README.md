# Control API

The Lambda function behind the control panel (`handler.py`, Python 3.12, no packages beyond what Lambda already includes). It sits behind an API Gateway HTTP API with a Cognito JWT authorizer. The page calls only these routes. It never reads DynamoDB or calls AWS itself.

It is built by hand, outside Terraform (see `../SETUP.md`).

## Rules it enforces

- Every route needs a signed-in user. Group membership is read live from Cognito on every call, so removing someone from a group takes effect at once, even while their token is still valid. If Cognito cannot be read, the call fails closed (503).
- A customer sees and starts only the instances they hold an active grant for, and only while they are in `operators`. Nobody can stop an instance through this API; there is no stop route.
- Admin routes need `user_mgrs`. Saving changes writes `instance_entitlements` and Cognito group membership only. It never changes IAM, tags or instances.
- An admin cannot remove their own `user_mgrs` membership. Removing someone from `operators` revokes all their grants. A user who is not an operator cannot be granted an instance.
- Everything is validated before anything is written. Errors never include internal detail.

## Routes

All routes need `Authorization: Bearer <ID token>`. Times are milliseconds since the epoch.

| Route | Who | Returns |
|---|---|---|
| `POST /session` | anyone signed in | Records this sign-in once, returns `{userId, name, email, groups, signedInAt}` |
| `GET /instances` | anyone | The caller's granted instances (empty unless an operator) |
| `POST /instances/{instanceId}/start` | operator with a grant | `{ok: true}`. 403 without a grant, 404 unknown, 409 not stopped, 503 no capacity |
| `GET /logins` | anyone | The caller's last 10 panel sign-ins |
| `GET /logs?source=&severity=&windowMinutes=` | anyone | The caller's own log lines, plus EC2 start/stop activity for instances they hold |
| `GET /admin/users` | `user_mgrs` | Every pool user with `groups` |
| `GET /admin/instances` | `user_mgrs` | The managed instances |
| `GET /admin/users/{userId}/grants` | `user_mgrs` | Instance IDs the user holds |
| `GET /admin/changes` | `user_mgrs` | The last 12 access changes |
| `PUT /admin/users/{userId}` | `user_mgrs` | Body `{groups:[{group,member}], grants:[{instanceId,grant}]}`; returns `{applied}` |

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

Sign-ins are recorded when the page loads after Cognito sign-in. Failed sign-ins are not visible: Cognito only exposes them with its paid advanced security tier.

## Tables

Create both in DynamoDB (on-demand billing).

| Table | Keys | Notes |
|---|---|---|
| `instance_entitlements` | `userId` (string, partition), `instanceId` (string, sort) | Schema in the PRD, FR-4. Rows are never deleted: revoking sets `status` to `revoked` |
| `control_panel_events` | `pk` (string, partition), `sk` (string, sort) | Turn on TTL for the attribute `expiresAt`. Holds `user#<id>` sign-ins and log lines, and the `changes` history |

## Settings (Lambda environment variables)

| Variable | Value |
|---|---|
| `USER_POOL_ID` | Terraform output `cognito_user_pool_id` |
| `INSTANCE_IDS` | Comma-separated instance IDs the panel manages, from Terraform output `instance_id` |
| `TARGET_GROUP_ARN` | Terraform output `open_webui_target_group_arn` |
| `AUTO_STOP_PARAMETER` | `/<project_name>/auto-stop` |
| `SERVICE_URL` | Where the Access button goes, for example `https://aiwebdemo.click` |
| `ENTITLEMENTS_TABLE`, `EVENTS_TABLE` | Table names (defaults match the table above) |
| `EVENT_TTL_DAYS` | Optional, default 90 |

## Permissions for the function's role

Replace the placeholders. Add the usual `AWSLambdaBasicExecutionRole` for logs.

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
    { "Effect": "Allow",
      "Action": ["cognito-idp:AdminListGroupsForUser", "cognito-idp:ListUsers", "cognito-idp:ListUsersInGroup",
                 "cognito-idp:AdminAddUserToGroup", "cognito-idp:AdminRemoveUserFromGroup"],
      "Resource": "arn:aws:cognito-idp:us-east-1:394566733278:userpool/<user pool id>" },
    { "Effect": "Allow", "Action": ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"],
      "Resource": ["arn:aws:dynamodb:us-east-1:394566733278:table/instance_entitlements",
                   "arn:aws:dynamodb:us-east-1:394566733278:table/control_panel_events"] }
  ]
}
```

The role can start one instance and can never stop one. It can add and remove users in groups, but the code only ever touches `operators` and `user_mgrs`.

## API Gateway

1. Create an HTTP API with a Lambda integration for this function (payload format 2.0) and a `$default` stage.
2. Add a JWT authorizer. Issuer `https://cognito-idp.us-east-1.amazonaws.com/<user pool id>`. Audience: the control panel app client ID from `control_panel_config`.
3. Create the ten routes in the table above, each using the authorizer. The page sends the **ID token**, which carries the name, email and groups.
4. CORS: allow origin `https://cp.aiwebdemo.click`, methods `GET, POST, PUT, OPTIONS`, header `authorization, content-type`.
5. Put the API's address in `control_panel_api_url` in `terraform.tfvars`, apply, and run `.\scripts\make-panel-config.ps1`.

## Package and deploy (PowerShell)

```powershell
cd C:\GitHub\ai-cloud-lab\dashboards\api
Compress-Archive -Path handler.py -DestinationPath control-api.zip -Force
aws lambda create-function --function-name ai-lab-control-api --runtime python3.12 `
  --handler handler.lambda_handler --timeout 15 --memory-size 256 `
  --role <role arn> --zip-file fileb://control-api.zip `
  --environment "Variables={USER_POOL_ID=<pool id>,INSTANCE_IDS=<instance id>,TARGET_GROUP_ARN=<target group arn>,AUTO_STOP_PARAMETER=/<project_name>/auto-stop,SERVICE_URL=https://aiwebdemo.click}"
```

To update after a change:

```powershell
Compress-Archive -Path handler.py -DestinationPath control-api.zip -Force
aws lambda update-function-code --function-name ai-lab-control-api --zip-file fileb://control-api.zip
```

## Tests

The tests replace AWS with in-memory fakes, so no account is needed. They cover the rules above.

```powershell
cd C:\GitHub\ai-cloud-lab\dashboards\api
python -m pip install boto3 pytest
python -m pytest -q
```
