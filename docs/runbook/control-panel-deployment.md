# Control panel deployment

How to deploy the control panel's **pages** (`dashboards\`) after they change. This is a small,
frequent step that is **separate from Terraform** and separate from the API stack.

Everything here is **Local Windows PowerShell**, in a window prepared by
[deploy runbook step 1](terraform-smoke-test-plan.md#step-1-prepare-every-new-powershell-window)
(`$env:AWS_PROFILE` set, and the `Get-TfVar` values loaded).

## Why this is not Terraform

The panel's S3 bucket, CloudFront distribution and DNS are created by hand, once, in
[pre-deployment step 8](pre-deployment.md#8-control-panel-hosting), and they stay in place when a
lab is destroyed. Terraform only writes one file into that bucket, `config.js` (this lab's Cognito
sign-in settings), from `control_panel_site.tf`.

The page shell — `index.html`, `app.js`, `styles.css`, `api.js` — is left out of Terraform on
purpose. If the lab stack owned those files, a `terraform destroy` would delete them and the panel
would return a 404 until the next apply. Keeping them out means the panel site survives the lab's
lifecycle, at the cost of this one manual deploy step.

Because of that split, deploying the pages:

- does **not** touch the lab EC2 instance, Open WebUI, Ollama, the load balancer or anything else
  in the lab stack;
- does **not** touch the API stack (`dashboards\api`);
- leaves `config.js` alone (the lab apply owns it).

## When to run it

Run it whenever a file under `dashboards\` changes and it is **not** under `dashboards\api`
(that is the API Lambda stack, deployed with Terraform in
[deploy runbook step 2](terraform-smoke-test-plan.md#step-2-control-panel-api-stack)).

| You changed | Deploy with |
|---|---|
| `dashboards\app.js`, `styles.css`, `index.html`, `api.js` | this page |
| `dashboards\api\...` (handler, routes, functions, SSM document) | the API stack, [step 2](terraform-smoke-test-plan.md#step-2-control-panel-api-stack) |
| The lab (instance, Open WebUI version, Cloudflare, Cognito) | the lab stack, [steps 3-4](terraform-smoke-test-plan.md#step-3-lab) |

A Lambda change and a page change are independent: deploying one never requires the other.

## Deploy

From the repository root, after step 1:

```powershell
.\scripts\deploy-panel.ps1
```

The script reads `control_panel_bucket` and `control_panel_distribution_id` from
`terraform.tfvars`, uploads the pages, deletes any bucket objects that are no longer in
`dashboards\`, and clears the CloudFront cache. Reload the panel with **Ctrl+F5** about a minute
after it prints the invalidation ID.

Options:

| Command | Does |
|---|---|
| `.\scripts\deploy-panel.ps1 -DryRun` | Lists what would upload or delete; changes nothing, skips the invalidation. |
| `.\scripts\deploy-panel.ps1 -Profile ai-cloud-lab` | Uses a named AWS CLI profile instead of `$env:AWS_PROFILE`. |
| `.\scripts\deploy-panel.ps1 -Bucket my-bucket -DistributionId E123...` | Overrides the bucket and distribution (for a non-demo environment whose values are not in this `terraform.tfvars`). |

## What the script does

It is exactly these two AWS CLI calls, with the values filled in:

```powershell
aws s3 sync .\dashboards "s3://$panelBucket" `
  --exclude "*.md" --exclude "config.example.js" --exclude "config.js" `
  --exclude "api/*" --exclude "_deploy/*" --delete
aws cloudfront create-invalidation --distribution-id $panelDistribution --paths "/*"
```

- The excludes keep docs, the example config, the live `config.js`, the API stack and local deploy
  scratch out of the upload. `--delete` removes stale objects, but an excluded key (`config.js`
  above all) is never deleted.
- The invalidation is what makes the change appear. CloudFront caches the pages at the edge, so a
  browser hard refresh alone keeps serving the old files until the cache is cleared or expires.

If PowerShell cannot run the script (execution policy on a locked-down machine), run the two
commands above directly, after setting `$panelBucket` and `$panelDistribution` with the
`Get-TfVar` helper from step 1.

## Verify

1. Reload the panel at `control_panel_url` with **Ctrl+F5**.
2. If it still looks old after a minute, confirm the right `app.js` is live:

   ```powershell
   aws s3api head-object --bucket $panelBucket --key app.js --query "LastModified" --output text
   ```

   The time should be seconds ago. If it is, the invalidation is still propagating; wait and reload.
3. `config.js` should be untouched by this deploy (its `LastModified` stays from the last lab
   apply):

   ```powershell
   aws s3 cp "s3://$panelBucket/config.js" -
   ```

The panel's behaviour is then checked in the smoke tests:
[control panel smoke tests](smoke-tests/control-panel.md).

## Cautions

- This deploys pages only. A fix that lives in the panel's **API** (for example a validation
  message in `handler.py`) is live only after the API stack is applied in
  [step 2](terraform-smoke-test-plan.md#step-2-control-panel-api-stack); uploading pages does not
  ship it.
- Never edit `config.js` in the bucket by hand. It is regenerated by the lab apply; a hand edit is
  overwritten on the next apply and lost on destroy.
