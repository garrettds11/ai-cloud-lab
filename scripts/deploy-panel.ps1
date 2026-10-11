<#
.SYNOPSIS
  Deploy the AI Cloud Lab control panel pages to their S3 bucket and clear the
  CloudFront cache.

.DESCRIPTION
  The panel's bucket, CloudFront distribution and DNS live outside Terraform on
  purpose (see control_panel_site.tf), so the static pages in dashboards\ are
  deployed with this script, not by `terraform apply`.

  It uploads the pages, deletes bucket objects no longer in dashboards\, and
  invalidates CloudFront so the new files are served at once. It does NOT touch:
    - config.js        (written by the lab apply; it holds this lab's sign-in settings)
    - dashboards\api    (the API Lambda stack, deployed with Terraform)
    - the lab EC2 instance or anything else in the lab stack

  Bucket and distribution are read from terraform.tfvars
  (control_panel_bucket, control_panel_distribution_id) unless overridden.

.PARAMETER Bucket
  Override the S3 bucket. Default: control_panel_bucket from terraform.tfvars.

.PARAMETER DistributionId
  Override the CloudFront distribution. Default: control_panel_distribution_id
  from terraform.tfvars.

.PARAMETER Profile
  AWS CLI profile to use. Default: none, so the AWS CLI uses $env:AWS_PROFILE
  (which the deploy runbook sets in step 1).

.PARAMETER DryRun
  Show what would upload without changing anything, and skip the invalidation.

.EXAMPLE
  .\scripts\deploy-panel.ps1
  Upload changed pages and clear the cache, using terraform.tfvars and $env:AWS_PROFILE.

.EXAMPLE
  .\scripts\deploy-panel.ps1 -DryRun
  List what would change; make no changes.

.EXAMPLE
  .\scripts\deploy-panel.ps1 -Profile ai-cloud-lab
  Use a named AWS CLI profile instead of $env:AWS_PROFILE.
#>
[CmdletBinding()]
param(
  [string]$Bucket,
  [string]$DistributionId,
  [string]$Profile,
  [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$repoRoot   = Split-Path -Parent $PSScriptRoot
$dashboards = Join-Path $repoRoot 'dashboards'
$tfvars     = Join-Path $repoRoot 'terraform.tfvars'

function Get-TfVar([string]$Name) {
  if (-not (Test-Path $tfvars)) { throw "terraform.tfvars not found at $tfvars. Run from the repo, or pass -Bucket and -DistributionId." }
  $m = Select-String -Path $tfvars -Pattern ('^\s*' + $Name + '\s*=\s*"([^"]+)"') | Select-Object -First 1
  if (-not $m) { throw "$Name is not set in $tfvars. Pass it with -$Name instead." }
  $m.Matches.Groups[1].Value
}

if (-not (Test-Path $dashboards)) { throw "dashboards folder not found at $dashboards" }
if (-not $Bucket)         { $Bucket         = Get-TfVar control_panel_bucket }
if (-not $DistributionId) { $DistributionId = Get-TfVar control_panel_distribution_id }

$profileArgs = @()
if ($Profile) { $profileArgs = @('--profile', $Profile) }

# config.js is owned by the lab apply; api\ is its own stack; docs and examples are not served.
# --delete removes stale objects, but excluded keys (config.js above all) are never deleted.
$syncArgs = @(
  's3', 'sync', $dashboards, "s3://$Bucket",
  '--exclude', '*.md',
  '--exclude', 'config.example.js',
  '--exclude', 'config.js',
  '--exclude', 'api/*',
  '--exclude', '_deploy/*',
  '--delete'
) + $profileArgs
if ($DryRun) { $syncArgs += '--dryrun' }

Write-Host "Panel pages: $dashboards -> s3://$Bucket" -ForegroundColor Cyan
aws @syncArgs
if ($LASTEXITCODE -ne 0) { throw "aws s3 sync failed (exit $LASTEXITCODE)" }

if ($DryRun) {
  Write-Host "Dry run: no files changed, CloudFront invalidation skipped ($DistributionId)." -ForegroundColor Yellow
  return
}

Write-Host "Clearing CloudFront cache: $DistributionId" -ForegroundColor Cyan
# Out-String joins AWS CLI's multi-line JSON so ConvertFrom-Json parses it on every PowerShell version.
$invJson = aws cloudfront create-invalidation --distribution-id $DistributionId --paths '/*' --output json @profileArgs | Out-String
if ($LASTEXITCODE -ne 0) { throw "aws cloudfront create-invalidation failed (exit $LASTEXITCODE)" }
$invId = ($invJson | ConvertFrom-Json).Invalidation.Id

Write-Host "Done. Invalidation $invId submitted." -ForegroundColor Green
Write-Host "Reload the panel with Ctrl+F5 after about a minute." -ForegroundColor Green
