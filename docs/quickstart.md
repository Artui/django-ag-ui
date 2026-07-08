# Quickstart

This walks through registering server-side tools, building the view, and
mounting the AG-UI endpoint. It assumes you are deploying under ASGI (see
[Installation](installation.md#asgi-is-required)).

## 1. Configure the model

Set the Pydantic-AI model in your Django settings:

```python
# settings.py
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",
}
```

Any Pydantic-AI model string (or a `Model` instance passed to the view) works.
If you neither set `MODEL` nor pass `model=` to the view, building an agent
raises `ImproperlyConfigured` with a clear message.

Under `pydantic-ai-slim`, the matching provider extra must be installed for your
model (see [Installation → Model provider extras](installation.md#model-provider-extras)):

```bash
pip install "django-ag-ui[anthropic]"
```

By default Pydantic-AI infers the provider key from the environment. To pass it
explicitly instead, set [`API_KEY`](configuration.md#api_key) (or
[`PROVIDER`](configuration.md#provider) for a custom `base_url` / client):

```python
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",
    "API_KEY": os.environ["ANTHROPIC_API_KEY"],
}
```

## 2. Register tools

A [`ToolRegistry`][django_ag_ui.ToolRegistry] is an instance — build one and
attach tools with the [`@tool`][django_ag_ui.tool] decorator. Tools declare
**typed** parameters and a typed return; the registry derives the JSON Schema
for AG-UI from the signature.

```python
# agent_tools.py
from django_ag_ui import ToolCategory, ToolRegistry, tool

registry = ToolRegistry()


@tool(registry, category=ToolCategory.INTROSPECT)
def count_active_users() -> int:
    """Return how many users are currently active."""
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(is_active=True).count()


@tool(
    registry,
    destructive=True,
    category=ToolCategory.UI_WRITE,
    confirm="Deactivate this user?",
    summary="Deactivate user",
)
def deactivate_user(user_id: int) -> str:
    """Deactivate the user with the given id."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(pk=user_id)
    user.is_active = False
    user.save(update_fields=["is_active"])
    return f"deactivated {user_id}"
```

`destructive=True` is stamped into the tool's JSON Schema as `x-destructive`, so
an AG-UI client can gate it behind an inline confirmation card. The optional
`confirm=` prompt is stamped as `x-confirm` (shown in the card), and `summary=`
as `x-summary` (the card's label). The first paragraph of the docstring becomes
the tool description unless you pass `description=`.

## 3. Mount the server

[`AGUIServer`][django_ag_ui.AGUIServer] is the package's front door: construct it
**once** with the tool registry, then mount its namespaced
[`.urls`][django_ag_ui.AGUIServer.urls] with `include()` — the
`django.contrib.admin` `site.urls` idiom.

```python
# urls.py
from django.urls import path

from django_ag_ui import AGUIServer

from agent_tools import registry

agent = AGUIServer(registry)

urlpatterns = [
    path("agent/", agent.urls),
]
```

This mounts a POST endpoint at `agent/` (choose any prefix the Django way —
`path("chat/", agent.urls)`) plus a read-only tool catalog at `agent/tools/`. The
agent endpoint accepts a `RunAgentInput` JSON body and streams AG-UI events back
as `text/event-stream`.

`.urls` is the `(patterns, app_name, namespace)` triple `path()` mounts directly
(like `admin.site.urls` — no `include()`), so the endpoint names are
**namespaced** (`"ag_ui"` by default) and reversible —
`reverse("ag_ui:endpoint")`, `reverse("ag_ui:tools")`. Two mounts don't collide;
pass `namespace="…"` to distinguish them. Because the server holds its own
registry and config, you can mount several with independent registries — one per
surface, each with its own tools.

## 4. (Optional) override per mount

`model`, `instructions`, and `audit_logger` fall back to `DJANGO_AG_UI` but can
be passed explicitly — handy in tests, where you inject a Pydantic-AI
`TestModel`:

```python
from pydantic_ai.models.test import TestModel

agent = AGUIServer(registry, model=TestModel())
```

!!! warning "CSRF and cookie-authenticated deployments"
    CSRF is exempt by default — right for header-token auth (Bearer / API
    key), where CSRF doesn't apply. If your deployment authenticates with
    **session cookies**, pass `csrf_exempt=False` and send the token from
    the client: tools act as `request.user`, so a cookie-auth endpoint
    without CSRF protection lets any third-party page drive the agent as
    the logged-in user (Django's default `SameSite=Lax` cookie mitigates,
    but does not eliminate, the risk).

Authentication is the host's responsibility, but the view offers two hooks. Pass
`require_authenticated=True` to fail closed (anonymous requests get `401`), and a
`get_user=` callable — **sync or async**; a sync ORM lookup is fully supported
(it runs off the event loop) — to establish the user. Its return value is
assigned onto `request.user`, so tools and conversation ownership act as that
user:

```python
def get_user(request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return Token.objects.select_related("user").get(key=token).user


agent = AGUIServer(
    registry,
    require_authenticated=True,
    get_user=get_user,
)
```

`AGUIServer` forwards `require_authenticated` / `get_user` / `authorize` to
**every** view it builds — the agent endpoint *and* the tool / skill / thread /
attachment catalogs — so one policy locks down the whole mount (the catalogs
enumerate every server tool and skill prompt, so they're worth gating too).

## 5. (Optional) offer skills

Register pre-defined prompts in a [`SkillRegistry`][django_ag_ui.SkillRegistry]
and pass it as `AGUIServer(..., skills=...)` to mount a `<prefix>skills/` catalog
the web component fetches via `data-skills-url`:

```python
from django_ag_ui import AGUIServer, SkillRegistry

skills = SkillRegistry()
skills.add(
    "summarise",
    title="Summarise",
    prompt="Summarise the {selection} for me.",
    chip=True,
)

urlpatterns = [
    path("agent/", AGUIServer(registry, skills=skills).urls),
]
```

## 6. The tool catalog

The read-only tool catalog is mounted automatically at `<prefix>tools/` (GET,
JSON) — the server builds it from the **same** registry you pass, so there's
nothing extra to wire. The web component fetches it via `data-tools-url` to
label tool-call cards for server-side tools, whose JSON Schema never reaches the
browser.

Each entry is `{"name", "summary", "description"?}`; `summary` falls back from
`@tool(summary=…)` to a prettified tool name. With
[`DRF_MCP_SERVER`](configuration.md#drf_mcp_server) set, the catalog also surfaces
the drf-mcp tools, using their `display_name` as the label. See
[Tool metadata catalog](concepts.md#tool-metadata-catalog).

## What next

- [Configuration](configuration.md) for every settings key.
- [Key concepts](concepts.md) for how the registry, audit logger, streaming,
  and persistence fit together.
</content>
