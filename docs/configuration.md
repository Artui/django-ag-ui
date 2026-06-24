# Configuration

All configuration lives under a single `DJANGO_AG_UI` dict in your Django
settings. It is read fresh on every request into a frozen
[`AppSettings`][django_ag_ui.AppSettings] snapshot
([`get_settings`][django_ag_ui.get_settings]), so test overrides take effect
immediately. Every key is optional; the table below lists the dict key, the
default, and what it does.

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `MODEL` | `str` | `None` | Pydantic-AI model string (or pre-built `Model`). |
| `API_KEY` | `str` | `None` | Explicit provider key (builds the model via `build_model`). |
| `PROVIDER` | `Provider` / dotted `str` | `None` | Explicit Pydantic-AI `Provider`; takes precedence over `API_KEY`. |
| `AUTO_CONFIRM` | `bool` | `False` | Whether destructive tools skip confirmation. |
| `AUDIT_LOGGER` | dotted `str` | `None` | `AuditLogger` implementation. |
| `SYSTEM_PROMPT` | `str` | `None` | Override the agent's instructions. |
| `MODEL_SETTINGS` | `dict` | `None` | Pydantic-AI `ModelSettings`. |
| `RETRIES` | `int` | `None` | Tool/output retry budget. |
| `AGENT_FACTORY` | dotted `str` | `None` | Escape hatch replacing `build_agent`. |
| `TOOLSETS` | `tuple[str, ...]` | `()` | Extra Pydantic-AI toolsets. |
| `CAPABILITIES` | `tuple[str, ...]` | `()` | Pydantic-AI capabilities. |
| `CONVERSATION_STORE` | dotted `str` | `None` | Server-side conversation persistence. |
| `DRF_MCP_SERVER` | dotted `str` | `None` | drf-mcp server whose tools the agent gets. |

## `MODEL`

A Pydantic-AI model string, e.g. `"anthropic:claude-sonnet-4.6"`, **or** a
pre-built Pydantic-AI `Model` instance (which passes through untouched).
Optional in settings, but an agent cannot be built without a model: if `MODEL`
is unset and no `model=` is passed to
[`DjangoAGUIView`][django_ag_ui.DjangoAGUIView], the view raises
`django.core.exceptions.ImproperlyConfigured`. A `model=` argument to the view
always wins over this setting.

```python
DJANGO_AG_UI = {"MODEL": "anthropic:claude-sonnet-4.6"}
```

When [`API_KEY`](#api_key) or [`PROVIDER`](#provider) is set, the `MODEL` string
is routed through [`build_model`](api.md#django_ag_ui.agent.build_model.build_model),
which delegates the `provider:` prefix → model resolution to Pydantic-AI itself —
so any provider it knows works (`anthropic`, `openai`, `openai-responses`,
`google`, `google-gla`, `groq`, `bedrock`, …), as does a bare model name it can
map to a provider (e.g. `claude-…`). When the provider can't be resolved the view
raises `ImproperlyConfigured` (set `PROVIDER` instead). A pre-built `Model`
instance ignores `API_KEY` / `PROVIDER` and is used as-is.

## `API_KEY`

An explicit provider API key. When set (and `MODEL` is a `provider:name`
string), the view builds the model via
[`build_model`](api.md#django_ag_ui.agent.build_model.build_model) — passing the
key to the prefix's default `Provider` — **instead of** letting Pydantic-AI
infer the key from environment variables. Requires the matching provider extra
installed (e.g. `django-ag-ui[anthropic]`). [`PROVIDER`](#provider), if also
set, takes precedence over `API_KEY`.

```python
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",
    "API_KEY": os.environ["ANTHROPIC_API_KEY"],
}
```

## `PROVIDER`

A Pydantic-AI `Provider` instance (or a dotted-path string resolving to one),
passed straight to the constructed model. Use it when you need a custom
`base_url` or HTTP client (a proxy, a gateway, a self-hosted endpoint). It
**takes precedence over [`API_KEY`](#api_key)**. As with `API_KEY`, the matching
provider extra must be installed.

```python
DJANGO_AG_UI = {
    "MODEL": "openai:gpt-4o",
    "PROVIDER": "myproject.providers.gateway_provider",
}
```

## `AUTO_CONFIRM`

When `True`, destructive tools no longer require client-side confirmation (the
"autopilot" toggle). This is the value [`needs_confirmation`][django_ag_ui.needs_confirmation]
consults: a tool needs confirmation when it is `destructive` **and**
`auto_confirm` is `False`. The actual modal is rendered client-side; this flag
is the canonical server-side statement of the rule. Defaults to `False`.

## `AUDIT_LOGGER`

A dotted path to an [`AuditLogger`][django_ag_ui.AuditLogger] class, importable
with no arguments. `None` (the default) uses
[`NullAuditLogger`][django_ag_ui.NullAuditLogger] (no auditing). Point it at
[`LoggingAuditLogger`][django_ag_ui.LoggingAuditLogger] or your own
implementation. Resolution is done by
[`resolve_audit_logger`][django_ag_ui.resolve_audit_logger], which raises
`TypeError` if the path does not produce an `AuditLogger`.

```python
DJANGO_AG_UI = {
    "AUDIT_LOGGER": "django_ag_ui.LoggingAuditLogger",
}
```

A logger that needs constructor arguments cannot be expressed as a bare dotted
path — construct it yourself and pass `audit_logger=` to the view instead.

## `SYSTEM_PROMPT`

Overrides the agent's instructions. When unset, the view uses
[`DEFAULT_SYSTEM_PROMPT`][django_ag_ui.DEFAULT_SYSTEM_PROMPT]. An `instructions=`
argument to the view takes precedence over both.

## `MODEL_SETTINGS`

A Pydantic-AI `ModelSettings` dict (e.g. `{"temperature": 0.2, "max_tokens":
1024}`) passed straight to the `Agent`. `None` leaves model defaults untouched.

```python
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",
    "MODEL_SETTINGS": {"temperature": 0.2, "max_tokens": 1024},
}
```

## `RETRIES`

The default tool/output retry budget passed to the `Agent`. `None` uses
Pydantic-AI's own default.

## `AGENT_FACTORY`

The escape hatch. A dotted path to a callable matching
[`AgentFactoryFn`][django_ag_ui.AgentFactoryFn] —
`(registry: ToolRegistry, settings: AppSettings) -> Agent` — that **fully
replaces** the built-in [`build_agent`][django_ag_ui.build_agent]. Use it for
custom model providers, output types, instrumentation, or toolset wiring the
sugar keys do not cover. When set, the view hands construction entirely to your
factory and does **not** apply `MODEL_SETTINGS`, `RETRIES`, `TOOLSETS`,
`CAPABILITIES`, or the drf-mcp bridge itself — your factory owns all of it.

```python
# myproject/agent.py
from pydantic_ai import Agent


def build_my_agent(registry, settings):
    return Agent(model="anthropic:claude-sonnet-4.6", ...)
```

```python
DJANGO_AG_UI = {"AGENT_FACTORY": "myproject.agent.build_my_agent"}
```

## `TOOLSETS`

A tuple of dotted paths to extra Pydantic-AI toolsets composed alongside the
registry tools — e.g. an MCP-client toolset. Each path resolves to either an
instance (used as-is) or a zero-argument callable/class returning one (invoked),
via [`resolve_dotted_instances`](api.md#django_ag_ui.agent.resolve_dotted_instances.resolve_dotted_instances).
Empty by default. (Ignored when `AGENT_FACTORY` is set.)

```python
DJANGO_AG_UI = {
    "TOOLSETS": ("myproject.toolsets.weather_toolset",),
}
```

## `CAPABILITIES`

A tuple of dotted paths to Pydantic-AI capabilities passed to the `Agent`,
resolved the same way as `TOOLSETS`. Empty by default. (Ignored when
`AGENT_FACTORY` is set.)

## `CONVERSATION_STORE`

A dotted path to a [`ConversationStore`][django_ag_ui.ConversationStore] class,
importable with no arguments. `None` (the default) keeps the server stateless
using [`NullConversationStore`][django_ag_ui.NullConversationStore] — the
conversation lives entirely in the client's posted history. Resolution is done
by [`resolve_conversation_store`][django_ag_ui.resolve_conversation_store],
which raises `TypeError` if the path does not produce a `ConversationStore`.

The package ships
[`DjangoSessionConversationStore`][django_ag_ui.DjangoSessionConversationStore]
(session-backed, no migration) and an abstract
[`ModelConversationStore`][django_ag_ui.ModelConversationStore] base you can
subclass with your own model. See
[Conversation persistence](concepts.md#conversation-persistence).

```python
DJANGO_AG_UI = {
    "CONVERSATION_STORE": "django_ag_ui.DjangoSessionConversationStore",
}
```

For a ready-made **durable, cross-device** store, opt into the
`django_ag_ui.contrib.store` app instead of writing your own model — add it to
`INSTALLED_APPS`, run `migrate`, and point the setting at its store:

```python
INSTALLED_APPS = [
    # ...
    "django_ag_ui.contrib.store",
]

DJANGO_AG_UI = {
    "CONVERSATION_STORE": (
        "django_ag_ui.contrib.store.default_conversation_store.DefaultConversationStore"
    ),
}
```

The base package ships no model, so projects that don't opt in get no migration.
To expose the **thread-history drawer** endpoints over HTTP, pass the same store
to `get_urls(view, threads=store)` — see
[Thread history](concepts.md#thread-history).

## `DRF_MCP_SERVER`

A dotted path to a `djangorestframework-mcp-server` `MCPServer` instance whose
tools are exposed to the agent in-process (requires the `[drf-mcp]` extra).
`None` (the default) disables the bridge. When set, the view builds a per-request
[`DrfMcpToolset`](concepts.md#the-drf-mcp-toolset-bridge) carrying the current
`request`, so the agent acts as the logged-in user and drf-mcp's own validation
and permission checks apply. See
[Installation → the `[drf-mcp]` extra](installation.md#the-drf-mcp-extra).

The drf-mcp tools also appear in the
[tool metadata catalog](concepts.md#tool-metadata-catalog) (mounted by
`get_urls(view, tools=registry)`), which reads each tool's `display_name` /
`display_description` as the web component's card label.

```python
DJANGO_AG_UI = {
    "DRF_MCP_SERVER": "myproject.mcp.server",
}
```
</content>
