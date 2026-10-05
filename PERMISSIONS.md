# Control panel permissions: how it works

This is the knowledge doc for the permissions framework behind the AI Cloud Lab control panel (`https://cp.aiwebdemo.click`). It describes what is enforced, where, and why. The code is `dashboards/api/handler.py`; the setup details are in `dashboards/api/README.md`.

## The idea in one paragraph

The panel answers two separate questions. **Who are you?** is answered by a sign-in provider (any OpenID Connect provider; Cognito is the first one used). **What may you do?** is answered by the panel's own tables, read on every call. The sign-in token proves identity only. Roles in the token are ignored, so a change made in the panel takes effect on the next request, with no sign-out needed.

## The three roles

Roles say **who someone is**. They are stored per person in the `panel_users` DynamoDB table (key `email`, attribute `roles`).

| Role | May do |
|---|---|
| `operators` | Launch and access instances they have been **granted**. Nothing else. |
| `user_mgrs` | Everything an operator needs to administer access: open User management and change **grants** (which instances a person may start). Cannot change roles. |
| `admin` | Everything. Sees and starts every managed instance without grants, changes **roles** and grants, and sees everyone's logins and logs. Any number of people can be administrators. |

Two kinds of change, two kinds of person:

- **Who** someone is (roles) is changed only by an administrator.
- **What** someone may start (grants) is changed by user managers and administrators.

A user manager is not automatically an operator. Someone who only manages access has no instances of their own unless they also hold `operators` (the demo's odd-numbered users hold both).

### Bootstrap administrator

`BOOTSTRAP_ADMINS` is an environment setting on both Lambdas holding one always-admin address. That person is an administrator whatever the table says, so the panel can never lock everyone out and nobody else can get in before a real person is added. The panel cannot remove this address's administrator role. Everything beyond this one address is managed in the panel by making more people `admin`.

## Grants: which instances an operator can use

Grants say **what** someone may start. They live in `instance_entitlements` (partition key `userId`, which is the email in lower case; sort key `instanceId`).

- A grant is a row with `status = applied`. Revoking sets `status = revoked` and keeps the row for the history. Rows are never deleted.
- An operator sees and starts an instance only with an applied grant. Having the role alone shows nothing.
- A grant needs the operator role. The panel refuses to grant an instance to someone who will not hold `operators` after the save (HTTP 409). Administrators are the exception because they need no grants.
- Removing someone's operator role revokes all their applied grants in the same save.
- Roles and grants can be changed in one save, so a person can be made an operator and granted an instance together. Everything is checked before anything is written.

## What each role can do

| Action | operators | user_mgrs | admin |
|---|---|---|---|
| Sign in, see own sign-ins and own logs | yes | yes | yes |
| See instances | granted ones | granted ones (needs operators too) | all managed instances |
| Start an instance | granted ones | granted ones (needs operators too) | all managed instances |
| Open User management, see users, instances, access changes | no | yes | yes |
| Grant or revoke instances for others | no | yes | yes |
| Change anyone's roles | no | no | yes |
| Remove own administrator role | n/a | n/a | no (another admin must) |
| Remove the bootstrap admin's administrator role | n/a | n/a | no |
| See everyone's logins and logs | no | no | yes |

Nobody can stop an instance from the panel. Auto-stop is a separate automatic rule on the instance.

## Where each rule is enforced

The page hides things a person cannot use, but that is cosmetic. Every rule is checked again in the API.

1. **Sign-in check: API Gateway JWT authorizer.** Every route needs a valid token from the configured provider. Terraform points the authorizer at the lab's Cognito pool on apply and back at a holding pool on destroy.
2. **Role check: the Lambda, per call.** The handler reads the caller's row from `panel_users` on every request (a failed read returns 503, so it fails closed) and decides from that.
3. **Two functions, two IAM roles.** One shared handler has two entry points:
   - *customer* (`ai-lab-control-customer`): sign-in session, instances, start, own logins and logs. Its IAM role can start only instances tagged `control-panel=managed`, and cannot write roles or grants.
   - *admin* (`ai-lab-control-admin`): user management and the admin views. Its IAM role can write roles and grants and scan the events table.

   Even a bug in the customer code cannot change a role or a grant, because that function has no permission to.
4. **Instance scope.** The instances the panel manages come from SSM parameters Terraform publishes (`/<project>/control-panel/instance-ids`, plus the target group and service URL), read live and cached for 60 seconds. With no lab deployed they do not exist, so the panel shows no instances.

## Routes and who may call them

| Route | Who |
|---|---|
| `POST /session` | anyone signed in. Records the sign-in and adds the person to `panel_users` with no roles on first sight |
| `GET /instances` | operators (granted ones), admins (all) |
| `POST /instances/{id}/start` | operators with a grant, admins |
| `GET /logins`, `GET /logs` | anyone (own activity; logs also include EC2 activity for the caller's instances) |
| `GET /admin/users`, `/admin/instances`, `/admin/users/{id}/grants`, `/admin/changes` | `user_mgrs` or `admin` |
| `PUT /admin/users/{id}` | `user_mgrs` or `admin`. Any role change in the body needs `admin` |
| `GET /admin/logins`, `GET /admin/logs` | `admin` only |

## History and logs

Every role and grant change is written to the history (`control_panel_events`) with who made it and when, and shown as the latest access changes in User management. Starts, refusals (a start without a grant is refused with 403 and logged), sign-ins and changes also go to the per-person log. Events expire by TTL.

## The demo

When Terraform is given the table names, it seeds a demo so the framework can be tried straight away. Terraform writes these rows, and a later apply puts them back as declared (a table backup is the only way to keep earlier data):

| Demo account | Roles | Grant |
|---|---|---|
| `demo1`, `demo3`, `demo5`, `demo7`, `demo9` | `operators`, `user_mgrs` | lab instance |
| `demo2`, `demo4`, `demo6`, `demo8`, `demo10` | `operators` | lab instance |
| `admin@example.local` | `operators`, `admin` | lab instance |

Variables: `control_panel_users_table` seeds the roles, `control_panel_entitlements_table` seeds the grants. People added in the panel are never touched by Terraform.

## Worked examples

- **A new person signs in.** `POST /session` adds them with no roles. They see an empty Instances page. An administrator gives them `operators`, and a user manager or administrator grants an instance. Their next request shows it.
- **An operator tries an instance they were not granted.** The start is refused with 403 and logged.
- **A user manager tries to make someone an administrator.** Refused with 403: only an administrator changes roles.
- **An administrator removes someone's operator role.** The role is removed and all their applied grants are revoked in the same save.
- **An administrator tries to demote themselves.** Refused with 409. Another administrator must do it.

## Out of scope for now

- Adding new users from the panel (people appear on first sign-in; roles are given afterwards).
- Per-instance roles. Roles are global, and grants carry the per-instance part.
