# Control panel

The AI Cloud Lab control panel: one page with four views, **Instances**, **Logins**, **Logs** and **User management**. A customer signs in, starts the instances they have been granted, and opens the service when it is ready. They cannot stop anything. An administrator manages who may start what, and which groups people are in.

It is plain HTML, CSS and JavaScript with no build step. The page holds no permissions of its own: it shows what the Control API returns, and the API checks every rule again.

Live at `https://cp.aiwebdemo.click`. Setup of everything it needs is in [`SETUP.md`](SETUP.md).

## Who sees what

| Person | Cognito group | What they see |
|---|---|---|
| Customer with a grant | `operators` | Their instances, with Start and, once ready, Access |
| A user with no role, or no grants | none | "You have no instances available for edit." |
| Administrator | `user_mgrs` | The same pages, plus **User management** |

A customer needs both the `operators` group and a grant in `instance_entitlements`. Terraform creates the two groups and puts only the demo users in them: the odd demo users in `operators`, `admin@example.local` in `user_mgrs`. Everyone else is managed on the User management page.

## Files

| File | Purpose |
|---|---|
| `index.html` | The page shell |
| `styles.css` | Docker Desktop style, dark, desktop only (1024 px and up) |
| `app.js` | The four views |
| `api.js` | Cognito sign-in (authorization code with PKCE) and calls to the Control API |
| `config.js` | Region, user pool ID, app client ID and API address. Written by `scripts\make-panel-config.ps1`; not committed |
| `config.example.js` | Shows what `config.js` looks like |
| `api/` | The Control API Lambda, its tests and its setup guide |
| `SETUP.md` | What is built by hand outside Terraform |

## Run it

The page signs in through Cognito, which sends the browser back to `https://cp.aiwebdemo.click/`, so it works from that address. Opening `index.html` from a file shows the page without settings or sign-in.

1. Build what `SETUP.md` lists.
2. Apply Terraform, then write the settings: `.\scripts\make-panel-config.ps1`.
3. Upload the pages (command in `SETUP.md`).

## Not decided yet

- The service URL and health-check path. The Access button opens `SERVICE_URL`, set on the API, and ready means the EC2 status checks pass and the ALB target is healthy.
- Whether auto-stop rules are per project (today's SSM parameter) or per instance. The API reads the one project-wide setting.
- ALB connections in the logs.
