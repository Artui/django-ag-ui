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


@tool(registry, destructive=True, category=ToolCategory.UI_WRITE)
def deactivate_user(user_id: int) -> str:
    """Deactivate the user with the given id."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.get(pk=user_id)
    user.is_active = False
    user.save(update_fields=["is_active"])
    return f"deactivated {user_id}"
```

`destructive=True` is stamped into the tool's JSON Schema as `x-destructive`, so
an AG-UI client can gate it behind a confirmation modal. The first paragraph of
the docstring becomes the tool description unless you pass `description=`.

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

## What next

- [Configuration](configuration.md) for every settings key.
- [Key concepts](concepts.md) for how the registry, audit logger, streaming,
  and persistence fit together.
</content>
