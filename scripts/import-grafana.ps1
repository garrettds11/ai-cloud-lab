<#
.SYNOPSIS
  Imports the AI lab's Grafana dashboards and alert rules into a Grafana stack.

.DESCRIPTION
  Reads grafana/dashboards/*.json and grafana/alerts/alert-rules.json from this repository and
  creates or updates them in Grafana through its HTTP API. Safe to run again: dashboards are
  overwritten by their ID, and an alert rule that already exists is updated in place.

  Needs a Grafana service account token with the Admin role (alert rules need it). Create it in
  Grafana under Administration, Users and access, Service accounts. Put it in the environment,
  not on the command line:

      $env:GRAFANA_TOKEN = '<token>'

  The dashboards choose their data sources with variables, so they work in any stack. The alert
  rules need real data source IDs: this script finds the Prometheus-compatible and CloudWatch data
  sources by type. Rules that need a CloudWatch data source are skipped, with a warning, until you
  add one (docs/runbook/smoke-tests/lab-features.md, Grafana dashboards and alerts test).

  The rules notify through your default notification policy. Set up an email contact point there
  first, or nothing will be sent.

.PARAMETER GrafanaUrl
  The stack address, for example https://yourstack.grafana.net (no trailing slash).

.PARAMETER InstanceId
  The lab instance's ID (terraform output instance_id). Needed by the "Telemetry silent" rule;
  that rule is skipped without it.

.PARAMETER ProjectName
  The lab's project_name (the Lambda is called <project_name>-vuln-mcp). Read from
  terraform.tfvars.example when left out.

.PARAMETER SkipAlerts
  Import the dashboards only.
#>
param(
  [Parameter(Mandatory = $true)][string]$GrafanaUrl,
  [string]$InstanceId = '',
  [string]$ProjectName = '',
  [switch]$SkipAlerts
)

$ErrorActionPreference = 'Stop'
$GrafanaUrl = $GrafanaUrl.TrimEnd('/')
if ($GrafanaUrl -notmatch '^https://[A-Za-z0-9.-]+(:[0-9]+)?$') { throw 'GrafanaUrl must look like https://yourstack.grafana.net' }
if (-not $env:GRAFANA_TOKEN) { throw 'Set $env:GRAFANA_TOKEN to a Grafana service account token first.' }

$root = Split-Path -Parent $PSScriptRoot
$headers = @{ Authorization = "Bearer $($env:GRAFANA_TOKEN)"; 'Content-Type' = 'application/json' }

function Invoke-Grafana {
  # Body is an object to convert; RawBody is JSON text sent as it is (the dashboard files are sent exactly as written).
  param([string]$Method, [string]$Path, $Body = $null, [string]$RawBody = '', [hashtable]$Extra = @{}, [int[]]$Tolerate = @())
  $params = @{ Method = $Method; Uri = "$GrafanaUrl$Path"; Headers = ($headers + $Extra) }
  if ($RawBody) { $params.Body = [System.Text.Encoding]::UTF8.GetBytes($RawBody) }
  elseif ($null -ne $Body) { $params.Body = ($Body | ConvertTo-Json -Depth 40 -Compress) }
  try {
    return Invoke-RestMethod @params
  } catch {
    $status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    if ($Tolerate -contains $status) { return $null }
    throw "Grafana answered $status for $Method $Path. $($_.ErrorDetails.Message)"
  }
}

# The project name goes into the Lambda name the panels query. The example tfvars file is the source of truth.
if (-not $ProjectName) {
  $line = Select-String -Path (Join-Path $root 'terraform.tfvars.example') -Pattern '^\s*project_name\s*=\s*"([^"]+)"' | Select-Object -First 1
  if ($line) { $ProjectName = $line.Matches[0].Groups[1].Value }
}
if ($ProjectName -notmatch '^[a-z0-9][a-z0-9-]{0,40}$') { throw 'Could not work out the project name. Pass -ProjectName (letters, digits and hyphens).' }
Write-Host "Project name: $ProjectName"

# Folder: 409 or 412 means it already exists.
$folderUid = 'ai-lab'
$null = Invoke-Grafana -Method POST -Path '/api/folders' -Body @{ uid = $folderUid; title = 'AI Lab' } -Tolerate 409, 412
Write-Host "Folder 'AI Lab' is ready."

# Dashboards
foreach ($file in Get-ChildItem -Path (Join-Path $root 'grafana/dashboards') -Filter *.json) {
  $json = (Get-Content -Raw -Encoding UTF8 -Path $file.FullName).Replace('__PROJECT__', $ProjectName)
  $title = ($json | ConvertFrom-Json).title
  $wrapped = '{"dashboard":' + $json + ',"folderUid":"' + $folderUid + '","overwrite":true,"message":"Imported from the ai-cloud-lab repository"}'
  $null = Invoke-Grafana -Method POST -Path '/api/dashboards/db' -RawBody $wrapped
  Write-Host "Dashboard imported: $title"
}

if ($SkipAlerts) {
  Write-Host "Open the Lab overview: $GrafanaUrl/d/ai-lab-overview"
  return
}

# Data sources, found by type.
$sources = Invoke-Grafana -Method GET -Path '/api/datasources'
$prom = $sources | Where-Object { $_.type -eq 'prometheus' -and $_.name -match '-prom$' } | Select-Object -First 1
if (-not $prom) { $prom = $sources | Where-Object { $_.type -eq 'prometheus' } | Select-Object -First 1 }
$cw = $sources | Where-Object { $_.type -eq 'cloudwatch' } | Select-Object -First 1
if (-not $prom) { throw 'No Prometheus data source found in this stack.' }
Write-Host "Metrics data source: $($prom.name)"
if ($cw) { Write-Host "CloudWatch data source: $($cw.name)" } else { Write-Warning 'No CloudWatch data source yet: the rules that read CloudWatch will be skipped.' }

$definition = Get-Content -Raw -Encoding UTF8 -Path (Join-Path $root 'grafana/alerts/alert-rules.json') | ConvertFrom-Json
foreach ($rule in $definition.rules) {
  $text = $rule | ConvertTo-Json -Depth 40 -Compress
  $needsCloudWatch = $text -match '__DS_CW__'
  $needsInstance = $text -match '__INSTANCE_ID__'
  if ($needsCloudWatch -and -not $cw) { Write-Warning "Skipped '$($rule.title)': needs a CloudWatch data source."; continue }
  if ($needsInstance -and -not $InstanceId) { Write-Warning "Skipped '$($rule.title)': pass -InstanceId."; continue }
  if ($InstanceId -and $InstanceId -notmatch '^i-[0-9a-f]+$') { throw 'InstanceId must look like i-0123456789abcdef0' }
  $text = $text.Replace('__PROJECT__', $ProjectName).Replace('__DS_PROM__', $prom.uid)
  if ($cw) { $text = $text.Replace('__DS_CW__', $cw.uid) }
  if ($InstanceId) { $text = $text.Replace('__INSTANCE_ID__', $InstanceId) }
  # X-Disable-Provenance keeps the rules editable in the Grafana screen.
  $existing = Invoke-Grafana -Method GET -Path "/api/v1/provisioning/alert-rules/$($rule.uid)" -Tolerate 404
  if ($existing) {
    $null = Invoke-Grafana -Method PUT -Path "/api/v1/provisioning/alert-rules/$($rule.uid)" -RawBody $text -Extra @{ 'X-Disable-Provenance' = 'true' }
    Write-Host "Alert rule updated: $($rule.title)"
  } else {
    $null = Invoke-Grafana -Method POST -Path '/api/v1/provisioning/alert-rules' -RawBody $text -Extra @{ 'X-Disable-Provenance' = 'true' }
    Write-Host "Alert rule created: $($rule.title)"
  }
}

Write-Host ''
Write-Host "Done. Open the Lab overview: $GrafanaUrl/d/ai-lab-overview"
Write-Host 'Set grafana_dashboard_url to that address in terraform.tfvars to add the link to the control panel.'
