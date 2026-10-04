# Control panel

The AI Cloud Lab control panel: one page with four views, **Instances**, **Logins**, **Logs** and **User management**. A customer signs in, starts the instances they have been granted, and opens the service when it is ready. They cannot stop anything. An administrator manages who may start what, and which roles people have.

It is plain HTML, CSS and JavaScript with no build step. The page holds no permissions of its own: it shows what the Control API returns, and the API checks every rule again.

Live at `https://cp.aiwebdemo.click`. Setup of everything it needs is in [`SETUP.md`](SETUP.md).

## Who sees what

| Person | Role (panel's own table) | What they see |
|---|---|---|
| Customer with a grant | `operators` | Their instances, with Start and, once ready, Access |
| A user with no role, or no grants | none | "You have no instances available for edit." |
| User manager | `user_mgrs` (usually with `operators`) | The same pages, plus **User management**, where they change who may start which instance |
| Administrator | `admin` (any number of people) | The same pages, and on **User management** they also change roles, including who else is an administrator |

A customer needs both the `operators` role and a grant in `instance_entitlements`. Roles live in the panel's own `panel_users` table, not in Cognito. When `control_panel_users_table` is set, Terraform adds only the demo users to that table: every demo user as `operators` (may launch), with `user_mgrs` added for the odd demo users and `admin` for `admin@example.local`. Only an administrator changes roles. Everyone else is managed on the User management page. With no users in the table the panel shows no users, and only the address in the API's `BOOTSTRAP_ADMINS` setting can open User management.

## Files

| File | Purpose |
|---|---|
| `index.html` | The page shell |
| `styles.css` | Docker Desktop style, dark, desktop only (1024 px and up) |
| `app.js` | The four views |
| `api.js` | OpenID Connect sign-in (authorization code with PKCE, any provider) and calls to the Control API |
| `config.js` | Region, user pool ID, app client ID and API address. Published to the bucket by Terraform on apply; a local copy is not committed |
| `config.example.js` | Shows what `config.js` looks like |
| `api/` | The Control API Lambda, its tests and its setup guide |
| `SETUP.md` | What is built by hand outside Terraform |

## Run it

The page signs in through the provider named in `config.js` (Okta, Entra ID, Cognito or any OIDC provider), which sends the browser back to `https://cp.aiwebdemo.click/`, so it works from that address. Opening `index.html` from a file shows the page without settings or sign-in.

1. Build what `SETUP.md` lists.
2. Upload the pages (command in `SETUP.md`). It leaves `config.js` alone.
3. Apply Terraform. It publishes `config.js` and clears it from the CloudFront cache.

## Not decided yet

- The service URL and health-check path. The Access button opens `SERVICE_URL`, set on the API, and ready means the EC2 status checks pass and the ALB target is healthy.
- Whether auto-stop rules are per project (today's SSM parameter) or per instance. The API reads the one project-wide setting.
- ALB connections in the logs.
