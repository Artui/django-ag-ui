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
- **Authentication, closed by default** — every route refuses anonymous
  requests (`401`) until you say otherwise, and a `get_user(request)` hook
  establishes the user tools, the drf-mcp bridge, and conversation ownership
  act as. See [Security defaults](#security-defaults).
- **One-object mounting** — an `AGUIServer(registry, …)` config object exposing a
  namespaced `.urls`, mounted the `admin.site` way with
  `path("agent/", server.urls)`. It builds the agent view and every sub-view from
  the registry passed **once**, and forwards one auth policy to all of them.
- **Skills** — a `SkillRegistry` / `SkillSpec` catalog of pre-defined prompts
  served at `<prefix>skills/` (via `AGUIServer(..., skills=...)`), surfaced by the
  web component as chips and a `/`-command palette.
- **Tool metadata catalog** — a read-only `ToolsView` served at `<prefix>tools/`
  (mounted automatically by `AGUIServer`), giving the web component
  (`data-tools-url`) friendly card labels for server-side tools whose schema never
  reaches the browser.
- **Audit boundary** — an `AuditLogger` Protocol (`Null` / `Logging` shipped,
  pluggable by dotted path) records every server-side tool call.
- **Opt-in conversation persistence** — a `ConversationStore` Protocol with a
  no-op default, a session-backed store, and an abstract model-backed base.
- **Thread history** — the store can `list` and `rename` a user's threads, and a
  `ThreadsView` served at `<prefix>threads/` (mounted by `AGUIServer` when a store
  is active) backs a chat-history drawer (owner-scoped GET list / GET messages / PATCH
  rename / DELETE). An opt-in `django_ag_ui.contrib.store` app ships a ready-made
  durable model + `DefaultConversationStore` (add it to `INSTALLED_APPS` and
  `migrate`); the base package still ships no model.
- **File uploads** — an `AttachmentStore` Protocol (owner-scoped, off by default)
  with an `AttachmentsView` served at `<prefix>attachments/` (mounted by
  `AGUIServer` when a store is active; server-validated POST upload / owner-checked
  GET download / DELETE). Uploads travel as lightweight refs, and a per-request
  `read_attachment` tool lets the agent read the bytes server-side. The same
  `contrib.store` app ships a `Storage`-backed `DefaultAttachmentStore`.
- **Voice input** — a `TranscriptionBackend` Protocol (off by default) with a
  `TranscribeView` served at `<prefix>transcribe/` (mounted by `AGUIServer` when a
  backend is active; multipart audio in, `{"text"}` out). An
  opt-in `OpenAITranscriptionBackend` works against any OpenAI-compatible
  `/audio/transcriptions` endpoint (the `[openai]` extra).
- **Model reasoning** — when a reasoning model is configured to think (via
  `MODEL_SETTINGS`), its chain-of-thought streams to the client as standard
  AG-UI reasoning events (pure pass-through); `FORWARD_REASONING = False` keeps
  it server-side.
- **Reach external tools** — compose any Pydantic-AI toolset, including an
  in-process [`drf-mcp`](https://github.com/Artui/djangorestframework-mcp-server)
  bridge (the `[drf-mcp]` extra) so the agent can query DRF-exposed data.
- **drf-services specs as tools, no MCP hop** — point `SERVICE_SPECS` at a
  `name → spec` mapping and the agent calls them in-process via
  [`djangorestframework-pydantic-ai`](https://github.com/Artui/djangorestframework-pydantic-ai)'s
  `SpecCapability` (the `[spec-tools]` extra) — permission-checked, acting as the
  logged-in user, with the spec conventions taught to the model.
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
from django.urls import path

from django_ag_ui import AGUIServer

from .tools import registry

urlpatterns = [
    path("agent/", AGUIServer(registry).urls),
]
```

```python
# settings.py
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",  # any Pydantic-AI model string
    # "API_KEY": os.environ["ANTHROPIC_API_KEY"],  # else inferred from env
    # "MODEL_SETTINGS": {"temperature": 0.2},
}
```

`POST`ing an AG-UI `RunAgentInput` to `/agent/` now streams the agent's run.
Frontend-declared tools in the request are merged into the agent's catalog
automatically; server-side tools run in-process. See the
[docs](https://artui.github.io/django-ag-ui/) for the full settings reference,
the persistence stores, and the `drf-mcp` bridge.

## Security defaults

An agent endpoint is not an ordinary view: **server-side tools act as
`request.user`**, so who reaches the endpoint decides what the model can read
and change. Three defaults are worth knowing before you deploy.

### 1. Anonymous requests are refused

`require_authenticated` defaults to `True` on the agent endpoint **and on every
sub-view `AGUIServer` mounts** — the tool and skill catalogs, the thread drawer,
the attachment routes, transcription, the run index. An anonymous request gets
`401` with JSON `{"error": "authentication required"}`.

```python
AGUIServer(registry, require_authenticated=False)  # serve anonymous runs
```

Waiving it is a real choice for a public demo assistant with no user-scoped
tools. It is the wrong choice anywhere the tools read user data: without an
authenticated user, `request.user` is `AnonymousUser` and every visitor shares
one identity.

### 2. Establish the acting user with `get_user`

Refusing anonymous callers is not the same as knowing *who* is calling. Django's
auth middleware answers that for cookie-authenticated sites; for token clients,
pass a hook. It may be **sync or async** — a sync hook runs off the event loop,
so a plain ORM lookup is fully supported:

```python
def get_user(request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return Token.objects.select_related("user").get(key=token).user


AGUIServer(registry, get_user=get_user)
```

Its return value is assigned onto `request.user`. A hook that raises propagates
as a 500 — return `AnonymousUser` (or `None`) for a clean `401` instead. An
`authorize=` predicate runs after the user is established and denies with `403`
(JSON, never an HTML login redirect), which is the seam for a staff gate.

### 3. CSRF is exempt unless you say otherwise

AG-UI clients typically authenticate by header (Bearer / API key), where CSRF
does not apply — so the view is CSRF-exempt by default.

**If your deployment authenticates with session cookies, that default is
wrong for you.** Tools act as `request.user`, so a cookie-authenticated endpoint
with CSRF off lets any third-party page drive the agent as whoever is logged in
— mitigated, but not eliminated, by Django's default `SameSite=Lax` cookie.

```python
AGUIServer(registry, csrf_exempt=False)  # and send X-CSRFToken from the client
```

Leaving `csrf_exempt` unset *and* passing no `get_user` hook emits a
`RuntimeWarning` when the endpoint is built. That combination says nothing about
how requests authenticate, and the likeliest reading is the dangerous one. It is
the case the `require_authenticated` default cannot see — those requests *are*
authenticated. Any of three answers settles it and silences the warning:
`csrf_exempt=False`, `csrf_exempt=True` (deliberately exempt), or a `get_user`
hook.

### Anonymous requests and the stores

The model-backed stores refuse anonymous thread / attachment operations unless
`ALLOW_ANONYMOUS` is set (which buckets per browser session). Owner scoping
alone cannot isolate anonymous visitors from one another — they have no user id
— so prefer an authenticated endpoint over `ALLOW_ANONYMOUS` whenever a store
persists.

## License

MIT — see [LICENSE](LICENSE).
