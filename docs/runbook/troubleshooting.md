# Troubleshooting a deploy

Likely failure points when following [the deploy runbook](terraform-smoke-test-plan.md), with
the fix for each. Commands are **Local Windows PowerShell**, in the runbook's window.

## Before or during plan

- **AWS credentials are missing, expired, or lack EC2, IAM, security-group, or SSM permissions.**
  Check the account with `aws sts get-caller-identity --query Account --output text`.
- **Plan acts on the wrong account** (for example `Cannot import non-existent remote object` for
  the panel tables): `$env:AWS_PROFILE` is not set in this window, or names another account. Both
  tfvars examples keep `aws_profile = null`, so the profile comes only from `$env:AWS_PROFILE`.
- **`Too many command line arguments`:** a plain `terraform` argument with a dot after `=` was not
  quoted. Use `terraform plan "-out=api.tfplan"`.
- **`403 Missing X-Auth-Email header`:** a lab Terraform command was run without the wrapper, so no
  Cloudflare token was set. (The API stack in `dashboards\api\terraform` does not need the
  wrapper.)
- **A secret ARN is rejected at plan** (`must be the secret's full ARN`): the ARN is cut short.
  Secrets Manager ARNs end in a hyphen and six random characters, and IAM matches them exactly, so
  a short one gives no access. Print the full one with
  `aws secretsmanager describe-secret --secret-id <secret name> --query ARN --output text` and put
  it in the example.
- **The lab plan warns about `check.control_panel_api_settings_found`:** the API stack is not
  applied, or its `lab_project_name` differs from the lab's `project_name`, or `AWS_PROFILE` points
  at another account. Fix that before applying the lab.
- **The API stack plan shows a table must be replaced:** the existing table's keys differ from
  `tables.tf`. Stop; do not apply.
- **Domain access fails:** it needs an issued ACM certificate in the selected region that covers
  `domain_name` exactly, plus either a public Route 53 hosted zone or, with Cloudflare, an
  **Active** Cloudflare zone. See
  [cloudflare-and-domain-requirements.md](../../cloudflare-and-domain-requirements.md).

## During apply or boot

- **The selected region has no default VPC.**
- **The account lacks `c7i.4xlarge` quota, or the selected subnet's AZ does not offer that
  instance type.**
- **Bootstrap is still downloading** packages, Ollama, the model, or the Open WebUI image. Wait for
  `READY` (runbook step 4).
- **A 403 on the Cloudflare Worker resources:** the Cloudflare token lacks **Account: Workers
  Scripts Edit** and **Zone: Workers Routes Edit**.

## After apply

- **The panel answers 401 to everyone after the API stack was created or rebuilt:** the lab apply
  did not replace `terraform_data.control_panel_authorizer`, so the authorizer is still on its
  holding pool. Force the replacement from the repository root (Windows PowerShell 5.1 drops the
  inner quotes unless they are escaped, PowerShell 7.3 and later passes them as written):

  ```powershell
  $authorizer = 'terraform_data.control_panel_authorizer["authorizer"]'
  if ($PSVersionTable.PSVersion -lt [version]'7.3') { $authorizer = $authorizer.Replace('"', '\"') }
  Invoke-TerraformWithCloudflareToken -Arguments @("plan", "-replace=$authorizer", "-out=ai-lab.tfplan")
  ```

  Check that the plan replaces only that resource, then apply `ai-lab.tfplan` as usual.
- **The panel says "Could not reach the Control API", or still uses an old API:** the pages or
  `config.js` are cached, or the lab was applied before it knew the API's address. Check
  `aws s3 cp "s3://$panelBucket/config.js" -`, then reload with Ctrl+F5. Deploy the pages if
  they changed (runbook step 3).
- **An Open WebUI action fails with a 409 about Systems Manager:** the instance has not registered
  with SSM yet. Wait a minute after it reaches `READY`.
- **The Session Manager plugin is not installed locally:** `aws ssm start-session` fails until it
  is.
- **Local port 8080 is already occupied:** use local port 8081 for the tunnel (SSM-only test).
- **The vulnerability findings tool is missing from Open WebUI:** see the "Two kinds of failure"
  table in [vuln-mcp-acceptance-tests.md](../vuln-mcp-acceptance-tests.md#two-kinds-of-failure), and
  the troubleshooting table in [lambda/vuln_mcp/README.md](../../lambda/vuln_mcp/README.md).
