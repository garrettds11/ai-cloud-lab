<#
.SYNOPSIS
  Call the lab's Open WebUI API from PowerShell: wait until the lab answers, then list models or
  send one chat message.

.DESCRIPTION
  Uses your own Open WebUI API key (Settings, Account, API keys). The lab must be running; this
  script does not start it. Start it from the control panel. If the lab is stopped or still
  loading, the script waits and tries again (up to -WaitMinutes) instead of failing at once.

  Settings come from environment variables so no secret is typed on the command line:
    AILAB_URL   The lab address, for example https://aiwebdemo.click
    AILAB_KEY   Your Open WebUI API key (starts with sk-)

  The reply text is written to the output, so it can be captured: $answer = .\ailab-api.ps1 "Hi"

.PARAMETER Prompt
  The message to send. Leave out with -ListModels.

.PARAMETER Model
  A model id from -ListModels. Default: the first model the lab lists.

.PARAMETER System
  An optional system message sent before the prompt.

.PARAMETER ListModels
  Print the model ids the lab offers and stop.

.PARAMETER WaitMinutes
  How long to wait for a stopped or starting lab to answer. Default 10. Use 0 to try once.

.PARAMETER TimeoutSec
  How long to wait for one chat reply. Default 300.

.PARAMETER Raw
  Print the full JSON reply instead of just the message text.

.EXAMPLE
  .\scripts\ailab-api.ps1 -ListModels

.EXAMPLE
  .\scripts\ailab-api.ps1 "Summarise what a security group does in two sentences."
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][string]$Prompt,
  [string]$Model,
  [string]$System,
  [switch]$ListModels,
  [string]$BaseUrl = $env:AILAB_URL,
  [string]$Key = $env:AILAB_KEY,
  [int]$WaitMinutes = 10,
  [int]$TimeoutSec = 300,
  [switch]$Raw
)

$ErrorActionPreference = 'Stop'

if (-not $BaseUrl) { throw 'Set AILAB_URL to the lab address, for example: $env:AILAB_URL = "https://aiwebdemo.click"' }
if (-not $Key) { throw 'Set AILAB_KEY to your Open WebUI API key (Settings, Account, API keys).' }
if (-not $ListModels -and -not $Prompt) { throw 'Give a prompt, or use -ListModels.' }
$BaseUrl = $BaseUrl.TrimEnd('/')

$headers = @{ Authorization = "Bearer $Key" }

# --- Cloudflare Access (demo only) -----------------------------------------------------------
# While the lab sits behind Cloudflare Access, a script needs a service token. Delete this block
# (and the two environment variables) when Cloudflare is removed; nothing else depends on it.
if ($env:CF_ACCESS_CLIENT_ID -and $env:CF_ACCESS_CLIENT_SECRET) {
  $headers['CF-Access-Client-Id'] = $env:CF_ACCESS_CLIENT_ID
  $headers['CF-Access-Client-Secret'] = $env:CF_ACCESS_CLIENT_SECRET
}
# --- end Cloudflare Access -------------------------------------------------------------------

function Get-StatusCode($ErrorRecord) {
  $response = $ErrorRecord.Exception.Response
  if ($response -and $response.StatusCode) { return [int]$response.StatusCode }
  return 0   # no answer at all: timeout or connection refused
}

function Invoke-Lab([string]$Method, [string]$Path, [string]$Json, [int]$Seconds) {
  $request = @{
    Uri                = "$BaseUrl$Path"
    Method             = $Method
    Headers            = $headers
    TimeoutSec         = $Seconds
    MaximumRedirection = 0       # a redirect means a sign-in page, which a script cannot use
    UseBasicParsing    = $true
  }
  if ($Json) {
    $request.Body = [Text.Encoding]::UTF8.GetBytes($Json)   # bytes, so Windows PowerShell keeps UTF-8
    $request.ContentType = 'application/json'
  }
  try {
    $reply = Invoke-WebRequest @request
    return @{ Status = [int]$reply.StatusCode; Content = $reply.Content }
  }
  catch {
    return @{ Status = (Get-StatusCode $_); Content = $null; Message = $_.Exception.Message }
  }
}

function Test-Refused($Result) {
  switch ($Result.Status) {
    401 { throw 'The lab refused the API key (401). Check AILAB_KEY, and that API keys are On in the control panel (Open WebUI, Features).' }
    403 { throw 'Refused (403). Either the route is not in the panel''s "Routes API keys may call" list, or Cloudflare Access did not accept the service token.' }
    { $_ -in 301, 302, 303, 307, 308 } { throw 'The lab sent a sign-in redirect. Cloudflare Access is asking for a login: set CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET (see docs\runbook\programmatic-access.md).' }
  }
}

function Get-Models {
  $deadline = (Get-Date).AddMinutes($WaitMinutes)
  $told = $false
  while ($true) {
    $result = Invoke-Lab 'GET' '/api/models' $null 30
    Test-Refused $result
    if ($result.Status -eq 200) {
      if ($result.Content -notmatch '^\s*[\{\[]') {
        throw 'The lab answered with a web page, not the API. Cloudflare Access is probably in the way: see docs\runbook\programmatic-access.md.'
      }
      return ($result.Content | ConvertFrom-Json)
    }
    if ((Get-Date) -ge $deadline) {
      throw "The lab did not answer within $WaitMinutes minute(s) (last status $($result.Status)). Is it running? Start it from the control panel."
    }
    if (-not $told) {
      Write-Host "The lab is not answering yet (status $($result.Status)). If it is stopped, start it from the control panel. Waiting up to $WaitMinutes minute(s)..." -ForegroundColor Yellow
      $told = $true
    }
    Start-Sleep -Seconds 15
  }
}

$models = Get-Models
$ids = @($models.data | ForEach-Object { $_.id })

if ($ListModels) { $ids; return }
if (-not $ids.Count) { throw 'The lab lists no models. Pull a model first (see the runbook).' }
if (-not $Model) { $Model = $ids[0] }
elseif ($ids -notcontains $Model) { throw "The lab has no model called '$Model'. Models: $($ids -join ', ')" }

$messages = @()
if ($System) { $messages += @{ role = 'system'; content = $System } }
$messages += @{ role = 'user'; content = $Prompt }
$json = @{ model = $Model; messages = $messages; stream = $false } | ConvertTo-Json -Depth 6 -Compress

$result = Invoke-Lab 'POST' '/api/chat/completions' $json $TimeoutSec
Test-Refused $result
switch ($result.Status) {
  200 { }
  524 { throw 'Cloudflare gave up waiting (524). The model is probably still loading into memory. Wait a minute and run it again.' }
  0   { throw "No reply within $TimeoutSec seconds ($($result.Message)). The model may still be loading; try again, or raise -TimeoutSec." }
  default { throw "The lab answered $($result.Status). $($result.Message)" }
}

if ($Raw) { $result.Content; return }
($result.Content | ConvertFrom-Json).choices[0].message.content
