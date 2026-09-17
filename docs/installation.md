# Installation

```bash
pip install django-ag-ui
```

Core dependencies are `django>=4.2` and `pydantic-ai-slim[ag-ui]>=2.37,<3`. The
AG-UI wire types and the `AGUIAdapter` come from the `pydantic-ai-slim[ag-ui]`
extra; this package does not re-implement them. The **slim** package ships the
AG-UI adapter and wire types but **no model-provider library** — pick one via a
provider extra (see below).

## Compatibility

| Component | Floor | Tested |
| --- | --- | --- |
| Python | 3.10 | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Django | 4.2 LTS | 4.2, 5.0, 5.1, 5.2, 6.0 |
| Pydantic-AI | 2.37 (with the `pydantic-ai-slim[ag-ui]` extra) | latest in the CI matrix |

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
</content>
