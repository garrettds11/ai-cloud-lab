<#
.SYNOPSIS
  Sets the password for every Cognito user Terraform created, using the demo-user
  password stored in AWS Secrets Manager.

.DESCRIPTION
  Terraform creates the Cognito users without passwords so no password is stored
  in Terraform state. Run this once after `apply` (and again after adding users).
  It reads the same Secrets Manager secret that Open WebUI's demo accounts use
  and marks each password permanent, so users are not forced to change it at
  first sign-in.

  Run it from the repository directory in the same PowerShell window where
  AWS_PROFILE and AWS_DEFAULT_REGION are set (see terraform-smoke-test-plan.md).

.PARAMETER DemoPasswordSecretArn
  Secrets Manager ARN holding the password. Defaults to
  TF_VAR_open_webui_demo_user_password_secret_arn, then to terraform.tfvars.

.PARAMETER Region
  AWS region of the user pool. Defaults to AWS_DEFAULT_REGION.
#>
param(
  [string]$DemoPasswordSecretArn = $env:TF_VAR_open_webui_demo_user_password_secret_arn,
  [string]$Region = $env:AWS_DEFAULT_REGION
)

$ErrorActionPreference = "Stop"

if (-not $DemoPasswordSecretArn -and (Test-Path "terraform.tfvars")) {
  $match = Select-String -Path "terraform.tfvars" -Pattern '^\s*open_webui_demo_user_password_secret_arn\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($match) { $DemoPasswordSecretArn = $match.Matches[0].Groups[1].Value }
}
if (-not $DemoPasswordSecretArn) {
  throw "Set -DemoPasswordSecretArn or open_webui_demo_user_password_secret_arn in terraform.tfvars."
}
if (-not $Region) { throw "Set AWS_DEFAULT_REGION or pass -Region." }

$poolId = (terraform output -raw cognito_user_pool_id).Trim()
if ($LASTEXITCODE -ne 0 -or -not $poolId -or $poolId -eq "null") {
  throw "No Cognito user pool in Terraform outputs. Set enable_cognito = true and apply first."
}
# Windows PowerShell 5.1 returns a JSON array as a single object; enumerate it so
# each email is handled on its own.
$emails = @((terraform output -json cognito_user_emails | Out-String | ConvertFrom-Json) | ForEach-Object { $_ })
if ($emails.Count -eq 0) { throw "No Cognito users found in Terraform outputs." }

$secretString = (aws secretsmanager get-secret-value `
    --secret-id $DemoPasswordSecretArn `
    --query SecretString `
    --output text `
    --region $Region).Trim()
if ($LASTEXITCODE -ne 0 -or -not $secretString) { throw "Could not read the password secret." }

# The secret may be plain text or a one-key key/value secret, like the bootstrap script accepts.
$password = $secretString
$parsed = $null
try { $parsed = $secretString | ConvertFrom-Json -ErrorAction Stop } catch { $parsed = $null }
if ($parsed -is [pscustomobject]) {
  $values = @($parsed.PSObject.Properties)
  if ($values.Count -ne 1) { throw "Password secret must contain exactly one value." }
  $password = [string]$values[0].Value
}

foreach ($email in $emails) {
  Write-Host "Setting password for $email"
  aws cognito-idp admin-set-user-password `
    --user-pool-id $poolId `
    --username $email `
    --password $password `
    --permanent `
    --region $Region
  if ($LASTEXITCODE -ne 0) {
    throw "Setting the password failed for $email. Check the user pool password policy (minimum 8 characters with a lowercase letter and a number)."
  }
}

Write-Host "Done. $($emails.Count) Cognito user(s) can now sign in."
