# Control panel API: Terraform stack

This folder builds the control panel's API with Terraform, separately from the lab. The lab (the repository root) is applied and destroyed often; the API must keep answering when no lab exists, so it has its own state and is applied once, then only when the API changes.

## What it creates

| Piece | Notes |
|---|---|
| HTTP API, `$default` stage | CORS for `panel_origin` only. Throttled (10 requests a second, bursts of 20, by default). Access log in CloudWatch: who, which route, the status. No bodies or tokens |
| JWT authorizer | Created trusting an empty holding pool, so every route answers 401. The lab points it at its own Cognito pool on apply and back on destroy (`control_panel_api.tf` at the root), exactly as before. This stack never changes the issuer or audience after creating it |
| 13 routes | The list in `api.tf`. `../tests/test_openapi.py` fails if it differs from `../openapi.yaml` or from what `handler.py` serves |
| Two functions and their roles | `<name_prefix>-customer` and `<name_prefix>-admin`, built from `../handler.py`. The policies are the ones `../README.md` documents: only the customer role can start an instance (tagged `control-panel=managed`) and write the timer reset; the admin role can do neither |
| Three tables | `panel_users`, `instance_entitlements`, `control_panel_events`, with deletion protection and point-in-time recovery. Existing hand-built tables are imported, keeping their data |
| Holding pool | An empty Cognito pool, no users, no app clients |

Not here (still built by hand, see `../../../SETUP.md`): the panel's S3 bucket, CloudFront distribution, certificate and DNS.

## First time: move from the hand-built API

The new API gets a new address. The hand-built one keeps working until the lab is pointed at the new one, so there is no outage, and going back is one setting.

Run these in PowerShell from the repository, in the window where `AWS_PROFILE` and `AWS_DEFAULT_REGION` are set.

1. **Settings.** Copy the example and fill it in. Keep `adopt_existing_tables = true`, so the existing tables and their data are imported rather than created.

   ```powershell
   cd C:\GitHub\ai-cloud-lab\dashboards\api\terraform
   Copy-Item terraform.tfvars.example terraform.tfvars
   notepad terraform.tfvars
   ```

2. **State.** Keep this stack's state apart from the lab's. With the S3 backend from the root `backend.tf.example`, copy it here as `backend.tf` and use a different key, for example `ai-cloud-lab/control-api.tfstate`. Without a backend the state is the local `terraform.tfstate` in this folder; do not lose it.

3. **Plan, and read it.** The three tables must show **will be imported**, possibly with in-place updates (deletion protection, point-in-time recovery). They must not show **must be replaced** or **destroy**. If they do, stop: the table keys differ from `tables.tf`. Everything else is created.

   ```powershell
   terraform init
   terraform plan -out cutover.tfplan
   ```

4. **Apply.**

   ```powershell
   terraform apply cutover.tfplan
   ```

5. **Point the lab at the new API.** Print the lines for the lab's settings, and replace the matching lines in the root `terraform.tfvars`:

   ```powershell
   terraform output -raw lab_tfvars
   ```

   Then apply the lab as usual from the repository root. That publishes the new address in `config.js` and points the new authorizer at the lab's Cognito pool. Until then the new API answers 401 to everything, which is expected.

6. **Check the panel.** Sign in, see the instance, start it, reset the timer, and as an administrator open User management and the logs.

7. **Remove the hand-built API** once the panel works, so there is only one. The tables are not deleted: they now belong to this stack.

   ```powershell
   aws apigatewayv2 delete-api --api-id 65j334bc19
   aws lambda delete-function --function-name ai-lab-control-customer
   aws lambda delete-function --function-name ai-lab-control-admin
   foreach ($role in 'ai-lab-control-customer', 'ai-lab-control-admin') {
     foreach ($p in (aws iam list-role-policies --role-name $role --query 'PolicyNames' --output text) -split '\s+' | Where-Object { $_ }) {
       aws iam delete-role-policy --role-name $role --policy-name $p
     }
     foreach ($a in (aws iam list-attached-role-policies --role-name $role --query 'AttachedPolicies[].PolicyArn' --output text) -split '\s+' | Where-Object { $_ }) {
       aws iam detach-role-policy --role-name $role --policy-arn $a
     }
     aws iam delete-role --role-name $role
   }
   aws cognito-idp delete-user-pool --user-pool-id us-east-1_xcTOLNQJM
   ```

**Going back** before step 7: put the old values back in the root `terraform.tfvars` (API `65j334bc19`, authorizer `pnlj78`, holding pool `us-east-1_xcTOLNQJM`, and the old API address) and apply the lab.

## Later changes

Change `../handler.py` or a file here, then `terraform plan` and `terraform apply` in this folder. A code change updates both functions in place; the API address does not change.

A new route goes in three places: `handler.py`, `../openapi.yaml` and `local.routes` in `api.tf`. The tests fail until all three agree.

## Tests

Offline, with mocked providers:

```powershell
cd C:\GitHub\ai-cloud-lab\dashboards\api\terraform
terraform init -backend=false
terraform test
```
