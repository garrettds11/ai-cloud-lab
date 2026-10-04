# Dashboards

A clickable mock of the AI Cloud Lab control panel. It is one page with four views: **Instances**, **Logins**, **Logs** and **User management**. It uses made-up data and does not call AWS.

Open it from PowerShell:

```powershell
Start-Process C:\GitHub\ai-cloud-lab\dashboards\index.html
```

There is no build step. The page works from a plain file path.

## Who sees what

| Person | Cognito group | What they see |
|---|---|---|
| Demo users 1, 3, 5, 7, 9 | `operators` | The instances an admin granted them, with Start and Access |
| Demo users 2, 4, 6, 8, 10 | none | "You have no instances available for edit." |
| Administrator | `user_mgrs` | The same pages, plus **User management** |

An operator needs both the group and a grant: the `operators` group says they may start instances, and a grant in `instance_entitlements` says which ones. Terraform creates the two groups and places only the demo users in them (odd users in `operators`, the demo administrator in `user_mgrs`). Everyone else is managed in the panel. See `SETUP.md`.

## Try it

The yellow strip at the top is for the mock only.

- **Sign in as** switches between the demo users and the administrator.
- **Move clock +10 min** moves the mock clock so you can watch a countdown run down, the warning turn amber, and an instance stop.
- **Auto-stop view** shows the states you rarely see: auto-stop off, idle monitor silent, activity unknown.
- **Reset demo** puts everything back.

The mock starts `ai-lab-gpu` with a capacity failure the first time, so you can see the red state and Retry. Starting an instance takes about ten seconds: starting, then running with checks pending, then ready.

## Files

| File | Purpose |
|---|---|
| `index.html` | The page shell |
| `styles.css` | Docker Desktop style, dark, desktop only (1024 px and up) |
| `app.js` | The four views |
| `mock-api.js` | The stand-in Control API, and where the access rules live |
| `SETUP.md` | What is built by hand outside Terraform, and what the panel depends on |

## Moving to the real API

`app.js` only calls the functions on `window.MockApi`. To go live, replace `mock-api.js` with a module that offers the same calls and checks the same rules on the server: group membership from the sign-in token, a grant before every Start, and answers filtered to the signed-in user. Save on User management must change entitlements only, never the instance or any AWS permission.

## Not decided yet

- The service URL and health-check path. The Access button opens `https://aiwebdemo.click` for now.
- Whether auto-stop rules are per project (today's SSM parameter) or per instance. The mock gives each instance its own.
- ALB connections in the logs.
