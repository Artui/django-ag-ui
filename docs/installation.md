# Installation

```bash
pip install django-ag-ui
```

Core dependencies are `django>=4.2`, `django-spec-agents>=0.1.0`,
`pydantic-ai-slim[ui]>=2.37,<3` and `ag-ui-protocol>=1.0`. The `AGUIAdapter` comes from pydantic-ai-slim and the
AG-UI wire types from `ag-ui-protocol`; this package does not re-implement
either. The **slim** package ships the adapter but **no model-provider
library** — pick one via a provider extra (see below).

[django-spec-agents](https://artui.github.io/django-spec-agents/) is the agent
runtime underneath: the tool registry, agent construction, the guard and failure
policies, audit and the storage contracts. This package re-exports the names you
use from it, so `from django_ag_ui import ToolRegistry` is the spelling to keep.

The `[ui]` extra rather than `[ag-ui]` is deliberate. From 2.47,
pydantic-ai-slim's `[ag-ui]` extra caps `ag-ui-protocol` below 1.0, and this
package needs 1.0, so asking for both would quietly hold pydantic-ai-slim at
2.46. If your own project depends on `pydantic-ai-slim[ag-ui]` or
`pydantic-ai[ag-ui]`, it brings the cap back, and the resolver again settles on
pydantic-ai-slim 2.46 without saying so. Depend on the `[ui]` extra instead, as
this package does.

Upgrading from 0.66 or earlier does not move pydantic-ai on its own. 2.46 still
satisfies this package's floor, so a lockfile or environment that already
resolved it keeps it until you upgrade it by name, together with
`pydantic-ai-harness` if you use the `[harness]` or `[code-mode]` extra (the
cap held it at 0.36, since every later release pins a newer pydantic-ai-slim):

```bash
uv lock --upgrade-package pydantic-ai-slim --upgrade-package pydantic-ai-harness
```

## Compatibility

| Component | Floor | Tested |
| --- | --- | --- |
| Python | 3.10 | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Django | 4.2 LTS | 4.2, 5.0, 5.1, 5.2, 6.0 |
| Pydantic-AI | 2.37 (with the `pydantic-ai-slim[ui]` extra) | latest in the CI matrix |

## Model provider extras

Because `pydantic-ai-slim` ships no provider library, install the one matching
your model via a `django-ag-ui` provider extra:

```bash
pip install "django-ag-ui[anthropic]"   # or [openai], or [google]
```

Each maps to the corresponding `pydantic-ai-slim` provider extra:

| Extra | Pulls in |
| --- | --- |
| `django-ag-ui[anthropic]` | `pydantic-ai-slim[anthropic]` |
| `django-ag-ui[openai]` | `pydantic-ai-slim[openai]` |
| `django-ag-ui[google]` | `pydantic-ai-slim[google]` |

No extra is needed to stand the endpoint up and stream from it: setting
`"MODEL": "test"` uses Pydantic-AI's built-in `TestModel`, which reaches no
provider. See
[Rehearsing the wiring before you have a key](configuration.md#rehearsing-the-wiring-before-you-have-a-key).

When you set [`API_KEY` or `provider=`](configuration.md#api_key) so the model is
built with an explicit key, the `MODEL` string's `provider:` prefix may be **any
provider Pydantic-AI knows** (`anthropic`, `openai`, `openai-responses`,
`google`, `google-gla`, `groq`, `bedrock`, …) — resolution is delegated to
Pydantic-AI, so the list tracks whatever your installed version supports. A bare
model name it can map to a provider (e.g. `claude-…`) works too. When the
provider can't be resolved, the view raises `ImproperlyConfigured` (set
`provider=` to a `Provider` instance instead). Install the matching provider
extra for whichever you use.

## ASGI is required

[`DjangoAGUIView`][django_ag_ui.DjangoAGUIView] is an **async** view that
returns a `StreamingHttpResponse` of Server-Sent Events. AG-UI's SSE streaming
needs an event loop, which the synchronous WSGI worker does not provide. Deploy
under an ASGI server such as [Uvicorn](https://www.uvicorn.org/) or
[Daphne](https://github.com/django/daphne) and point it at your project's
`asgi.py`:

```bash
uvicorn myproject.asgi:application
```

The view marks itself as a coroutine function (via
`asgiref.sync.markcoroutinefunction`) so Django's request handler awaits it when
mounted. When served over WSGI, the view emits a one-time `RuntimeWarning` to
flag that SSE streaming needs ASGI.

## Streaming through a proxy

Almost every deployment puts something between the ASGI server and the browser —
a load balancer, nginx, a CDN — and streaming has one requirement of all of
them: **do not buffer, and do not time out an idle connection.**

Buffering is handled for you. The endpoint sets `X-Accel-Buffering: no` and
`Cache-Control: no-cache` on every response, which nginx and the proxies that
copy its conventions honour.

Idle timeouts are the one that bites, because the symptom is misleading. An
agent run emits nothing while the model thinks and nothing while a slow tool
call runs. An AWS Application Load Balancer closes a connection idle for
`idle_timeout` seconds — **60 by default** — and nginx's `proxy_read_timeout`
defaults to the same. Past that the stream is closed mid-run, and it reads as
"long answers never arrive".

The endpoint prevents this by writing an SSE comment into the stream whenever it
has been silent for `HEARTBEAT_SECONDS` (default `15.0`), which a conformant
client ignores. It is on by default and needs no configuration — see
[`HEARTBEAT_SECONDS`](configuration.md#heartbeat_seconds) for the reasoning, for
when to lower it, and for why raising the load balancer's timeout instead is the
wrong fix.


## The `[drf-mcp]` extra

To expose a
[`djangorestframework-mcp-server`](https://github.com/Artui/djangorestframework-mcp-server)
tool registry to the agent in-process (no network MCP hop), install the extra:

```bash
pip install "django-ag-ui[drf-mcp]"
```

This pulls in `djangorestframework-mcp-server` (which in turn pulls
`djangorestframework-services`), at the floor `pyproject.toml` declares. The bridge
([`DRFMCPToolset`](concepts.md#the-drf-mcp-toolset-bridge)) is imported lazily,
only when a `drf_mcp_server=` is passed, so the dependency stays
optional for projects that do not use it. See
[Configuration → `drf_mcp_server=`](configuration.md#drf_mcp_server).

## The `[specs]` extra

To expose drf-services specs to the agent as tools with no MCP server in the path
(`service_specs=`), install the extra:

```bash
pip install "django-ag-ui[specs]"
```

This pulls in `django-spec-agents[specs]`, whose `django_spec_agents.spec_tools`
holds the `SpecToolset` and `SpecCapability` the endpoint builds, with
`djangorestframework-services` and DRF beneath it. Like the bridge, it is
imported lazily, only when `service_specs=` is passed. See
[Configuration → `service_specs=`](configuration.md#service_specs).

`[spec-tools]` is the extra's old name, and it still installs for this minor: it
pulls in `django-spec-agents[specs]` and `djangorestframework-pydantic-ai`, so a
project naming it keeps everything it had. A `SpecToolset` or `SpecCapability`
built from `rest_framework_pydantic_ai` is still served when passed as
`service_specs=`. The next minor removes `[spec-tools]`; move to `[specs]` and
import from `django_spec_agents.spec_tools` before then.

## Moving from django-pydantic-agent

Releases of this package up to 0.68 ran on `django-pydantic-agent` and, for
`service_specs=`, `djangorestframework-pydantic-ai`. django-spec-agents replaces
both, and a project that used either directly moves with it:

- **Imports.** `django_pydantic_agent` becomes `django_spec_agents`, and
  `rest_framework_pydantic_ai` becomes `django_spec_agents.spec_tools`. Names
  imported from `django_ag_ui` need no edit.
- **The reference store app.** If `INSTALLED_APPS` lists it, the entry becomes
  `"django_spec_agents.contrib.store"`. Its label follows the package, so run
  `migrate` once: its one migration renames the store's tables in place, and it
  needs `django-pydantic-agent`'s own store migrations applied first, so migrate
  on the old release before you upgrade if yours is behind.
- **Registered names.** The agent offers a registry tool under the name it was
  registered with (`@tool(registry, name="...")`), where it used to offer the
  function's own name. Anything that named such a tool by its function name,
  `REQUIRE_APPROVAL`, `EXEMPT`, `approval_prompts` or an audit query, uses the
  registered name now.
- **Names that keep their spelling.** The attachment storage alias
  `django_pydantic_agent_attachments`, the session key the session conversation
  store writes, and the `django_pydantic_agent.audit` and
  `django_pydantic_agent.failure` loggers are unchanged, so existing settings,
  sessions and `LOGGING` keep working.
