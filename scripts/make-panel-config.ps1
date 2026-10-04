<#
.SYNOPSIS
  Writes dashboards/config.js, the file the control panel loads, from Terraform's
  control_panel_config output.

.DESCRIPTION
  Terraform creates the panel's Cognito app client and exposes the region, user
  pool ID, app client ID and API address as the control_panel_config output.
  This script turns that output into dashboards/config.js. It never touches
  index.html or any other page, and Terraform does not run it.

  Run it from the repository directory after `apply`, and again after anything
  that changes those values. Terraform is read through
  Invoke-TerraformWithCloudflareToken, so load that function first (your
  PowerShell profile or the setup in terraform-smoke-test-plan.md).

  Upload the result with the rest of the pages (see dashboards/SETUP.md).

.PARAMETER ApiUrl
  Control API address. Overrides control_panel_api_url from Terraform, for use
  before that value is in terraform.tfvars.

.PARAMETER OutFile
  Where to write the file. Defaults to dashboards\config.js.
#>
param(
  [string]$ApiUrl,
  [string]$OutFile = (Join-Path $PSScriptRoot "..\dashboards\config.js")
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command Invoke-TerraformWithCloudflareToken -ErrorAction SilentlyContinue)) {
  throw "Invoke-TerraformWithCloudflareToken is not loaded. Load it first, then run this script again."
}

$json = (Invoke-TerraformWithCloudflareToken -Arguments @("output", "-json", "control_panel_config") | Out-String).Trim()
if (-not $json -or $json -eq "null") {
  throw "Terraform has no control_panel_config. Set enable_cognito = true and control_panel_url in terraform.tfvars, then apply."
}

$config = $json | ConvertFrom-Json
foreach ($name in @("region", "userPoolId", "appClientId", "hostedLoginDomain", "redirectUri")) {
  if (-not $config.$name) { throw "control_panel_config is missing '$name'." }
}

if ($ApiUrl) { $config.apiUrl = $ApiUrl }
if (-not $config.apiUrl) {
  Write-Warning "No API address yet. config.js is written with apiUrl null; set control_panel_api_url in terraform.tfvars or pass -ApiUrl."
}

$body = "// Written by scripts/make-panel-config.ps1 from Terraform's control_panel_config output.`n" +
        "// Do not edit; run the script again to refresh it.`n" +
        "window.PANEL_CONFIG = " + ($config | ConvertTo-Json -Depth 4) + ";`n"

$resolved = [System.IO.Path]::GetFullPath($OutFile)
[System.IO.File]::WriteAllText($resolved, $body, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Wrote $resolved"
