A **simplified admin control plane for Open WebUI** would materially improve the `ai-cloud-lab`/`aiwebdemo` experience.

The key is not to try to reproduce every Open WebUI admin screen. Instead, expose a curated set of **high-value, low-risk administrative operations** and leave deep platform tuning to Open WebUI itself.

Open WebUI has three main configuration mechanisms you can build against:

- documented REST APIs for models and some admin/config objects,
- persistent admin configuration stored as `ConfigVar` values,
- deployment-time environment variables for infrastructure-level settings. [Open WebUI](https://docs.openwebui.com/reference/api-endpoints/?utm_source=chatgpt.com)

## What I would put in the control panel

I would organize it into roughly six admin areas:

| Control-panel area | What it should manage | Priority |
| --- | --- | --- |
| **Models** | enable/configure models, defaults, parameters | P1 |
| **Tools & Skills** | install/import, enable, bind to models | P1 |
| **Providers / Connections** | Ollama/OpenAI-compatible/MCP/OpenAPI connections | P1 |
| **Features** | web search, code execution, image gen, APIs, plugins | P1 |
| **Users & Access** | roles, signup, groups, API keys | P2 |
| **System / Diagnostics** | version, health, configuration summary, logs | P2 |

That gives a non-expert admin almost everything they would routinely need without sending them through several different Open WebUI menus.

---

# 1. Models should definitely be first-class

Open WebUI now has quite a useful model-management API.

For example:

```
GET  /api/v1/models/all
POST /api/v1/models/create
POST /api/v1/models/model/update
POST /api/v1/models/model/delete
POST /api/v1/models/import
POST /api/v1/models/sync
```

The particularly interesting endpoint for the control panel is:

```
POST /api/v1/models/sync
```

That is effectively a **desired-state reconciliation API**: send the desired model configurations, and Open WebUI creates, updates, or removes models to match. It is admin-only. [Open WebUI](https://docs.openwebui.com/reference/api-endpoints/?utm_source=chatgpt.com)

That lends itself very nicely to a UI like:

```
Models
───────────────────────────────────────────

✓ qwen3.6:27b        Enabled
✓ llama3.3:70b       Enabled
○ deepseek-coder     Disabled

Default model:
[ qwen3.6:27b ▼ ]

Default Function Calling:
[ Native ▼ ]

Default Temperature:
[ 0.7 ]

[ Apply Configuration ]
```

Open WebUI also supports global **Model Defaults** and per-model overrides for capabilities like Tools, Skills, Web Search, Code Interpreter, Vision, and parameters. [Open WebUI](https://docs.openwebui.com/features/workspace/models/?utm_source=chatgpt.com)

So this could be one of the best abstractions in the control panel.

---

# 2. Tools and Skills absolutely belong there

I would expose them slightly differently.

### Skills

Think of Skills as managed content:

```
Skills
──────────────────────────────────────────

AWS Operations       v1.2     ✓ Enabled
Terraform Review     v1.0     ✓ Enabled
Cyber Analysis       v2.3     ✓ Enabled

[ Import Skill ]
[ View ]
[ Replace ]
[ Disable ]
```

I would avoid making a nontechnical administrator edit raw Markdown unless they explicitly select an advanced mode.

Instead:

```
Import Skill
──────────────────
Name:
[ Terraform Review ]

Description:
[ Reviews Terraform plans and changes ]

File:
[ Choose SKILL.md ]

Attach to:
☑ Infrastructure Model
☐ General Model

[ Install ]
```

### Tools

Tools deserve an even stronger warning because Workspace Tools execute Python directly in the Open WebUI process.

Open WebUI explicitly warns that giving someone permission to create/import Workspace Tools is effectively giving them arbitrary code execution on the server. [Open WebUI](https://docs.openwebui.com/features/extensibility/plugin/tools/?utm_source=chatgpt.com)

So I would differentiate:

```
Tools
────────────────────────────────────────

EXTERNAL
✓ AWS Inventory MCP
✓ GitHub OpenAPI
✓ Terraform MCP

LOCAL CODE
⚠ Python Infrastructure Tool
⚠ Security Utility Tool
```

And require an extra confirmation for local Workspace Tools.

---

# 3. Provider and tool-server connections

This is probably another **P1 capability**.

An admin should be able to see:

```
AI Providers
───────────────────────────────────

Ollama
http://ollama:11434
● Connected

OpenAI-Compatible
https://api.example.local/v1
● Connected

[ Test Connection ]
[ Edit ]
```

And separately:

```
Tool Servers
───────────────────────────────────

AWS MCP        ● Connected
Splunk MCP     ● Connected
Internal API   ● Connected
```

Open WebUI treats MCP, OpenAPI, Ollama, and OpenAI-compatible provider connections as instance-level administration. [Open WebUI](https://docs.openwebui.com/getting-started/quick-start/settings/?utm_source=chatgpt.com)

I would make **Test Connection** especially prominent.

A non-expert admin often doesn't need to know what an HTTP 401, DNS timeout, SSE problem, or malformed OpenAPI document means.

The control panel could translate that into:

```
AWS MCP
✓ Server reachable
✓ Authentication valid
✓ 12 tools discovered
✓ Ready
```

That alone would add a lot of value.

---

# 4. Feature switches

This is where the control panel could hide a huge amount of Open WebUI complexity.

Open WebUI exposes many capabilities as admin-configurable settings, such as:

```
Web Search
Code Interpreter
Image Generation
Plugins
API Keys
Signup
Tool Permissions
Memory
Direct Connections
```

Open WebUI's architecture distinguishes global admin settings from user-level preferences: admin settings determine what is available at all, while users can sometimes choose whether to use the enabled feature. [Open WebUI](https://docs.openwebui.com/getting-started/quick-start/settings/?utm_source=chatgpt.com)

The UI could make that much clearer:

```
AI Capabilities
────────────────────────────────────────

Web Search                [ ON ]
Code Interpreter          [ ON ]
Image Generation          [ OFF ]
Memory                    [ ON ]
Workspace Plugins         [ ON ]
API Access                [ ON ]
Tool Approval             [ OFF ]
```

I would include a little scope indicator:

```
GLOBAL
```

versus:

```
USER DEFAULT
```

because that distinction isn't always obvious in Open WebUI.

---

# 5. API administration

Thinking of automation, let's expose API controls.

Open WebUI supports API keys, but they must first be globally enabled. We can also restrict which API routes API keys may access. [Open WebUI](https://docs.openwebui.com/features/authentication-access/api-keys/?utm_source=chatgpt.com)

So something like:

```
API Access
──────────────────────────────────────────

Open WebUI API              [ Enabled ]

Restrict API-key endpoints  [ Enabled ]

Allowed:
✓ /api/chat/completions
✓ /api/v1/models
✓ /api/v1/tools
○ /api/v1/users

[ Generate Admin Service Key ]
```

Although I would **not display an existing secret again** after initial creation.

For `aiwebdemo`, I would actually favor a dedicated service credential for the control panel rather than reusing a human admin's credential.

---

# 6. Users and access

This should probably be deliberately simple.

Open WebUI has roles:

```
admin
user
pending
```

plus groups and resource-level permissions. [Open WebUI](https://docs.openwebui.com/getting-started/advanced-topics/hardening/?utm_source=chatgpt.com)

The control panel could expose:

```
Users
─────────────────────────────────────────

Alice       Admin
Bob         User
Charlie     Pending

New registrations       [ OFF ]
Default new-user role    [ Pending ▼ ]

[ Approve Pending Users ]
[ Manage Groups ]
```

Then provide an **Advanced** link into Open WebUI itself for more detailed access-control work.

I would not initially attempt to reproduce the entire RBAC system.

---

# 7. Configuration backup/export

This would be one of my favorite additions.

Give the admin:

```
Configuration
──────────────────────────────────────

[ Export Configuration ]
[ Import Configuration ]

Last Export:
2026-10-05 15:04
```

Open WebUI supports JSON import/export for some resources, and persistent configuration itself can also be manipulated through configuration endpoints. The current docs specifically reference:

```
POST /api/v1/configs/import
```

for changing persisted `ConfigVar` settings in some cases. [Open WebUI](https://docs.openwebui.com/reference/env-configuration/?utm_source=chatgpt.com)

I'd eventually let the control panel produce something like:

```
{
  "version": 1,
  "models": {},
  "skills": {},
  "tools": {},
  "providers": {},
  "features": {},
  "permissions": {}
}
```

That starts turning the lab into a **repeatable appliance** rather than a hand-configured UI.

---

# Important distinction: API vs environment variables

This is the architectural piece I'd be careful about.

Open WebUI has more than 200 environment variables, but many are `ConfigVar` settings. On first startup, the environment initializes them; afterward, persistent database values can override the environment. [Open WebUI](https://docs.openwebui.com/reference/env-configuration/?utm_source=chatgpt.com)

So it effectively has:

```
                  Open WebUI configuration
                           │
             ┌─────────────┴─────────────┐
             │                           │
       Deployment config             Runtime config
             │                           │
     Terraform / Docker             Open WebUI API
     environment vars                 / database
             │                           │
             ▼                           ▼
        boot settings              day-2 settings
```

For the project, we could establish this rule:

> **Terraform owns infrastructure settings. The control panel owns operational Open WebUI settings.**

For example:

### Terraform-owned

```
Container image
CPU / memory
networking
storage
WEBUI_URL
database location
TLS
SSO bootstrap
secret sources
```

### Control-panel-owned

```
models
model defaults
skills
tools
MCP servers
OpenAPI servers
feature toggles
API access
signup policy
user approvals
```

That prevents Terraform and the control panel from constantly fighting over persistent configuration.

---

# There is an admin-config API

There is evidence in the current docs of an administrative configuration endpoint:

```
POST /api/v1/auths/admin/config
```

For example, Open WebUI documents using it for UI translation configuration. [Open WebUI](https://docs.openwebui.com/features/administration/translations/?utm_source=chatgpt.com)

There is also:

```
POST /api/v1/configs/import
```

for persisted configuration. [Open WebUI](https://docs.openwebui.com/reference/env-configuration/?utm_source=chatgpt.com)

However, I **wouldn't design the control panel around blindly posting arbitrary configuration dictionaries**.

Instead, write a small adapter layer:

```
Browser Control Panel
        │
        ▼
aiwebdemo control API
        │
        ├── get_models()
        ├── set_default_model()
        ├── install_skill()
        ├── configure_mcp()
        ├── enable_web_search()
        ├── approve_user()
        └── health_check()
                 │
                 ▼
           Open WebUI API
```

That buys version isolation.

If Open WebUI changes:

```
/api/v1/foo
```

to:

```
/api/v2/foo
```

Update one server-side adapter rather than rebuilding the whole UI.

---

# What I would build first

I think the first admin-control-panel release should be deliberately constrained to about **10 operations**:

1. View Open WebUI health/version.
2. List available models.
3. Set selected/default model.
4. Configure basic model parameters.
5. Add/import a Skill.
6. Add an MCP/OpenAPI Tool Server.
7. Enable/disable core features.
8. View/test provider connections.
9. Enable/restrict API access.
10. Export the effective configuration.

Those cover the majority of day-to-day setup without exposing dangerous internals.

Then Phase 2 could add:

```
users/groups
model synchronization
knowledge collections
Workspace Tools
Functions
SSO
branding/interface defaults
configuration import/restore
```

## I would actually avoid one feature initially

I **would not expose arbitrary Workspace Tool creation through the simple admin UI**.

Because:

```
"Add Skill"
```

means:

> add instructions.

While:

```
"Add Workspace Tool"
```

can mean:

> execute arbitrary Python inside the Open WebUI server.

Open WebUI's own security guidance treats that as essentially shell-level trust. [Open WebUI](https://docs.openwebui.com/features/extensibility/plugin/tools/?utm_source=chatgpt.com)

I'd let the simplified interface add:

- Skills
- MCP servers
- OpenAPI servers

and put Workspace Python Tools behind something explicitly labeled:

```
Advanced / Developer Administration
```

That would be a much safer product boundary.

For `aiwebdemo`, this would evolve the existing control panel from an **EC2/lab lifecycle controller** into a genuine **AI platform administration console**: start the environment, reset its timer, open Open WebUI, select/configure models, connect tools, install Skills, test connections, and inspect health without requiring the operator to understand all of Open WebUI's menus.