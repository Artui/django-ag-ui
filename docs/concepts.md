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
as the card label). **The wire stays vanilla AG-UI** — this gating is purely
client-side and applies only to **client-registered** tools. Server-side tools
(this package's `@tool` registry and drf-mcp-bridged tools) run mid-stream and
are **not** gated: `x-destructive` reaches the LLM as a schema hint, but no
server-side confirmation happens today. A real server-side gate is planned (a
`ToolGuard` + typed `ask_user` mechanism).
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
`audit_logger` is set, `build_agent` composes an
[`AuditCapability`][django_ag_ui.AuditCapability] — a Pydantic-AI capability on
the `wrap_tool_execute` lifecycle hook — that times and records **every** tool
the agent runs: registry tools and composed toolsets (drf-mcp / spec /
attachment / skill tools) alike. Frontend tools declared in the request are
merged by the adapter and are not registered here.

For total control over construction, set
[`AGENT_FACTORY`](configuration.md#agent_factory) to a callable matching
[`AgentFactoryFn`][django_ag_ui.AgentFactoryFn]; it replaces `build_agent`
entirely.

## The audit boundary: the `AuditLogger` protocol

[`AuditLogger`][django_ag_ui.AuditLogger] is a runtime-checkable Protocol with a
single method, `record(event: AuditEvent)`. Each
[`AuditEvent`][django_ag_ui.AuditEvent] is a frozen record of one tool
invocation: tool name, a string-ified arguments repr, duration in milliseconds,
a success flag, and either an error string or a result size — plus optional
request/tenancy context (`ip_address`, filled by the view from the driving
request; `organization_id` / `target_type` / `target_id` for custom sinks that
know their tenancy and domain objects).

Recording is **non-raising**: a sink that throws is caught by the
[`AuditCapability`][django_ag_ui.AuditCapability] and logged to the
`django_ag_ui.audit` Python logger, so a broken audit backend degrades to lost
audit records, never a broken agent run.

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
   `{"error": "authentication required"}`. `get_user` may be **sync or
   async**; sync hooks run off the event loop in Django's sync executor, so
   the canonical token lookup Just Works:

    ```python
    def get_user(request):
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return Token.objects.select_related("user").get(key=token).user
    ```

    Without a hook, the middleware-provided lazy `request.user` is
    materialized in a worker thread before the gate — with Django's
    DB-backed sessions, touching it on the event loop would raise
    `SynchronousOnlyOperation`. The catalog views (`ToolsView` and `SkillsView`) accept the same
    `require_authenticated` / `get_user` pair, so one policy covers the
    agent endpoint and the catalogs it advertises.
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

[`AGUIServer`][django_ag_ui.AGUIServer] builds this view (plus its sub-views)
from the registry and exposes a namespaced [`.urls`][django_ag_ui.AGUIServer.urls]
tuple you mount at any prefix — see [Mounting](#mounting) below.

## Mounting

[`AGUIServer`][django_ag_ui.AGUIServer] is the package's front door — one
instance-configured object holding the registry, stores, and auth policy, mounted
the `django.contrib.admin` `site.urls` way. Construct it **once** and `include()`
its [`.urls`][django_ag_ui.AGUIServer.urls]:

```python
from django.urls import path

from django_ag_ui import AGUIServer

agent = AGUIServer(registry, require_authenticated=True)

urlpatterns = [
    path("agent/", agent.urls),
]
```

- **The registry is passed once.** The object builds the agent view *and* the
  tool catalog from it — no `tools=registry` echo.
- **You choose the mount point** the Django way (`path("<prefix>", agent.urls)`);
  there is no `prefix=` argument.
- **`.urls` is namespaced.** It returns the `(patterns, app_name, namespace)`
  triple `path()` mounts directly (like `admin.site.urls` — no `include()`), so
  the endpoints reverse as `reverse("ag_ui:endpoint")`,
  `"ag_ui:tools"`, `"ag_ui:skills"`, `"ag_ui:threads"`, `"ag_ui:thread"`,
  `"ag_ui:attachments"`, `"ag_ui:attachment"`, `"ag_ui:transcribe"`. Two mounts
  don't collide; override the namespace with `namespace="…"`.
- **Sub-views mount when their backend is active.** The agent endpoint and its
  tool catalog always mount; `skills` mounts when a `SkillRegistry` is passed;
  `threads` / `attachments` / `transcribe` mount when their store/backend
  (resolved from settings by default, or passed explicitly) is not the `Null`
  one. A bare `AGUIServer(registry)` mounts only `endpoint` + `tools`.
- **One auth policy covers the whole mount.** `require_authenticated` /
  `get_user` / `authorize` forward to every view the object builds.

Because the object holds its own registry and config, you can mount several with
independent registries — one per surface — each namespaced separately.

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
[`AGUIServer`][django_ag_ui.AGUIServer] mounts at `<prefix>skills/` (named
`skills`) when you pass `skills=`:

```python
urlpatterns = [
    path("agent/", AGUIServer(registry, skills=skills).urls),
]
```

The web component fetches this endpoint via its `data-skills-url` attribute.

### Agent skills (progressive disclosure)

Palette skills are prompts a *human* picks. The second kind —
[`AgentSkill`][django_ag_ui.AgentSkill], served by a
[`SkillsCapability`][django_ag_ui.SkillsCapability] — is discovered and
activated by the *agent* via progressive disclosure: up front the model sees
only each skill's name + description (a couple of instruction lines) and two
tools, `search_skills` and `activate_skill`. Activating a skill injects its
full `instructions` into the **model context** — never the visible transcript —
and makes its scoped `tools` callable. A skill that bundles files gets a
path-traversal-guarded `read_skill_resource` tool while active. Activation
state is per run: every run starts with no skills active.

Build skills programmatically, or load `SKILL.md` bundles (the
[agentskills.io](https://agentskills.io) interop format — a `---`-fenced
`name:`/`description:` frontmatter followed by the instructions body) with
[`load_skill_directories`][django_ag_ui.load_skill_directories]:

```python
from django_ag_ui import AGUIServer, AgentSkill, SkillsCapability

agent_skills = SkillsCapability(
    [AgentSkill(name="triage", description="Triage a bug report.", instructions="...")],
    directories=[BASE_DIR / "skills"],
)

urlpatterns = [
    path("agent/", AGUIServer(registry, agent_skills=agent_skills).urls),
]
```

`AGUIServer(agent_skills=...)` composes the capability into the agent and
appends the skills to the GET catalog as `{"name", "description", "agent": true}`
entries — promptless, so a palette-only client ignores them. Outside
`AGUIServer`, compose the capability through
[`CAPABILITIES`](configuration.md#capabilities) or
`AgentConfig.capabilities` directly.

## Tool metadata catalog

Server-side tools — the `@tool` registry and (when
[`DRF_MCP_SERVER`](configuration.md#drf_mcp_server) is set) the drf-mcp tools —
execute **server-side**, so their JSON Schema never reaches the browser. A client
therefore can't read an `x-summary` off the schema to label a tool-call card.
The **tool catalog** is the channel for those labels: a small read-only JSON
endpoint the web component fetches via its `data-tools-url` attribute and uses to
map a tool name → a friendly card label.

[`build_tool_catalog(registry)`](api.md#django_ag_ui.agent.build_tool_catalog.build_tool_catalog)
builds the catalog as a list of entries, each
`{"name", "summary", "description"?}`:

- `summary` is **always present**, resolved from a fallback chain:
  registry tools use `@tool(summary=…)`
  ([`ToolSpec.summary`][django_ag_ui.ToolSpec]) → a prettified tool name
  (`query_model` → `"Query model"`); drf-mcp tools use `display_name` → `title`
  → a prettified name.
- `description` (a longer blurb, e.g. for a tooltip) is included only when
  available — `ToolSpec.description` for registry tools, or drf-mcp
  `display_description` → `description`.

Registry tools win on name collisions. The drf-mcp `display_name` /
`display_description` are drf-mcp's **binding metadata** (consumer-only, never on
the MCP wire), so the catalog surfaces friendly labels for those tools too.

`ToolsView` (`django_ag_ui.ToolsView`) — a GET-only callable view holding the
same [`ToolRegistry`][django_ag_ui.ToolRegistry] the agent uses — serves the
catalog. [`AGUIServer`][django_ag_ui.AGUIServer] builds it from the registry you
pass and mounts it at `<prefix>tools/` (named `tools`) automatically — no extra
argument:

```python
urlpatterns = [
    path("agent/", AGUIServer(registry).urls),
]
```

## Conversation persistence

By default the server is **stateless**: the conversation lives in the message
history the client posts on every turn. Persistence is opt-in via
[`CONVERSATION_STORE`](configuration.md#conversation_store) and modelled as a
pluggable Protocol, exactly like the audit logger.

[`ConversationStore`][django_ag_ui.ConversationStore] is a runtime-checkable
Protocol with async `load` / `save` / `delete`, plus `list` and `rename` for the
thread drawer (see [Thread history](#thread-history) below), each taking the
`request`. A [`Conversation`][django_ag_ui.Conversation] is a frozen record of a
`thread_id`, the AG-UI `Message` list (the wire shape, round-tripped verbatim),
and an `owner_id` for authorization scoping.

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
  implements the synchronous row operations (`_fetch`, `_store`, `_remove`, and
  the opt-in `_list` / `_rename`, which default to `[]` / no-op) against its own
  Django model. The package ships **no** concrete model on purpose, so it forces
  no migration — you define the model, its fields, and the owner relationship.
  For a ready-made one, see the [reference store](#a-ready-made-durable-store).

When a non-null store is configured, the view persists the run's full message
history when the run finishes streaming, scoped to the authenticated user
(`owner_id`).

### Thread history

The store also powers a **chat-history drawer**: a user's past conversations,
each loadable, renamable, and deletable. Two Protocol methods back it, both
owner-scoped:

- `list(*, request)` returns [`ConversationMeta`][django_ag_ui.ConversationMeta]
  — `thread_id`, `title`, `updated_at`, `preview` — **metadata only**, no message
  bodies, so the drawer stays cheap. `NullConversationStore` returns `[]`;
  `DjangoSessionConversationStore` enumerates the session's own threads (titles
  derived from the first user message, previews from the latest);
  `ModelConversationStore._list` defaults to `[]` until a subclass overrides it.
- `rename(thread_id, title, *, request)` sets a thread's display title. The
  session store persists it (overriding the derived title);
  `ModelConversationStore._rename` is a no-op until overridden.

[`AGUIServer`][django_ag_ui.AGUIServer] mounts [`ThreadsView`][django_ag_ui.ThreadsView]
automatically whenever the conversation store is active (a non-`Null` store,
resolved from [`CONVERSATION_STORE`](configuration.md#conversation_store) by
default or passed as `conversation_store=`), exposing them over HTTP for the web
component's `data-threads-url`:

| Route | Method | Action |
| --- | --- | --- |
| `<prefix>threads/` | `GET` | list the user's threads (metadata only) |
| `<prefix>threads/<id>/` | `GET` | that thread's messages (server-side rehydration) |
| `<prefix>threads/<id>/` | `PATCH` | rename (`{"title": "..."}`) |
| `<prefix>threads/<id>/` | `DELETE` | delete the thread |

Every operation is scoped to the acting user — a thread owned by someone else
reads as `404`, never another user's history — and the view carries the same
`require_authenticated` / `get_user` auth seam as `DjangoAGUIView`.

### A ready-made durable store

For cross-device, per-user history without writing your own model, opt into the
`django_ag_ui.contrib.store` app: add `"django_ag_ui.contrib.store"` to
`INSTALLED_APPS`, run `migrate`, and set
[`CONVERSATION_STORE`](configuration.md#conversation_store) to
`django_ag_ui.contrib.store.default_conversation_store.DefaultConversationStore`.
It ships a `StoredConversation` model and a `ModelConversationStore` subclass with
denormalised `title` / `preview` / `updated_at` columns so the thread list is a
single cheap query. Projects that don't opt in get no model and no migration.

!!! note "Deferred to a later release"
    The plan's server-authoritative **merge-by-id** policy (reconciling stored
    history with the posted messages so the client can only append, not rewrite,
    past turns) is designed but **not yet implemented**; today the store mirrors
    the run's messages on completion and the client remains the source of truth
    for the posted history. (The owner-scoped rehydration endpoint **is** now
    shipped — `GET <prefix>threads/<id>/` above.)

## File uploads

A user can attach files to a conversation — drop a PDF or image into the
composer, send a message, and let the agent read it. The design keeps the AG-UI
wire **vanilla**: files upload out-of-band to their own endpoint and travel as
lightweight **refs** (`id` / `name` / `mime` / `size`), never as base64 on the
message stream — the same principle the [tool metadata
catalog](#tool-metadata-catalog) uses to keep schemas off the wire.

The lifecycle:

1. The composer uploads each file (multipart `POST <prefix>attachments/`) and
   gets back an [`AttachmentRef`][django_ag_ui.AttachmentRef] — a durable handle,
   not bytes.
2. The user sends a message carrying the refs.
3. When the model needs a file's contents, it calls the built-in
   `read_attachment(attachment_id)` tool, which resolves the bytes **server-side,
   owner-scoped to the acting user**.

### The store

[`AttachmentStore`][django_ag_ui.AttachmentStore] is the persistence seam, set
via [`ATTACHMENT_STORE`](configuration.md#attachment_store). Every method is
async and owner-scoped — one user's id can never resolve another's file, the
security boundary for the feature. The default
[`NullAttachmentStore`][django_ag_ui.NullAttachmentStore] keeps uploads off
(`410 Gone`); subclass the abstract
[`ModelAttachmentStore`][django_ag_ui.ModelAttachmentStore] for your own model,
or opt into the [ready-made durable store](#a-ready-made-durable-store) which
keeps bytes in Django `Storage` (filesystem by default, S3/GCS via `STORAGES`).

### The endpoints

[`AGUIServer`][django_ag_ui.AGUIServer] mounts
[`AttachmentsView`][django_ag_ui.AttachmentsView] automatically whenever the
attachment store is active (a non-`Null` store, resolved from
[`ATTACHMENT_STORE`](configuration.md#attachment_store) by default or passed as
`attachment_store=`):

- `POST   <prefix>attachments/`      — multipart upload under the `file` field;
  validates [size](configuration.md#attachment_max_bytes) and
  [type](configuration.md#attachment_allowed_types) **server-side**, then returns
  `201` with the ref JSON.
- `GET    <prefix>attachments/<id>/` — stream the bytes back (owner-checked), as
  an `attachment` with `X-Content-Type-Options: nosniff` so an uploaded
  `text/html` can't execute as a same-origin page; missing / cross-owner → `404`.
- `DELETE <prefix>attachments/<id>/` — drop the attachment (`204`).

All owner-scoped and open by default like the catalog views — construct
`AttachmentsView` yourself with `require_authenticated` / `get_user` to lock it
down whenever the agent endpoint is. The web component reads
`data-attachments-url` to drive the composer's upload tray.

## Cancelling a run

AG-UI has **no server-side cancel route**. A run is one streaming HTTP request;
the client cancels it by aborting that request, and the server observes a
disconnect. The view handles that disconnect explicitly rather than leaving the
teardown to garbage collection:

- **Provider teardown is guaranteed.** The view keeps a reference to the
  innermost event generator — the one whose context manager owns the model
  provider's streaming request — and closes it when the disconnect surfaces, so
  no orphaned upstream generation keeps running (or billing) after the client
  stopped listening.
- **The partial exchange is persisted.** With a non-null
  [`CONVERSATION_STORE`](configuration.md#conversation_store) configured, the
  truncated conversation — the client-posted history plus whatever assistant
  text and completed tool calls streamed before the disconnect — is saved with
  the same thread/owner scoping as a completed run, so a durable thread
  reflects reality. Partially streamed *tool calls* are dropped (half a JSON
  arguments string is not a usable record). With the default
  `NullConversationStore`, nothing is saved.
- **The cancellation is audited.** The configured
  [`AuditLogger`][django_ag_ui.AuditLogger] receives a run-level
  [`AuditEvent`][django_ag_ui.AuditEvent] with `tool_name="agent.run"`,
  `success=False`, and an `error` starting with `"cancelled:"` —
  distinguishable from tool failures in logs/Sentry without widening the
  protocol. `duration_ms` measures run start → cancellation.
- **Cancellation is never swallowed.** The guard re-raises after observing;
  failures inside the persist/audit step are logged and do not replace the
  cancellation.

There is no setting to turn this off — cancellation handling is
transport-level, and partial persistence simply follows the store you already
configured (matching the client, which keeps the partial assistant bubble).

## The drf-mcp toolset bridge

With the [`[drf-mcp]` extra](installation.md#the-drf-mcp-extra) installed and
[`DRF_MCP_SERVER`](configuration.md#drf_mcp_server) set, the view builds a
per-request `DRFMCPToolset` — a Pydantic-AI toolset (an `AbstractToolset`
subclass) that exposes a
`djangorestframework-mcp-server` registry's tools to the agent **in-process**,
with no network MCP hop.

- Tool schemas are sourced from drf-mcp's own `tools/list` (via its public
  `MCPServer.list_tools`), so the agent sees the full advertised `inputSchema` —
  including a selector tool's filter / ordering / pagination arguments and the
  `additionalProperties` policy — not just the input serializer's fields.
- Execution routes through drf-mcp's public `MCPServer.acall_tool` (its in-process
  transport surface, drf-mcp 0.9+), so serializer validation and permissions are
  honoured exactly as over HTTP — without reaching into handler internals.
- The toolset hands the Django `request` and `request.user` to those methods, so
  the agent acts as the **logged-in AG-UI user**.

The bridge is imported lazily, only when `DRF_MCP_SERVER` is set, keeping
`rest_framework_mcp` an optional dependency.
</content>
