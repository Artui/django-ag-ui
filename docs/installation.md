# Installation

```bash
pip install django-ag-ui
```

Core dependencies are `django>=4.2` and `pydantic-ai-slim[ag-ui]>=1.0`. The
AG-UI wire types and the `AGUIAdapter` come from the `pydantic-ai-slim[ag-ui]`
extra; this package does not re-implement them. The **slim** package ships the
AG-UI adapter and wire types but **no model-provider library** — pick one via a
provider extra (see below).

## Compatibility

| Component | Floor | Tested |
| --- | --- | --- |
| Python | 3.10 | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Django | 4.2 LTS | 4.2, 5.0, 5.1, 5.2, 6.0 |
| Pydantic-AI | 1.0 (with the `pydantic-ai-slim[ag-ui]` extra) | latest in the CI matrix |

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

When you set [`API_KEY` or `PROVIDER`](configuration.md#api_key) so the model is
built with an explicit key, the `MODEL` string's `provider:` prefix must be one
of: `anthropic`, `openai`, `google`, `google-gla`, `gemini`. An unknown prefix
raises `ImproperlyConfigured` (set `PROVIDER` to a `Provider` instance instead).

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

## The `[drf-mcp]` extra

To expose a
[`djangorestframework-mcp-server`](https://github.com/Artui/djangorestframework-mcp-server)
tool registry to the agent in-process (no network MCP hop), install the extra:

```bash
pip install "django-ag-ui[drf-mcp]"
```

This pulls in `djangorestframework-mcp-server>=0.5`. The bridge
([`DrfMcpToolset`](concepts.md#the-drf-mcp-toolset-bridge)) is imported lazily,
only when `DJANGO_AG_UI["DRF_MCP_SERVER"]` is set, so the dependency stays
optional for projects that do not use it. See
[Configuration → `DRF_MCP_SERVER`](configuration.md#drf_mcp_server).
</content>
