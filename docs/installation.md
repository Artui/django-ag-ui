# Installation

```bash
pip install django-ag-ui
```

Core dependencies are `django>=4.2` and `pydantic-ai[ag-ui]>=1.0`. The AG-UI
wire types and the `AGUIAdapter` come from the `pydantic-ai[ag-ui]` extra; this
package does not re-implement them.

## Compatibility

| Component | Floor | Tested |
| --- | --- | --- |
| Python | 3.10 | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Django | 4.2 LTS | 4.2, 5.0, 5.1, 5.2, 6.0 |
| Pydantic-AI | 1.0 (with the `[ag-ui]` extra) | latest in the CI matrix |

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
mounted.

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
