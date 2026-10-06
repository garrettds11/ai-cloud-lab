# Model Calls via API
---

Inside Open WebUI, you can call any configured model through Open WebUI’s own **OpenAI-compatible API**. The main endpoint is:

```
POST /api/chat/completions
```

It works for models connected through OpenAI-compatible providers, Ollama, and Open WebUI Function/Pipe models. [Open WebUI](https://docs.openwebui.com/reference/api-endpoints/?utm_source=chatgpt.com)

The basic flow is:

1. Add/configure the model in Open WebUI.
2. Create an Open WebUI API key.
3. Call `/api/chat/completions` with the model ID.
4. Authenticate using `Authorization: Bearer <API_KEY>`.

For example:

```
curl http://YOUR-OPEN-WEBUI/api/chat/completions \
  -H "Authorization: Bearer YOUR_OPENWEBUI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.6:27b",
    "messages": [
      {
        "role": "user",
        "content": "Explain what this server does."
      }
    ]
  }'
```

That endpoint intentionally looks very similar to the OpenAI Chat Completions API. [Open WebUI](https://docs.openwebui.com/reference/api-endpoints/?utm_source=chatgpt.com)

In Python:

```
import requestsOPENWEBUI_URL = "http://localhost:3000"API_KEY = "YOUR_OPENWEBUI_API_KEY"response = requests.post(    f"{OPENWEBUI_URL}/api/chat/completions",    headers={        "Authorization": f"Bearer {API_KEY}",        "Content-Type": "application/json",    },    json={        "model": "qwen3.6:27b",        "messages": [            {                "role": "user",                "content": "Summarize the purpose of this application."            }        ],    },    timeout=60,)response.raise_for_status()result = response.json()print(result["choices"][0]["message"]["content"])
```

### Find the model ID first

You should not necessarily assume the display name in the UI is the API model ID. Open WebUI exposes model/API endpoints, and its provider architecture is based heavily around standard OpenAI-compatible model identifiers. [Open WebUI](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/?utm_source=chatgpt.com)

A useful first test is therefore:

```
curl http://YOUR-OPEN-WEBUI/api/models \
  -H "Authorization: Bearer YOUR_OPENWEBUI_API_KEY"
```

Then use the returned `id` as:

```
{
  "model": "the-model-id"
}
```

Depending on your version/configuration, model discovery routes can vary, but `/api/chat/completions` is the documented chat API endpoint.

### Calling a model from an Open WebUI Tool

This is probably particularly relevant to what you were asking earlier.

Say you create an Open WebUI Tool and want that tool to invoke another model. Architecturally:

```
User
  │
  ▼
Primary Model
  │
  │ calls
  ▼
Open WebUI Tool
  │
  │ HTTP POST
  ▼
/api/chat/completions
  │
  ▼
Secondary Model
```

For example, the Tool could contain logic equivalent to:

```
import requestsdef call_model(    base_url: str,    api_key: str,    model: str,    prompt: str):    response = requests.post(        f"{base_url}/api/chat/completions",        headers={            "Authorization": f"Bearer {api_key}",            "Content-Type": "application/json",        },        json={            "model": model,            "messages": [                {                    "role": "user",                    "content": prompt                }            ]        },        timeout=60    )    response.raise_for_status()    data = response.json()    return data["choices"][0]["message"]["content"]
```

That allows you to build things such as:

```
Primary conversational model
        │
        ├── Threat Intel Tool
        │
        ├── AWS Tool
        │
        └── specialist_llm()
                  │
                  ▼
             Coding Model
```

or:

```
User
 │
 ▼
General LLM
 │
 └── analyze_security_event()
              │
              ▼
        Cybersecurity LLM
              │
              ▼
        structured result
```

There is one architectural caution here: **don't blindly let models recursively call Open WebUI models**. You want a bounded call chain such as:

```
Model A
   │
   └─ Tool
       │
       └─ Model B
```

rather than:

```
Model A
 ↓
Model B
 ↓
Model A
 ↓
Model B
 ↓
...
```

because you'll otherwise create runaway inference, token usage, and possibly tool loops.

### If you're building an internal service

Another good pattern for `aiwebdemo` is to treat Open WebUI itself as your **LLM gateway**:

```
┌──────────────────────────────┐
│        Your Application      │
│                              │
│ Python / JS / API service    │
└──────────────┬───────────────┘
               │
               │ OpenAI-compatible request
               ▼
┌──────────────────────────────┐
│         Open WebUI           │
│                              │
│ POST /api/chat/completions   │
└──────────────┬───────────────┘
               │
       model routing/config
               │
       ┌───────┼──────────┐
       ▼       ▼          ▼
    Ollama   OpenAI     Claude
       │                  │
       └──── etc. ─────────┘
```

Your application then doesn't necessarily need to know whether the backend is Ollama, OpenAI, Mistral, or another OpenAI-compatible provider. Open WebUI can sit in front of the configured models. Open WebUI describes its API as a REST interface suitable for automations, chatbots, and custom integrations. [Open WebUI](https://docs.openwebui.com/reference/?utm_source=chatgpt.com)

### You can also use the OpenAI SDK

Because the interface is OpenAI-compatible, you can often point an OpenAI-compatible client at Open WebUI rather than manually building HTTP requests.

Conceptually:

```
from openai import OpenAIclient = OpenAI(    base_url="http://localhost:3000/api",    api_key="YOUR_OPENWEBUI_API_KEY")response = client.chat.completions.create(    model="your-model-id",    messages=[        {            "role": "user",            "content": "Explain this log event."        }    ])print(response.choices[0].message.content)
```

That is one of the biggest benefits of Open WebUI's protocol-oriented architecture: it uses the OpenAI Chat Completions convention as a common interface for many providers. [Open WebUI](https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/?utm_source=chatgpt.com)

### And this works with Tools

The API isn't limited to basic text completion. Open WebUI's current `/api/chat/completions` endpoint also supports tool-related behavior. You can provide OpenAI-style `tools`, or use Open WebUI tool IDs so Open WebUI can resolve and execute configured tools. [Open WebUI](https://docs.openwebui.com/reference/api-endpoints/?utm_source=chatgpt.com)

That means you can eventually build something like:

```
Your Python App
       │
       ▼
Open WebUI API
       │
       ▼
Model
       │
       ├── MCP Tool
       ├── OpenAPI Tool
       ├── Workspace Tool
       └── Skill instructions
```

So the API caller can get substantially more than simply "send prompt → get text."

For **your `aiwebdemo` architecture**, I'd probably standardize around this model:

```
application code
       │
       ▼
Open WebUI /api/chat/completions
       │
       ├── model selection
       ├── Skills
       ├── Tools
       ├── MCP
       └── provider routing
              │
              ▼
           actual LLM
```

That keeps your application from becoming tightly coupled to Ollama, OpenAI, Anthropic, Bedrock gateways, etc., and gives you one internal API contract to document.