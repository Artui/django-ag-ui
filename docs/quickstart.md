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

## 3. Mount the view

[`DjangoAGUIView`][django_ag_ui.DjangoAGUIView] is a callable instance that
holds one registry. [`get_urls`][django_ag_ui.get_urls] returns the URL
patterns; include them from your root URLconf.

```python
# urls.py
from django.urls import include, path

from django_ag_ui import DjangoAGUIView, get_urls

from agent_tools import registry

agent_view = DjangoAGUIView(registry)

urlpatterns = [
    path("", include(get_urls(agent_view))),
]
```

This mounts a POST endpoint at `agent/` (override with
`get_urls(view, prefix="chat/")`). The endpoint accepts a `RunAgentInput` JSON
body and streams AG-UI events back as `text/event-stream`.

Because the view is an instance, you can mount several with independent
registries — one per surface, each with its own tools.

## 4. (Optional) override per mount

`model`, `instructions`, and `audit_logger` fall back to `DJANGO_AG_UI` but can
be passed explicitly — handy in tests, where you inject a Pydantic-AI
`TestModel`:

```python
from pydantic_ai.models.test import TestModel

view = DjangoAGUIView(registry, model=TestModel())
```

CSRF is exempt by default (AG-UI clients authenticate via headers/session and
post JSON); pass `csrf_exempt=False` to opt back in.

Authentication is the host's responsibility, but the view offers two hooks. Pass
`require_authenticated=True` to fail closed (anonymous requests get `401`), and a
`get_user=` callable to establish the user (e.g. from a token) — its return value
is assigned onto `request.user`, so tools and conversation ownership act as that
user:

```python
view = DjangoAGUIView(
    registry,
    require_authenticated=True,
    get_user=lambda request: resolve_user_from_token(request),
)
```

## 5. (Optional) offer skills

Register pre-defined prompts in a [`SkillRegistry`][django_ag_ui.SkillRegistry]
and pass it to `get_urls(..., skills=...)` to mount a `<prefix>skills/` catalog
the web component fetches via `data-skills-url`:

```python
from django_ag_ui import SkillRegistry

skills = SkillRegistry()
skills.add(
    "summarise",
    title="Summarise",
    prompt="Summarise the {selection} for me.",
    chip=True,
)

urlpatterns = [
    *get_urls(DjangoAGUIView(registry), prefix="agent/", skills=skills),
]
```

## 6. (Optional) publish the tool catalog

Pass `tools=registry` (the **same** registry the view holds) to mount a
read-only tool catalog at `<prefix>tools/` (GET, JSON). The web component fetches
it via `data-tools-url` to label tool-call cards for server-side tools — whose
JSON Schema never reaches the browser:

```python
urlpatterns = [
    *get_urls(DjangoAGUIView(registry), prefix="agent/", tools=registry),
]
```

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
