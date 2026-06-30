# django-ag-ui

[![CI](https://github.com/Artui/django-ag-ui/workflows/tests/badge.svg)](https://github.com/Artui/django-ag-ui/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/django-ag-ui.svg)](https://pypi.org/project/django-ag-ui/)
[![Python versions](https://img.shields.io/pypi/pyversions/django-ag-ui.svg)](https://pypi.org/project/django-ag-ui/)
[![Django versions](https://img.shields.io/pypi/djversions/django-ag-ui.svg)](https://pypi.org/project/django-ag-ui/)
[![Docs](https://img.shields.io/badge/docs-artui.github.io-blue.svg)](https://artui.github.io/django-ag-ui/)
[![Coverage](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/Artui/django-ag-ui/gh-pages/coverage.json)](https://github.com/Artui/django-ag-ui/actions/workflows/tests.yml)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/pypi/l/django-ag-ui.svg)](LICENSE)

Wire a [Pydantic-AI](https://ai.pydantic.dev) agent into any Django project and
speak the [AG-UI](https://docs.ag-ui.com) protocol to a browser — a streaming
agent endpoint, a typed tool registry, and the plumbing in between. No admin
specifics; that lives in the downstream
[`django-admin-agent`](https://github.com/Artui/django-admin-agent), and the
browser half is
[`@artooi/ag-ui-web-component`](https://github.com/Artui/ag-ui-web-component).

- **Async AG-UI endpoint** — `DjangoAGUIView` wraps Pydantic-AI's `AGUIAdapter`
  and returns a `StreamingHttpResponse` of AG-UI events (SSE). Conversation
  state rides in each request, so there's no cross-request session store and
  multi-worker deployments are safe by default.
- **Typed tool registry** — register plain callables with `@tool`; JSON Schema
  is derived from their signatures. `destructive=` / `category=` / `confirm=` /
  `summary=` metadata surface as `x-destructive` / `x-category` / `x-confirm` /
  `x-summary` extensions for client-side gating.
- **Configurable agent** — `AgentConfig` + the `DJANGO_AG_UI` settings cover the
  model, `MODEL_SETTINGS`, `RETRIES`, external `TOOLSETS` / `CAPABILITIES`, an
  explicit `API_KEY` / `PROVIDER` credential path, and an `AGENT_FACTORY` escape
  hatch for full control of construction.
- **Authentication hooks** — `require_authenticated=True` fails closed (`401`)
  for anonymous requests, and a `get_user(request)` hook establishes the user
  tools, the drf-mcp bridge, and conversation ownership act as.
- **Skills** — a `SkillRegistry` / `SkillSpec` catalog of pre-defined prompts
  served at `<prefix>skills/` via `get_urls(view, skills=...)`, surfaced by the
  web component as chips and a `/`-command palette.
- **Tool metadata catalog** — a read-only `ToolsView` served at `<prefix>tools/`
  via `get_urls(view, tools=registry)`, giving the web component (`data-tools-url`)
  friendly card labels for server-side tools whose schema never reaches the
  browser.
- **Audit boundary** — an `AuditLogger` Protocol (`Null` / `Logging` shipped,
  pluggable by dotted path) records every server-side tool call.
- **Opt-in conversation persistence** — a `ConversationStore` Protocol with a
  no-op default, a session-backed store, and an abstract model-backed base.
- **Thread history** — the store can `list` and `rename` a user's threads, and a
  `ThreadsView` served at `<prefix>threads/` via `get_urls(view, threads=store)`
  backs a chat-history drawer (owner-scoped GET list / GET messages / PATCH
  rename / DELETE). An opt-in `django_ag_ui.contrib.store` app ships a ready-made
  durable model + `DefaultConversationStore` (add it to `INSTALLED_APPS` and
  `migrate`); the base package still ships no model.
- **File uploads** — an `AttachmentStore` Protocol (owner-scoped, off by default)
  with an `AttachmentsView` served at `<prefix>attachments/` via
  `get_urls(view, attachments=store)` (server-validated POST upload / owner-checked
  GET download / DELETE). Uploads travel as lightweight refs, and a per-request
  `read_attachment` tool lets the agent read the bytes server-side. The same
  `contrib.store` app ships a `Storage`-backed `DefaultAttachmentStore`.
- **Voice input** — a `TranscriptionBackend` Protocol (off by default) with a
  `TranscribeView` served at `<prefix>transcribe/` via
  `get_urls(view, transcribe=backend)` (multipart audio in, `{"text"}` out). An
  opt-in `OpenAITranscriptionBackend` works against any OpenAI-compatible
  `/audio/transcriptions` endpoint (the `[openai]` extra).
- **Model reasoning** — when a reasoning model is configured to think (via
  `MODEL_SETTINGS`), its chain-of-thought streams to the client as standard
  AG-UI reasoning events (pure pass-through); `FORWARD_REASONING = False` keeps
  it server-side.
- **Reach external tools** — compose any Pydantic-AI toolset, including an
  in-process [`drf-mcp`](https://github.com/Artui/djangorestframework-mcp-server)
  bridge (the `[drf-mcp]` extra) so the agent can query DRF-exposed data.
- **100% test coverage**, type-checked, Python 3.10–3.14, Django 4.2–6.0.

📖 **Full documentation:** <https://artui.github.io/django-ag-ui/>

```bash
pip install "django-ag-ui[anthropic]"   # or [openai], or [google]
# or, with uv:
uv add "django-ag-ui[anthropic]"
```

> The core dep is `pydantic-ai-slim[ag-ui]`, which ships no model-provider
> library — pick one via a provider extra (`anthropic` / `openai` / `google`).

> **ASGI required.** The agent endpoint streams Server-Sent Events, which the
> sync WSGI worker can't serve — deploy under Daphne / Uvicorn.

---

## Quick start

Register a read-only tool, mount the endpoint, and point a browser AG-UI client
at it.

```python
# tools.py
from django_ag_ui import ToolRegistry, tool

registry = ToolRegistry()


@tool(registry)
def count_active_users() -> int:
    """How many users are currently active."""
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(is_active=True).count()
```

```python
# urls.py
from django.urls import path  # noqa: F401

from django_ag_ui import DjangoAGUIView, get_urls

from .tools import registry

urlpatterns = [
    *get_urls(DjangoAGUIView(registry), prefix="agent/"),
]
```

```python
# settings.py
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",   # any Pydantic-AI model string
    # "API_KEY": os.environ["ANTHROPIC_API_KEY"],  # else inferred from env
    # "MODEL_SETTINGS": {"temperature": 0.2},
    # "AUDIT_LOGGER": "django_ag_ui.LoggingAuditLogger",
    # "CONVERSATION_STORE": "django_ag_ui.DjangoSessionConversationStore",
}
```

`POST`ing an AG-UI `RunAgentInput` to `/agent/` now streams the agent's run.
Frontend-declared tools in the request are merged into the agent's catalog
automatically; server-side tools run in-process. See the
[docs](https://artui.github.io/django-ag-ui/) for the full settings reference,
the persistence stores, and the `drf-mcp` bridge.

## License

MIT — see [LICENSE](LICENSE).
