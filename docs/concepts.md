# Key concepts

This page explains the moving parts behind the [Quickstart](quickstart.md). For
exact signatures, see the [API reference](api.md).

## The tool registry and `@tool`

A [`ToolRegistry`][django_ag_ui.ToolRegistry] is an ordered, named collection of
server-side tools. **State lives on the instance** — a
[`DjangoAGUIView`][django_ag_ui.DjangoAGUIView] holds one, and tests build a
fresh registry per scenario. There is no module-level global registry.

Each tool is a [`ToolSpec`][django_ag_ui.ToolSpec]: a frozen dataclass bundling
the callable with its `name`, `description`, a `destructive` flag, a
[`ToolCategory`][django_ag_ui.ToolCategory], and the optional `confirm` (a
human-readable confirmation prompt) and `summary` (a tool-call card label)
strings. The [`@tool`][django_ag_ui.tool] decorator (and the registry's `add`)
builds the spec and registers it, defaulting the name to the function name and
the description to the first paragraph of its docstring.

At registration the registry derives a JSON Schema from the function signature
and stores it alongside the spec as a [`ToolBinding`][django_ag_ui.ToolBinding],
so tool listings never re-introspect on each request. Tool callables must be
fully typed — an untyped tool breaks schema generation.

The registry can dispatch synchronously (`call`) or asynchronously (`acall`);
`call` refuses coroutine functions rather than silently returning an un-awaited
coroutine.

### Destructive metadata and `x-destructive`

AG-UI has no native concept of a "risky" tool, so
[`build_input_schema`][django_ag_ui.build_input_schema] stamps two JSON-Schema
extensions at the schema root:

- `x-destructive: true` (the key is
  [`X_DESTRUCTIVE_KEY`][django_ag_ui.X_DESTRUCTIVE_KEY]) when `destructive=True`.
- `x-category` (the key is [`X_CATEGORY_KEY`][django_ag_ui.X_CATEGORY_KEY])
  carrying the tool's [`ToolCategory`][django_ag_ui.ToolCategory] value.
- `x-confirm` (the key is [`X_CONFIRM_KEY`][django_ag_ui.X_CONFIRM_KEY])
  carrying the `confirm=` confirmation prompt, when set.
- `x-summary` (the key is [`X_SUMMARY_KEY`][django_ag_ui.X_SUMMARY_KEY])
  carrying the `summary=` tool-call card label, when set.

AG-UI passes these extensions through verbatim. A client (such as the
`@artooi/ag-ui-web-component`) reads `x-destructive` and gates execution behind
an inline confirmation card (showing `x-confirm` as the prompt and `x-summary`
as the card label). **The wire stays vanilla AG-UI** — the gating is purely
client-side. The server's canonical statement of the policy is
[`needs_confirmation`][django_ag_ui.needs_confirmation]: a tool needs
confirmation when it is destructive and the project has not set
[`AUTO_CONFIRM`](configuration.md#auto_confirm).
[`DEFAULT_SYSTEM_PROMPT`][django_ag_ui.DEFAULT_SYSTEM_PROMPT] steers the model to
call destructive tools directly (with the right arguments) and let the client
gate them, rather than refusing or asking for confirmation in-band.

`build_input_schema` handles the primitive parameter types — `str`, `int`,
`float`, `bool`, `list[T]`, `dict[str, Any]`, and `X | None` unions; richer
types fall back to an empty (but wire-valid) schema fragment.

## Building the agent: `AgentConfig` and `build_agent`

[`build_agent`][django_ag_ui.build_agent] turns a registry plus an
[`AgentConfig`][django_ag_ui.AgentConfig] into a Pydantic-AI `Agent`.
`AgentConfig` is a frozen record bundling the resolved `model`, `instructions`,
`audit_logger`, `model_settings`, `retries`, and the already-resolved `toolsets`
/ `capabilities` — so the call site passes one record instead of a long keyword
list.

Each registry tool is registered as a plain Pydantic-AI tool. When an
`audit_logger` is set, `build_agent` wraps every tool call to time it and record
success or failure, preserving the original signature so Pydantic-AI's schema
generation is unaffected. Frontend tools declared in the request are merged by
the adapter and are not registered here.

For total control over construction, set
[`AGENT_FACTORY`](configuration.md#agent_factory) to a callable matching
[`AgentFactoryFn`][django_ag_ui.AgentFactoryFn]; it replaces `build_agent`
entirely.

## The audit boundary: the `AuditLogger` protocol

[`AuditLogger`][django_ag_ui.AuditLogger] is a runtime-checkable Protocol with a
single method, `record(event: AuditEvent)`. Each
[`AuditEvent`][django_ag_ui.AuditEvent] is a frozen record of one tool
invocation: tool name, a string-ified arguments repr, duration in milliseconds,
a success flag, and either an error string or a result size.

Two implementations ship:

- [`NullAuditLogger`][django_ag_ui.NullAuditLogger] — discards every event. The
  default.
- [`LoggingAuditLogger`][django_ag_ui.LoggingAuditLogger] — writes to the Python
  `logging` framework (`INFO` on success, `WARNING` on failure).

Projects supply their own (Sentry, Honeycomb, custom) by pointing
[`AUDIT_LOGGER`](configuration.md#audit_logger) at a dotted path, resolved by
[`resolve_audit_logger`][django_ag_ui.resolve_audit_logger].

## Streaming: `DjangoAGUIView` and the AGUIAdapter

[`DjangoAGUIView`][django_ag_ui.DjangoAGUIView] is an async, callable view
instance. On each POST it:

1. Establishes the user. Authentication is the **host's responsibility**, but
   the view offers two hooks: a `get_user(request)` callable whose return value
   is assigned onto `request.user` (so tools, the drf-mcp bridge, and
   conversation ownership act as that user), and `require_authenticated=True`,
   which fails closed — anonymous requests get `401` with JSON
   `{"error": "authentication required"}`.
2. Parses the request body into a `RunAgentInput` via
   `AGUIAdapter.build_run_input` (returning HTTP 400 with an error count, not
   the raw payload, on a `ValidationError`).
3. Builds the per-request `Agent` (via the factory or `build_agent`).
4. Wraps the agent in a `pydantic_ai.ui.ag_ui.AGUIAdapter` and streams its
   encoded events as a `StreamingHttpResponse` with `Content-Type:
   text/event-stream`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no`.

Non-POST methods get `405 Method Not Allowed`. The view marks itself as a
coroutine function so Django awaits it under ASGI; served over WSGI it emits a
one-time `RuntimeWarning` (SSE streaming needs ASGI). Frontend-declared tools in
the request are merged into the catalog by the adapter automatically.

[`get_urls`][django_ag_ui.get_urls] returns the URL pattern(s) mounting a view
at a prefix (default `agent/`).

## Skills

A [`SkillRegistry`][django_ag_ui.SkillRegistry] is an instance (like the tool
registry) holding a catalog of **skills**: pre-defined prompts the client
surfaces as chips and/or a `/`-command palette. Skills are **data, not
callables** — there is no `@skill` decorator; you register them imperatively:

```python
from django_ag_ui import SkillRegistry

skills = SkillRegistry()
skills.add(
    "summarise",
    title="Summarise",
    prompt="Summarise the {selection} for me.",
    description="Condense the current selection.",
    chip=True,
)
```

Each entry is a frozen [`SkillSpec`][django_ag_ui.SkillSpec]
(`name`, `title`, `prompt`, optional `description`, `send_immediately`, `chip`).
`add(...)` is the convenience constructor; `register(SkillSpec(...))` takes a
pre-built spec. The `prompt` is a static string that may contain
`{placeholder}`s the client fills from its skill context before sending.
`send_immediately=True` sends the prompt on pick instead of pre-filling the
input; `chip=True` also surfaces the skill as a chip (the palette lists all
skills regardless).

[`SkillRegistry.payload()`][django_ag_ui.SkillRegistry] returns the
client catalog as a list of camelCase dicts (`name`, `title`, `prompt`, and the
optional `description`, `sendImmediately`, `chip` keys, omitted when at their
default). It is served by `SkillsView`
(`django_ag_ui.skills.skills_view.SkillsView`) — a GET-only callable view — which
[`get_urls`][django_ag_ui.get_urls] mounts at `<prefix>skills/` when you pass
`skills=`:

```python
urlpatterns = [
    *get_urls(DjangoAGUIView(registry), prefix="agent/", skills=skills),
]
```

The web component fetches this endpoint via its `data-skills-url` attribute.

## Conversation persistence

By default the server is **stateless**: the conversation lives in the message
history the client posts on every turn. Persistence is opt-in via
[`CONVERSATION_STORE`](configuration.md#conversation_store) and modelled as a
pluggable Protocol, exactly like the audit logger.

[`ConversationStore`][django_ag_ui.ConversationStore] is a runtime-checkable
Protocol with async `load` / `save` / `delete`, each taking the `request`. A
[`Conversation`][django_ag_ui.Conversation] is a frozen record of a `thread_id`,
the AG-UI `Message` list (the wire shape, round-tripped verbatim), and an
`owner_id` for authorization scoping.

The implementations:

- [`NullConversationStore`][django_ag_ui.NullConversationStore] — the default.
  `load` returns `None`; `save`/`delete` are no-ops. The view treats this store
  as "persistence off" and adds no overhead — it skips wiring an `on_complete`
  callback entirely.
- [`DjangoSessionConversationStore`][django_ag_ui.DjangoSessionConversationStore]
  — stores conversations in the Django session, namespaced by `thread_id` within
  the logged-in user's session (no migration). Durability spans that browser
  session.
- [`ModelConversationStore`][django_ag_ui.ModelConversationStore] — an abstract
  base for model-backed (or any synchronous) store. It provides the async
  wrapping (`sync_to_async`) and per-request owner scoping; a subclass
  implements three synchronous row operations (`_fetch`, `_store`, `_remove`)
  against its own Django model. The package ships **no** concrete model on
  purpose, so it forces no migration — you define the model, its fields, and the
  owner relationship.

When a non-null store is configured, the view persists the run's full message
history when the run finishes streaming, scoped to the authenticated user
(`owner_id`).

!!! note "Deferred to a later release"
    The plan's server-authoritative **merge-by-id** policy (reconciling stored
    history with the posted messages so the client can only append, not rewrite,
    past turns) and the owner-scoped **GET `conversations/<thread_id>/`**
    rehydration endpoint are designed but **not yet implemented** in this
    package. Today the store mirrors the run's messages on completion; the
    client remains the source of truth for the posted history.

## The drf-mcp toolset bridge

With the [`[drf-mcp]` extra](installation.md#the-drf-mcp-extra) installed and
[`DRF_MCP_SERVER`](configuration.md#drf_mcp_server) set, the view builds a
per-request `DrfMcpToolset` — a Pydantic-AI `ExternalToolset` that exposes a
`djangorestframework-mcp-server` registry's tools to the agent **in-process**,
with no network MCP hop.

- Tool schemas come straight from drf-mcp's serializers.
- Execution routes through drf-mcp's async handler, so serializer validation and
  permissions are honoured exactly as over HTTP.
- The toolset carries the Django `request`, synthesising an `MCPCallContext`
  whose token is `request.user`, so the agent acts as the **logged-in AG-UI
  user**.

The bridge is imported lazily, only when `DRF_MCP_SERVER` is set, keeping
`rest_framework_mcp` an optional dependency.
</content>
