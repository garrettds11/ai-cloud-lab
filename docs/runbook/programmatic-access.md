# Programmatic access (calling the lab's API from a script)

How a person on the team calls the lab's Open WebUI API from their own scripts or agents, for
example from PowerShell. This is **internal use**: each person uses their own Open WebUI API key,
and each person starts and stops their lab through the control panel. It is not a public or
customer-facing API.

Everything here is **Local Windows PowerShell**. Steps 1 and 2 are done once; step 3 is what you
repeat.

## What you need

| Item | Who sets it up | Where |
|---|---|---|
| API keys turned **On**, and the allowed routes | An administrator, once per lab | Control panel, Open WebUI, Features |
| An Open WebUI API key | Each person, for themselves | Open WebUI, Settings, Account, API keys |
| A Cloudflare Access service token | An administrator, **one per person** | [Cloudflare Access (demo only)](#cloudflare-access-demo-only) |
| `scripts\ailab-api.ps1` | Already in the repository | Run from the repository root |

## 1. Turn on API keys (administrator)

In the control panel, open **Open WebUI**, then **Features**:

1. Set **API keys** to **On**. Confirm the change.
2. In **Routes API keys may call**, enter only the routes a script needs, one per line:

   ```
   /api/chat/completions
   /api/models
   ```

   Leaving the list narrow means a leaked key cannot reach the rest of the API.

The panel keeps these settings and puts them back after every instance start, like its other
Open WebUI settings. Until they are saved here the page shows "Not set from here yet".

## 2. Create your own API key

1. Sign in to Open WebUI (the lab must be running).
2. Open **Settings**, then **Account**, then **API keys**, and create a new secret key.
3. Copy it now. Open WebUI shows it once.

Each key belongs to one person, so the lab's usage figures and spend caps can tell people apart.

## 3. Call the API

Set your values for the current window (nothing is written to disk):

```powershell
$env:AILAB_URL = "https://aiwebdemo.click"
$env:AILAB_KEY = "sk-..."
```

Start the lab from the control panel if it is stopped, then:

```powershell
.\scripts\ailab-api.ps1 -ListModels
.\scripts\ailab-api.ps1 "Explain a security group in two sentences."
$answer = .\scripts\ailab-api.ps1 "Hello" -Model "<a model id from -ListModels>"
```

The script does not start the lab. If the lab is stopped or still loading, it tells you once and
then retries every 15 seconds for up to 10 minutes (`-WaitMinutes`), so you can press Start in the
panel while it waits.

The first request after a start can be slow while the model loads into memory. The script allows
five minutes for a reply (`-TimeoutSec`); if Cloudflare gives up first it reports a 524, and running
the command again a minute later normally works.

To call the API without the script, any client works with the same two parts: the header
`Authorization: Bearer <your key>`, and a standard OpenAI-style request to
`$env:AILAB_URL/api/chat/completions`.

## Cloudflare Access (demo only)

> This whole section exists only while the lab sits behind Cloudflare Access. When Cloudflare is
> removed, delete this section; the rest of the page does not depend on it.

Cloudflare Access answers non-browser clients with a sign-in page, so a script needs a **service
token**. Create **one per person** so you can revoke one without touching the others.

**Administrator, once per person**

1. Cloudflare dashboard, **Zero Trust**, **Access**, **Service Auth**, **Service Tokens**: create a
   token named for the person. Copy the Client ID and Client Secret now; the secret is shown once.
2. Open the lab's Access application, **Policies**, and add a policy with the action
   **Service Auth** that includes that token.

**The person**

```powershell
$env:CF_ACCESS_CLIENT_ID     = "<client id>.access"
$env:CF_ACCESS_CLIENT_SECRET = "<client secret>"
```

`ailab-api.ps1` adds the two headers `CF-Access-Client-Id` and `CF-Access-Client-Secret` whenever
both variables are set, and sends nothing extra when they are not. Background:
[Cloudflare security settings](../../cloudflare-and-domain-requirements.md#cloudflare-security-settings-configured-manually).

**When Cloudflare is removed:** delete this section, unset the two variables, delete the marked
"Cloudflare Access (demo only)" block in `scripts\ailab-api.ps1`, and remove the service tokens and
Service Auth policies from Cloudflare.

## Check that it works

1. `.\scripts\ailab-api.ps1 -ListModels` prints at least one model id.
2. A short prompt returns text.
3. In the control panel's **Usage** page, your tokens for the call appear under your name.
   If an API call does not show there, usage and spend caps are not counting script traffic and
   this needs a follow-up.

## When it does not work

| What you see | Likely cause | Fix |
|---|---|---|
| "answering yet" for the whole wait | The lab is stopped or still starting | Start it in the control panel, then wait for the phase to reach ready |
| 401 | Wrong or revoked key, or API keys are Off | Check `AILAB_KEY`; check Features, API keys is On |
| 403 | Route not in the allowed list, or Access refused the service token | Add the route in Features; check the service token and its policy |
| "web page, not the API" or a sign-in redirect | Cloudflare Access is asking for a login | Set both `CF_ACCESS_CLIENT_*` variables |
| 524 | The model was still loading | Run it again after a minute |
| "no model called ..." | Typo, or the model is not pulled | Use an id from `-ListModels`; pull the model |
