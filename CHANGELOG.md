# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.21.0] — 2026-07-23

### Changed

- **The agent-host substrate now lives in
  [`django-pydantic-agent`](https://github.com/Artui/django-pydantic-agent)**, a new
  settings-agnostic package this release depends on. Agent construction, the tool
  registry, toolset/capability composition, audit, the tool guard, user resolution,
  the storage contracts and the reference store models moved there; this package
  keeps the AG-UI transport — the view, the SSE stream, `AGUIServer.urls`, the
  browser-facing sub-views, skills and transcription. It is the lift-down that lets
  a second transport share one substrate.

  **The public surface is unchanged**: every moved symbol is **permanently
  re-exported**, so `from django_ag_ui import ToolRegistry` (and friends) keeps
  working and downstream projects need only a version bump.

### Breaking

- **`INSTALLED_APPS`**: the reference store app moved — replace
  `"django_ag_ui.contrib.store"` with `"django_pydantic_agent.contrib.store"`.
- **The model stores no longer read `DJANGO_AG_UI["ALLOW_ANONYMOUS"]`.** A
  settings-agnostic substrate cannot read a transport's settings key, so pass
  `allow_anonymous=True` to the store constructor instead.
- **`Conversation.messages` holds JSON-serialisable records, not `ag_ui` `Message`
  objects.** The substrate persists transport-owned records verbatim (client
  message ids survive untouched) and the AG-UI wire shape is converted at this
  package's boundary. Code that read `message.content` off a loaded conversation
  now reads `message["content"]`; `messages_to_jsonable` / `messages_from_jsonable`
  live in `django_ag_ui.persistence.utils`.
- The attachment toolset's internal id is now `django-pydantic-agent-attachments`.
  Tool names (`read_attachment`) and the wire are unaffected.

## [0.20.0] — 2026-07-22

### Added

- **Durable step persistence** — a model-backed, owner-scoped store for
  `pydantic-ai-harness`'s `StepPersistence` capability. Pass
  `AGUIServer(step_store=DefaultStepStore)` (the constructor *is* the
  `request -> StepStore` factory — the harness protocol carries no request, so
  the store binds one and is built per run) and every run records an append-only
  event log, a `(run_id, tool_call_id)` tool-effect ledger, and a continuable
  snapshot at each provider-valid boundary, keyed on the AG-UI `run_id`. Four new
  models under `django_ag_ui.contrib.store` (`StoredRun` / `StoredStepEvent` /
  `StoredSnapshot` / `StoredToolEffect`, migration `0003`) back
  `DefaultStepStore`, which structurally satisfies the harness `StepStore`
  protocol. Every row filters by the resolved owner, so a `run_id` from one user
  can't read another's runs; an anonymous request without `ALLOW_ANONYMOUS`
  degrades to no-op rather than aborting the run. Requires the `[harness]` extra.
  A custom backend is any `request -> StepStore` callable. See
  [Durable step persistence](https://artui.github.io/django-ag-ui/step-persistence/).
- **Resume / fork endpoints** — configuring a `step_store` also mounts owner-scoped
  `resume/<run_id>/` and `fork/<run_id>/` endpoints. Both seed a new run from a
  prior run's last continuable snapshot: the server loads it (a `run_id` from
  another owner is a clean `404`), injects it as the run's `message_history`
  (`AgentSession` gained the seam; `run_stream_native` composes it ahead of the
  client's new turn), and records the new run with `parent_run_id` pointing back
  at the source — so the parent is never mutated. `resume` and `fork` are two
  names for one mechanism (the harness's `continue_run` / `fork_run` are
  data-identical). The web-component checkpoint UI rides a downstream release.

## [0.19.0] — 2026-07-17

Configuration is now **per-endpoint**: collaborators are constructor arguments
taking real objects, and `DJANGO_AG_UI` is no longer read on the request path at
all. This makes running more than one AG-UI endpoint in one project actually
work — previously two mounts served, but only their tool registries could
differ. Everything else — toolsets, capabilities, the tool-guard policy, the
drf-mcp bridge, retry budgets, upload caps — was global.

**Breaking**, with a migration. Ten settings that named a class by dotted path
are removed (they raise if left in place, naming the replacement), `AppSettings`
/ `get_settings` and the `resolve_*` helpers are gone from the public API, and
settings now resolve when a server is built rather than per request. Every
remaining setting survives as a **default**, so a single-endpoint project that
configures settings and passes nothing keeps working.

The dotted paths only ever existed because `settings.py` cannot hold a live
object. `urls.py` can — which is also why `drf_mcp_server=internal_mcp` is
expressible at all: with one global path there was no way to say *which* agent
bridges to *which* MCP server. There is now no `import_string` anywhere in the
package.

See [Multiple endpoints](https://artui.github.io/django-ag-ui/configuration/#multiple-endpoints).

### Added

- **`AGUIConfig` + `build_ag_ui_config()`** — an endpoint's scalars, resolved
  **once** in `AGUIServer.__init__` and threaded to the agent view and every
  sub-view. Thirteen `get_settings()` calls across eight files re-read the
  settings on every request; read there they could only ever be global.

  ```python
  AGUIServer(registry, config=build_ag_ui_config(retries=5))
  ```

  Use `build_ag_ui_config(**overrides)` rather than constructing `AGUIConfig`
  directly — it layers your overrides over the project's settings instead of
  discarding them.

- **Collaborators as constructor arguments** on `AGUIServer` / `DjangoAGUIView`:
  `toolsets=`, `capabilities=`, `agent_factory=`, `drf_mcp_server=`,
  `service_specs=`, `provider=` (the stores already were). Passing objects lifts
  a constraint the dotted paths imposed — a collaborator needing constructor
  arguments could not be named by path, so `audit_logger=LoggingAuditLogger(...)`
  and `SimpleJWT`-style configured instances now just work.

- **`ScopedConversationStore`** — partitions a store between endpoints:

  ```python
  AGUIServer(registry, conversation_store=ScopedConversationStore(store, scope="internal"))
  ```

  Stores key threads by `(owner_id, thread_id)`, so two endpoints sharing one
  shared a user's thread list: a conversation started at `/internal/agent`
  appeared in `/public/agent`'s drawer and resumed there under the *public*
  agent's model, tools and guard policy. Prefixing the storage key avoids a
  migration and a `ConversationStore` protocol break (which would hit every
  custom store). Opt-in and explicit — wrapping automatically from the server's
  `namespace` would silently orphan an existing project's history.

  There is deliberately no `ScopedAttachmentStore`: attachments are
  id-referenced with no enumeration and already owner-scoped, so two endpoints
  sharing a store expose nothing across the user boundary. Thread *lists* are
  the case that leaks.

- `ModelConversationStore(allow_anonymous=)` / `ModelAttachmentStore(allow_anonymous=)`.
  `ALLOW_ANONYMOUS` turned out to be a **store** policy — only the model stores
  read it — so it lives there, with `DJANGO_AG_UI["ALLOW_ANONYMOUS"]` as the
  default. A subclass that overrides `__init__` and forgets `super()` fails
  **closed** (refusing anonymous requests) rather than defaulting open.

### Changed

- **`DJANGO_AG_UI` is no longer read on the request path.** Mutating settings no
  longer reconfigures an already-built server. If your tests wrap a request in
  `override_settings(DJANGO_AG_UI=...)` against a server built at URL-conf
  import, the change is now ignored — build the server inside the test with
  `config=build_ag_ui_config(...)` instead.

- `AgentFactoryFn` receives `AGUIConfig` instead of `AppSettings`:
  `(registry: ToolRegistry, config: AGUIConfig) -> Agent`.

- `build_tool_catalog(registry, *, drf_mcp_server=, service_specs=)`,
  `resolve_owner_id(request, *, allow_anonymous=)`, and the `ToolsView` /
  `ThreadsView` / `AttachmentsView` / `TranscribeView` constructors take the
  values they used to read from settings. Only affects code calling these
  directly.

- `provider=` no longer accepts a dotted-path string — pass the `Provider`.

- The `[drf-mcp]` extra now requires `djangorestframework-mcp-server>=0.12,<0.13`
  (was `>=0.9,<0.12`), picking up its per-server identity, session namespacing
  and RFC 8707 audience binding.

### Removed

- **Ten dotted-path settings**: `AGENT_FACTORY`, `TOOLSETS`, `CAPABILITIES`,
  `AUDIT_LOGGER`, `CONVERSATION_STORE`, `ATTACHMENT_STORE`,
  `TRANSCRIPTION_BACKEND`, `DRF_MCP_SERVER`, `SERVICE_SPECS`, `PROVIDER`. Each
  raises `ImproperlyConfigured` naming its replacement if left in settings —
  a silently-ignored `TOOLSETS` would mean an agent quietly losing its tools.

  ```python
  # before — settings.py
  DJANGO_AG_UI = {
      "TOOLSETS": ("myproject.toolsets.weather",),
      "CONVERSATION_STORE": "myproject.stores.MyStore",
  }

  # after — urls.py
  AGUIServer(registry, toolsets=[weather], conversation_store=MyStore())
  ```

- **`AppSettings` and `get_settings`** — a process-global settings snapshot is
  precisely what made two endpoints indistinguishable. `conf.py` keeps one
  primitive, `get_setting(name, default)`. Use `AGUIConfig` /
  `build_ag_ui_config` for an endpoint's resolved scalars.

- **`resolve_audit_logger`, `resolve_conversation_store`,
  `resolve_attachment_store`, `resolve_transcription_backend`,
  `resolve_agent_factory`, `resolve_dotted_instances`** — the whole dotted-path
  resolver layer, which existed only to turn strings back into objects.

## [0.18.1] — 2026-07-16

### Added

- Docs recipe: [Delegating to sub-agents](subagents.md) — wire the
  `pydantic-ai-harness` `SubAgents` capability through the existing
  `CAPABILITIES` seam (the `[harness]` extra) to give the agent a
  `delegate_task` tool over a roster of named child agents. Zero new package
  code — a stateless capability adopted like CodeMode; covered by an
  `importorskip`-guarded recipe test. Per-delegate limits (`usage_limits` /
  `timeout_seconds` / `max_calls` / `on_failure`) are fields on `SubAgent`.

## [0.18.0] — 2026-07-14

### Added

- **`[harness]` extra + a CodeMode batching recipe.** Optional
  `pip install django-ag-ui[harness]` pulls `pydantic-ai-harness` (and its
  `pydantic-monty` sandbox) — lazy-imported, so the core install stays `django` +
  `pydantic-ai-slim`. Its `CodeMode` capability drops into the existing
  `CAPABILITIES` seam to collapse many tools (notably a large drf-mcp bridge) into
  one sandboxed `run_code` tool the model batches in a single round-trip. New
  [CodeMode guide](code-mode.md).

### Changed

- **The drf-mcp bridge now carries each tool's `outputSchema` onto
  `ToolDefinition.return_schema`.** drf-mcp advertises an output schema by default
  (`INCLUDE_OUTPUT_SCHEMA`); the bridge previously dropped it. Propagating it means
  the tool's return type reaches the model — chiefly so CodeMode renders each
  bridged tool as a **typed** Python stub (`-> <Model>`) instead of `-> Any`. A
  service with no output serializer advertises no schema, so its stub stays
  untyped (unchanged). No effect on tool dispatch or results.

### Documentation

- **New "Tool approval (human-in-the-loop)" guide** documenting the end-to-end
  approval flow: enabling `TOOL_GUARD`, what counts as destructive, what the user
  sees (approve / deny / resume), how a custom (non-web-component) client drives
  the interrupt/resume loop, and the `ask_user` typed-question tool. Cross-linked
  from the `TOOL_GUARD` configuration section.

## [0.17.0] — 2026-07-14

### Added

- **Server-side tool-approval interrupt/resume loop (HITL, part 1 — "turn it on").**
  A tool flagged `requires_approval` now finishes the run on a `RUN_FINISHED`
  *interrupt* outcome (carrying the tool call id and an approve/deny/edit response
  schema) instead of executing; a follow-up run carrying `RunAgentInput.resume[]`
  approves (runs the tool), denies, or overrides its arguments. The entire
  lifecycle is upstream (pydantic-ai + the AG-UI adapter) and was already driven by
  `AgentSession` — this release unlocks it:
  - Pins a direct `ag-ui-protocol>=0.1.19,<0.2` dependency (the interrupt/resume
    protocol floor; `pydantic-ai-slim` only floors it at `>=0.1.10`).
  - Puts `DeferredToolRequests` in the agent `output_type`, so approval works for
    **server-side** tools too — the AG-UI adapter only augments `output_type` when a
    run carries *frontend* tools, so a server-only gated tool would otherwise crash
    the run with a `RUN_ERROR`.

  This part turns the loop *on*; the `TOOL_GUARD` policy below is what flags tools.

- **`ToolGuard` — opt-in server-side approval gate (HITL, part 2 — the policy).**
  A new `DJANGO_AG_UI["TOOL_GUARD"]` setting composes a `ToolGuard` capability
  that flips **destructive** server-side tools to require approval (`kind=
  "unapproved"`) at `prepare_tools` time, so they defer to the interrupt loop
  above instead of running mid-stream. Off by default — no surprise gates.
  - Destructiveness is unified from three sources: a registry
    `@tool(destructive=True)`; a drf-mcp tool whose MCP `readOnlyHint` is `False`
    (the bridge now reads the tool `annotations` it previously discarded and
    stamps a `DESTRUCTIVE_METADATA_KEY` onto the tool definition — **no drf-mcp
    release needed**); and per-name `REQUIRE_APPROVAL` / `EXEMPT` overrides
    (`EXEMPT` wins).
  - Only `function` tools are flipped — an `external` (frontend) tool is already
    gated client-side, an `output` tool isn't executed.
  - See [`TOOL_GUARD`](configuration.md#tool_guard) for the settings shape.

### Changed

- **`AuditCapability` now declares its composition order** (`get_ordering()` →
  outermost) instead of relying on list position at the `build_agent` call site.
  This makes capability composition deterministic now that a second capability
  (`ToolGuard`) can join the chain: pydantic-ai's `CombinedCapability`
  topologically sorts by these constraints, so audit stays outermost (wrapping
  every tool execution) regardless of insertion order. No behavioural change with
  a single capability.

## [0.16.0] — 2026-07-13

### Changed

- **`SERVICE_SPECS` now uses `SpecCapability` instead of a bare `SpecToolset`.**
  The exposed tool set is byte-identical, but the spec path
  is now a Pydantic-AI *capability* on `AgentConfig.capabilities`, so it also
  teaches the model `SpecToolset`'s conventions — that list tools accept
  `page` / `limit` / `order`, and the error contract (an `{"error": …}` result is
  a final answer, a retry message means fix the argument, a permission error is
  final) — via instructions appended to the system prompt, closing the gap where
  the model rediscovered them by failing a call. Requires
  `djangorestframework-pydantic-ai>=0.5` (the `[spec-tools]` extra pin moves from
  `>=0.2,<0.4` to `>=0.5,<0.6`). Name-collision precedence, the tool-card catalog,
  and the per-request user binding are unchanged. The internal
  `django_ag_ui.integrations.build_spec_toolset` helper is replaced by
  `build_spec_capability` (not a public export).

## [0.15.0] — 2026-07-10

### Added

- **`AuditCapability`.** The audit boundary re-modelled as a Pydantic-AI
  capability on the `wrap_tool_execute` lifecycle hook, so **every** tool the
  agent runs is audited — registry tools and composed toolsets (drf-mcp / spec /
  attachment) alike, where the old per-tool wrapper saw only registry tools.
  `AuditEvent` gains optional context fields: `ip_address` (filled by the view
  from the driving request via the new `AgentConfig.audit_ip_address`), and
  `organization_id` / `target_type` / `target_id` for custom sinks;
  `LoggingAuditLogger` appends them to the log line when set. Recording is
  **non-raising** — a sink that throws is caught and logged to the
  `django_ag_ui.audit` Python logger, so a broken audit backend degrades to
  lost audit records instead of a broken agent run.
- **`AgentSession`** — one run's orchestration (adapter, stream composition,
  persistence, cancel handling) extracted from `DjangoAGUIView` into a public
  class, so the pipeline is drivable as a plain async iterator (testable apart
  from SSE, reusable under another transport). The view keeps its exact
  behaviour and public API.
- **`MANAGE_SYSTEM_PROMPT` setting** (`"server"` default): who owns the system
  prompt on the AG-UI wire. `"server"` strips a client-posted system message
  before it reaches the model; `"client"` honours it.
- **`ALLOW_UPLOADED_FILES` setting** (`False` default): whether `UploadedFile`
  references in client-submitted messages are honoured — they are fetched with
  the server's credentials, so they stay off unless the client is trusted. The
  attachment flow is unaffected (it travels server-issued refs in message text,
  not AG-UI file parts).

### Changed (breaking)

- **The `pydantic-ai-slim` floor moves to `>=2,<3`** (core and the
  `anthropic` / `openai` / `google` extras): the capability seam
  `AuditCapability` is built on (`pydantic_ai.capabilities`) and the AG-UI
  adapter's server-trust knobs are v2-only. The 1.x line is no longer supported.

### Verified

- Pydantic-AI's `sanitize_messages` hardening runs on the view's hand-composed
  streaming path (client-posted system prompts are stripped by default), the
  reasoning filter's `REASONING_*` event naming holds on the locked 2.x, and
  the attachment flow is unaffected by the `allow_uploaded_files` default —
  each now pinned by session-level tests.

## [0.14.0] — 2026-07-10

### Added

- `DRFMCPToolset(max_retries=...)` — each tool's retry budget: how many times a
  `ModelRetry` (malformed arguments, a service-raised validation error) is fed
  back to the model before the run aborts. Defaults to `1`, matching
  pydantic-ai's own function-tool default.

### Changed (breaking)

- **`DrfMcpToolset` is renamed `DRFMCPToolset`**, matching the capitalized
  acronyms of its sibling classes (`MCPServer`, `AGUIServer`) and PEP 8's
  CapWords convention. The class is built internally by the view from the
  `DRF_MCP_SERVER` setting, so only code importing it directly from
  `django_ag_ui.integrations.drf_mcp` needs the one-line rename; no alias is
  kept.

### Changed

- `DRFMCPToolset` now subclasses `pydantic_ai.toolsets.AbstractToolset`
  directly (the documented extension point) instead of `ExternalToolset`,
  building its tool definitions `kind="function"` from the start. Previously
  it inherited from a base class that models the opposite of in-process
  execution (external tools are *deferred* to the client) and re-stamped every
  tool definition back to `kind="function"` per run — the version-fragile seam
  behind the historically tight `<2` pydantic-ai pin. Public API and tool
  behaviour are otherwise unchanged.

### Fixed

- A bridged tool's `ModelRetry` (malformed arguments, a service-raised
  validation error) now actually reaches the model to self-correct, as
  documented. `ExternalToolset` pinned every tool's retry budget to `0`, so in
  a real agent run the first `ModelRetry` aborted the run with
  `UnexpectedModelBehavior` instead of retrying. Pinned by a full agent-run
  integration test.

## [0.13.0] — 2026-07-09

### Removed

- Remove the inert server-side confirmation machinery: the `needs_confirmation`
  helper (and its `django_ag_ui.policy.auto_confirm` module) and the
  `AUTO_CONFIRM` setting / `AppSettings.auto_confirm` field. These never gated
  anything — server-side tools execute mid-stream, so `@tool(destructive=True)`
  only ever reached the LLM as an `x-destructive` schema hint, never a runtime
  gate. Per-tool `destructive=` / `confirm=` metadata and the `x-destructive` /
  `x-confirm` schema stamps are unchanged (they remain LLM/client hints, and the
  web component still gates *client-registered* tools). A real server-side gate is
  planned separately (a `ToolGuard` + typed `ask_user` mechanism).
  **Breaking:** the `needs_confirmation` export and the `AUTO_CONFIRM` setting are
  gone; a project that set `AUTO_CONFIRM` should drop it (it was a no-op).

## [0.12.1] — 2026-07-08

### Changed

- Widen the `pydantic-ai-slim` dependency constraint from `>=1.0,<2` to
  `>=1.0,<3` (core plus the `anthropic` / `openai` / `google` provider extras),
  so the package installs against Pydantic-AI 2.x. Verified against
  `pydantic-ai-slim` 2.6.0. The 1.x line remains supported.
- Widen the `[spec-tools]` extra's `djangorestframework-pydantic-ai` pin to
  `>=0.2,<0.4` (was `<0.3` — stale; the backing package is at 0.3.x) and the
  `[drf-mcp]` extra's `djangorestframework-mcp-server` pin to `>=0.9,<0.12`
  (was `<0.11`). Together these let a project resolve `pydantic-ai-slim` 2.x
  through the optional bridges without back-tracking to the 1.x line.

### Notes on Pydantic-AI 2.x

`build_model` delegates provider-prefix → model-class resolution to Pydantic-AI
(there is no hand-maintained table), so it inherits these 2.x vocabulary
changes for projects that install `pydantic-ai-slim>=2`:

- The bare `openai:` prefix now builds an `OpenAIResponsesModel` (the Responses
  API) rather than an `OpenAIChatModel`. Use `openai-chat:` for the Chat
  Completions model.
- Bare model names no longer infer a provider — `claude-sonnet-4-5` (no
  `provider:` prefix) previously resolved to Anthropic; it now raises
  `ImproperlyConfigured` pointing at the `PROVIDER` setting. Pass an explicit
  `provider:name` string.
- The `google-gla:` and `google-vertex:` provider prefixes were removed
  upstream; only `google:` remains (our `gemini:` → `google:` alias is
  unaffected).

## [0.12.0] — 2026-07-08

### Added

- **`AGUIServer` — one config object with a namespaced `.urls`.** The
  Django-idiomatic front door for the package: construct it once with the tool
  registry (plus optional stores / skills / auth) and mount its `.urls` the
  `django.contrib.admin` `site.urls` way — `path("agent/", server.urls)`. It
  builds the agent view **and** every sub-view (tools / skills / threads /
  attachments / transcribe) internally from the registry passed **once** (no
  `tools=registry` echo), forwards one `require_authenticated` / `get_user` /
  `authorize` policy to all of them, and mounts each persistence sub-view only
  when its backend is active (a non-`Null` store, resolved from `DJANGO_AG_UI` by
  default or passed explicitly). `.urls` returns the
  `(patterns, app_name, namespace)` triple `path()` mounts directly — the
  `admin.site.urls` idiom, `path("agent/", server.urls)` with no `include()` — so
  endpoints are **namespaced** and reversible — `reverse("ag_ui:endpoint")`,
  `"ag_ui:tools"`, `"ag_ui:threads"`, … — and two mounts no longer collide on flat
  global names. Override the namespace with `namespace=`.

### Removed (breaking)

- **`get_urls` is removed** in favour of `AGUIServer`. It was a free factory that
  returned a bare, un-namespaced pattern list and required pre-building the view
  then re-passing the same registry as `tools=`. Migrate:

  ```python
  # before
  from django_ag_ui import DjangoAGUIView, get_urls
  urlpatterns = [
      *get_urls(DjangoAGUIView(registry), prefix="agent/", tools=registry, threads=store),
  ]

  # after
  from django_ag_ui import AGUIServer
  urlpatterns = [
      path("agent/", AGUIServer(registry, conversation_store=store).urls),
  ]
  ```

  The endpoint URL **names are now namespaced** — update `reverse()` /
  `{% url %}` calls from the old flat names (`django_ag_ui`, `django_ag_ui_tools`,
  `django_ag_ui_threads`, …) to `"<namespace>:endpoint"`, `"<namespace>:tools"`,
  `"<namespace>:threads"`, … (default namespace `"ag_ui"`). The threads /
  attachments / transcribe sub-views now mount automatically when their store is
  configured in settings, so passing them explicitly is only needed to override
  the settings-resolved backend.

## [0.11.1] — 2026-07-03

### Changed

- Adopted the current sibling-package release lines. The `[spec-tools]` extra now
  requires `djangorestframework-pydantic-ai>=0.2,<0.3` — 0.2.0 renamed its import
  to `rest_framework_pydantic_ai`, and `build_spec_toolset` was updated to import
  the new name. The `[drf-mcp]` extra's `djangorestframework-mcp-server` pin was
  widened to `>=0.9,<0.11` to allow the published 0.10.x line.

## [0.11.0] — 2026-07-02

### Added

- **Thread-index cap + `?limit`.** `GET threads/` returns at most
  `DJANGO_AG_UI["THREAD_LIST_LIMIT"]` rows (default 200); the client may request
  fewer via `?limit=N` and a larger value is clamped to the ceiling, so a user
  with thousands of threads no longer fetches and serializes all of them per
  drawer open. `ConversationStore.list` gains a `limit` argument.
- **`ConversationStore.exists()`.** A cheap owner-scoped presence check
  (no message body loaded). The rename endpoint now probes it instead of loading
  and deserializing the whole thread just to 404. Model-backed stores answer with
  a metadata-only `.exists()` query.

### Changed (breaking)

- **`OpenedAttachment.content` is now an open readable stream, not `bytes`.**
  Downloads stream the file via `FileResponse` instead of buffering it
  whole in memory, so a large attachment (with the size cap disabled) no longer
  lands in memory. `AttachmentStore.open` returns the open handle; the download
  view and the `read_attachment` tool consume/close it. A custom
  `AttachmentStore` that built `OpenedAttachment(content=<bytes>)` must now pass a
  readable binary handle.
- **`ConversationStore` gains required `exists()` and a `limit` on `list()`.** A
  custom store implementation must add `exists()` and accept `limit=` on `list()`.

### Fixed

- **Rename / upload title & filename caps.** A `PATCH threads/<id>/`
  title longer than the model's `CharField(max_length=255)` is truncated rather
  than raising a database `DataError` on a strict backend. (Uploaded filenames
  were already truncated to 255 by Django's `UploadedFile`.)
- **Cross-toolset name-collision guard.** The drf-mcp, `SpecToolset`, and
  `read_attachment` toolset builders previously each excluded only the `@tool`
  registry's names, so if `DRF_MCP_SERVER` and `SERVICE_SPECS` exposed the same
  tool name (or either exposed `read_attachment`), pydantic-ai raised a duplicate
  `UserError` mid-run while the catalog looked clean. One `seen` set is now
  threaded through all three builders in `build_tool_catalog`'s precedence order
  (registry → drf-mcp → spec → attachment) so a duplicate can't reach the run.
- **Early upload abort.** A `CappedUploadHandler` aborts an oversized
  upload mid-parse (honoring `ATTACHMENT_MAX_BYTES`) instead of spooling the whole
  body to a temp file before the 413.
- **Transcription client reuse + timeout.** `OpenAITranscriptionBackend`
  caches its `AsyncOpenAI` client on the instance rather than constructing one
  (with a new connection pool) per call, and sets a bounded default `timeout`
  (60 s, overridable) instead of the SDK's 10-minute default.

## [0.10.0] — 2026-07-02

### Added

- **`authorize=` predicate on every view + `get_urls`.** All the mounted views
  (`DjangoAGUIView`, `ToolsView`, `SkillsView`, `ThreadsView`, `AttachmentsView`,
  `TranscribeView`) and `get_urls` now accept an optional
  `authorize(request) -> bool` predicate, run *after* the acting user is
  established. A failing predicate denies with **403** (authenticated but
  forbidden) — as distinct from `require_authenticated`'s **401** — returning
  JSON, not an HTML login redirect. This is the seam a staff-gated mount uses
  (`authorize=lambda r: r.user.is_staff`).
- **`get_urls` forwards the auth seam to every sub-view it builds.**
  `require_authenticated` / `get_user` / `authorize` passed to `get_urls` now
  reach the skills, tools, threads, attachments, and transcribe endpoints, so a
  single call locks the whole mount down (the agent `view` carries its own auth,
  set when you construct `DjangoAGUIView`). Defaults stay open.
- **`DJANGO_AG_UI["ALLOW_ANONYMOUS"]` setting** (default `False`) governing how
  the model-backed stores treat anonymous requests — see below.

### Security

- **Model-backed stores no longer collapse every anonymous visitor into one
  shared owner bucket.** Previously an anonymous request resolved to owner id
  `None` → stored as `""`, so *all* anonymous visitors shared one bucket and
  could list / load / rename / delete / download each other's threads and
  attachments. The stores now resolve the owner via `resolve_owner_id`: an
  authenticated user's pk, or — only when `ALLOW_ANONYMOUS=True` — a per-browser
  `anon:<session_key>` bucket. With `ALLOW_ANONYMOUS` off (the default) an
  anonymous store operation raises `AnonymousOperationError`, which the
  persistence views turn into **403** and the agent endpoint's save path skips
  (the run still streams; it just isn't persisted). Authenticate the endpoints
  (`require_authenticated=True` / `get_user`) whenever a store persists.

### Fixed

- `get_urls`' docstring now documents the `threads/<id>/` **PATCH rename** route
  and the anonymous-scoping caveat.

## [0.9.0] — 2026-06-30

### Added

- **drf-services specs as tools, no MCP hop.** A new `SERVICE_SPECS`
  setting (dotted path to a `name -> ServiceSpec/SelectorSpec` mapping) exposes
  drf-services specs to the agent via `djangorestframework-pydantic-ai`'s
  `SpecToolset` — dispatched in-process through drf-services' transport-neutral
  surface, **without** standing up an MCP server. It drops into the same
  per-request `AgentConfig.toolsets` seam as the drf-mcp bridge: the agent acts
  as the logged-in AG-UI user, each spec's `permission_classes` are enforced, a
  registry `@tool` wins a name collision, and the spec tools' labels surface in
  the `data-tools-url` catalog. Requires the new `[spec-tools]` extra
  (`djangorestframework-pydantic-ai`), imported lazily.

## [0.8.0] — 2026-06-30

### Added

- **Voice input (server).** A new `TranscriptionBackend` Protocol (async,
  owner-scoped `transcribe`) with a `NullTranscriptionBackend` default (voice
  off → `410`), resolved from `DJANGO_AG_UI["TRANSCRIPTION_BACKEND"]` via
  `resolve_transcription_backend`. `get_urls(view, transcribe=backend)` mounts a
  `TranscribeView` at `<prefix>transcribe/` (POST a multipart `audio` clip →
  `{"text": "<transcript>"}`), server-validated by `TRANSCRIPTION_MAX_BYTES` /
  `TRANSCRIPTION_ALLOWED_TYPES` and carrying the same `require_authenticated` /
  `get_user` auth seam as the agent endpoint. The opt-in
  `django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend`
  is a ready reference impl over any OpenAI-compatible `/audio/transcriptions`
  endpoint (lazy `openai` import via the `[openai]` extra; subclass to change the
  model or `base_url`). New exports: `TranscriptionBackend`,
  `NullTranscriptionBackend`, `TranscribeView`, `resolve_transcription_backend`.
- **Model reasoning forwarding.** When a reasoning model is configured to think
  (via `MODEL_SETTINGS`), its chain-of-thought now streams to the client as the
  standard AG-UI reasoning events Pydantic-AI emits — a pure pass-through (no
  protocol extension), and the run transcript ignores the ephemeral events so
  nothing reasoning-related is persisted. The new `FORWARD_REASONING` setting
  (default `True`) gates it: set `False` to let the model reason privately while
  the events are stripped from the stream.

## [0.7.0] — 2026-06-25

### Added

- **File uploads (server side).** A new `AttachmentStore` Protocol
  (async, owner-scoped `save` / `open` / `delete`) is the persistence seam for
  files a user attaches to a conversation, set via
  `DJANGO_AG_UI["ATTACHMENT_STORE"]`. The package ships `NullAttachmentStore`
  (the default — uploads disabled, the endpoint answers `410`) and an abstract
  `ModelAttachmentStore` base (async wrapping + owner scoping over three sync
  ops). Uploads return a lightweight `AttachmentRef` (`id`/`name`/`mime`/`size`),
  never bytes — the AG-UI message stream stays vanilla.
- **`AttachmentsView`**, mounted by `get_urls(view, attachments=<store>)` at
  `<prefix>attachments/` (POST multipart upload → `201` ref) and
  `<prefix>attachments/<id>/` (GET download, DELETE). Uploads are validated
  **server-side** against `ATTACHMENT_MAX_BYTES` (oversize → `413`) and
  `ATTACHMENT_ALLOWED_TYPES` (disallowed → `415`); downloads stream as an
  `attachment` with `X-Content-Type-Options: nosniff`. Every operation is
  owner-scoped — another user's id reads as `404` — with the same
  `require_authenticated` / `get_user` auth seam as `DjangoAGUIView`.
- **`read_attachment` tool.** When an attachment store is configured, the view
  wires a per-request `read_attachment(attachment_id)` tool onto the agent,
  scoped to the acting user, so the model reads a file's contents server-side
  (text inlined; binary summarised) — the bytes never ride the wire. A consumer
  that registers its own `read_attachment` keeps it (registry tools win).
- **New settings:** `ATTACHMENT_STORE` (dotted path, default `None` = off),
  `ATTACHMENT_MAX_BYTES` (default 10 MiB, `0` disables), and
  `ATTACHMENT_ALLOWED_TYPES` (default `()` = any).
- **Reference durable file store (opt-in).** The `django_ag_ui.contrib.store`
  app now also ships a `StoredAttachment` model + migration and
  `DefaultAttachmentStore` (a `ModelAttachmentStore` subclass keeping bytes in
  Django `Storage` — S3/GCS via `STORAGES` — and metadata in a row, owner-scoped
  by an opaque `attachment_id`). The base package still ships no model.

## [0.6.0] — 2026-06-24

### Added

- **Thread index for the chat-history drawer (server side).**
  `ConversationStore` gains an async `list(*, request)` returning owner-scoped
  `ConversationMeta` (`thread_id`, `title`, `updated_at`, `preview`) — **metadata
  only**, no message bodies — so a thread list stays cheap. `NullConversationStore`
  lists nothing; `DjangoSessionConversationStore` enumerates the session's own
  threads (titles/previews derived from messages, `updated_at` stamped on save);
  `ModelConversationStore` adds a `_list(owner_id)` hook that **defaults to `[]`**
  (listing is opt-in, so existing subclasses keep working) and is overridden for
  a cheap column-backed listing.
- **`ThreadsView`**, mounted by `get_urls(view, threads=<store>)` at
  `<prefix>threads/` (GET — the user's threads) and `<prefix>threads/<id>/`
  (GET messages, **PATCH rename**, DELETE). Every operation is owner-scoped —
  another user's thread reads as `404` — and the view carries the same
  `require_authenticated` / `get_user` auth seam as `DjangoAGUIView`.
- **Thread rename.** `ConversationStore` gains `rename(thread_id, title, *, request)`.
  `DjangoSessionConversationStore` persists the title (it overrides the derived
  one in `list`); `ModelConversationStore` adds a `_rename(thread_id, title,
  owner_id)` hook that **defaults to a no-op** (override with a `title` column);
  `NullConversationStore` is a no-op. `PATCH <prefix>threads/<id>/` takes
  `{"title": "..."}` — a blank title is `400`, a missing/cross-owner thread `404`.
- **Reference durable store (opt-in).** A new `django_ag_ui.contrib.store` app
  ships a `StoredConversation` model + migration and `DefaultConversationStore`
  (a `ModelConversationStore` subclass implementing fetch/store/remove/list/rename
  with denormalised `title`/`preview`/`updated_at` columns for a cheap thread
  list). Enable it by adding `"django_ag_ui.contrib.store"` to `INSTALLED_APPS`,
  running `migrate`, and pointing `DJANGO_AG_UI["CONVERSATION_STORE"]` at it.
  The base package still ships no model, so projects that don't opt in get no
  migration.

## [0.5.0] — 2026-06-23

### Changed

- **The drf-mcp bridge now rides drf-mcp's public in-process surface** instead of
  importing handler internals. `DrfMcpToolset` lists tools via
  `MCPServer.list_tools` and executes them via `MCPServer.acall_tool` (drf-mcp's
  transport-complete in-process methods), passing the Django `request` +
  `request.user` so the call context is built inside drf-mcp. Behaviour is
  unchanged — the same merged `inputSchema`, serializer validation, permissions,
  and error mapping as before — but the bridge no longer reaches into
  `handle_tools_list` / `handle_tools_call_async` / `MCPCallContext`. **Requires
  `djangorestframework-mcp-server >= 0.9`** (the pin is now `>=0.9,<0.10`).

## [0.4.0] — 2026-06-12

### Added

- **Cancelling a run is now handled explicitly** (AG-UI has no server-side
  cancel route — the client aborts the streaming request and the server
  observes the disconnect). The view's event stream is wrapped in a
  teardown-aware guard that, on `CancelledError`/`GeneratorExit`:
    - **guarantees provider teardown** — the innermost event generator (whose
      context manager owns the model provider's streaming request) is closed
      explicitly rather than left to garbage-collection order, so no orphaned
      upstream generation keeps billing after the client stopped listening;
    - **persists the partial exchange** — with a non-null
      `CONVERSATION_STORE`, the truncated conversation (client history plus
      the assistant text and completed tool calls streamed so far, dropping
      partially streamed tool calls) is saved with the same thread/owner
      scoping as a completed run;
    - **audits the cancellation** — the configured `AuditLogger` receives a
      run-level `AuditEvent` with `tool_name="agent.run"`, `success=False`,
      and an `error` starting with `"cancelled:"`, riding the existing
      `record()` surface so custom loggers keep working unchanged;
    - **re-raises the cancellation** — persist/audit failures are logged,
      never substituted for the `CancelledError`.

  No new settings and no new endpoint: cancellation stays transport-level,
  and partial persistence follows the store you already configured.

## [0.3.1] — 2026-06-10

### Fixed

- **The `get_user` auth hook now accepts sync or async callables** and runs
  sync hooks off the event loop (`sync_to_async`, thread-sensitive), so the
  headline use case — a sync ORM token → user lookup — works without
  `SynchronousOnlyOperation`. Async hooks, previously called without being
  awaited (a coroutine landed on `request.user` and the auth gate silently
  failed), now work too. A sync hook that returns a coroutine (e.g. a
  `functools.partial` over an async fn) is awaited rather than leaked.
- **`require_authenticated` no longer crashes under ASGI with DB-backed
  sessions**. The lazy `request.user` is materialized in a worker
  thread before the gate, instead of being resolved on the event loop; the
  cached resolution also makes later loop-side readers (the drf-mcp
  bridge's `TokenInfo`, conversation ownership) safe.
- **Bridge errors no longer kill the chat**. The drf-mcp bridge
  previously raised `RuntimeError` for every `JsonRpcError`, which
  pydantic-ai treats as fatal — the most common failure (the model sending
  slightly wrong arguments) emitted `RUN_ERROR` and ended the run. Now:
  malformed-arguments (`-32602`) and service-raised validation raise
  `pydantic_ai.ModelRetry` carrying the field errors so the model
  self-corrects; business-rule failures and missing rows (drf-mcp 0.7's
  `isError` results) are returned as model-readable `{"error": {...}}`
  content; a hard `RuntimeError` is reserved for genuine protocol faults
  (unknown tool, auth, rate limits).
- **Tool-name collisions no longer break the agent**. The catalog
  deduped registry-vs-drf-mcp collisions ("registry wins") but the agent
  registered both, so pydantic-ai raised `UserError` at the first run. The
  drf-mcp toolset now receives the registry's names and skips collisions —
  catalog and agent agree on one rule.
- **The bridge no longer pins a hardcoded MCP protocol version**.
  Synthesised in-process calls advertise drf-mcp's own first supported
  version (`REST_FRAMEWORK_MCP["PROTOCOL_VERSIONS"][0]`), so the bridge
  tracks the server across upgrades.

### Added

- **Auth seam on the catalog views**. `ToolsView` and `SkillsView`
  accept the same `require_authenticated` / `get_user` (sync or async) pair
  as `DjangoAGUIView`, so one policy covers the agent endpoint and the
  catalogs it advertises — previously both answered any anonymous GET, even
  with a locked-down agent endpoint. Defaults stay open for backwards
  compatibility; lock the catalogs down whenever the endpoint is.
- Shared authorize helpers in `django_ag_ui.utils` (`aauthorize` /
  `authorize` / `acall_get_user` / `call_get_user`) — the single policy
  implementation behind all three views.

### Changed

- **Dependency ranges tightened**: `pydantic-ai-slim[ag-ui]` is now
  capped `>=1.0,<2` (the bridge touches semi-internal pydantic-ai surface —
  `ExternalToolset.tool_defs`, tool-def re-stamping), and the `drf-mcp`
  extra now requires `djangorestframework-mcp-server>=0.7,<0.8` — the range
  actually tested, and the floor that returns business failures as
  `isError` results (which the bridge's error mapping consumes).
- **CSRF guidance made prominent**: the view keeps `csrf_exempt=True` by
  default (right for header-token auth), but cookie-authenticated
  deployments should pass `csrf_exempt=False` — tools act as
  `request.user`, so an unprotected cookie-auth endpoint lets a third-party
  page drive the agent as the logged-in user. Documented in the quickstart
  and the view docstring.

## [0.3.0] — 2026-06-03

### Added
- **Tool-metadata catalog.** A read-only `ToolsView` (GET, JSON) returns the
  agent's server-tool catalog; `get_urls(view, tools=registry)` mounts it at
  `<prefix>tools/` (named `django_ag_ui_tools`), passing the same `ToolRegistry`
  the view uses. `build_tool_catalog(registry)` builds the list — each entry is
  `{"name", "summary", "description"?}`. `summary` is always present, resolved
  from a fallback chain: registry `@tool(summary=…)` → a prettified tool name
  (`query_model` → "Query model"); for drf-mcp tools `display_name` → `title` →
  prettified name. `description` is included when available (`ToolSpec.description`,
  or drf-mcp `display_description` → `description`); registry tools win on name
  collisions. **Purpose:** server-side tools execute server-side, so their JSON
  Schema never reaches the browser — the catalog is the channel the web component
  fetches via its `data-tools-url` attribute to label tool-call cards.

### Changed
- The `[drf-mcp]` extra now requires `djangorestframework-mcp-server>=0.6.1`
  (which pulls `djangorestframework-services>=0.15.0`). Additive, no code change:
  it lets the tool catalog read drf-mcp tools' `display_name` / `display_description`
  binding metadata (consumer-only, never on the MCP wire) as the label source.

## [0.2.2] — 2026-06-02

### Fixed
- **drf-mcp tools are now actually executed in-process.** `DrfMcpToolset`
  extended `ExternalToolset`, whose tools are `kind="external"` — Pydantic-AI
  *defers* those: it yields the call to the client and ends the run, never
  invoking the toolset's `call_tool`. So drf-mcp tool calls were handed off and
  silently dropped (no `TOOL_CALL_RESULT`, the model never continued, and an
  AG-UI client's pending indicator would hang). The toolset now advertises its
  tools as `kind="function"`, so the run loop runs them via the per-user
  `MCPCallContext` and streams a real `TOOL_CALL_RESULT`. Regression test drives
  a full agent run, not just a direct `call_tool`.

## [0.2.1] — 2026-06-02

### Fixed
- `API_KEY`-based model construction now works for **every provider Pydantic-AI
  knows** (`openai-responses`, `groq`, `bedrock`, …), not just a hand-maintained
  short list. `build_model` delegates the `provider:name` → Model-class mapping
  to Pydantic-AI's own `infer_model`, injecting the key via a `provider_factory`,
  so `MODEL = "openai-responses:…"` with an `API_KEY` no longer raises. A bare
  model name Pydantic-AI can map to a provider (e.g. `claude-…`) is accepted too.
- The **drf-mcp toolset** now sources each tool's schema from drf-mcp's own
  `tools/list` instead of re-deriving it from the input serializer alone. So the
  agent sees the full advertised `inputSchema` — a selector tool's
  filter / ordering / pagination arguments and the `additionalProperties` policy,
  not just the serializer's fields — matching the HTTP transport exactly.

### Changed
- `DEFAULT_SYSTEM_PROMPT` gained gentle steering for two common failure modes:
  use a listing/search tool's arguments to find things by name and then act on
  the result (don't stop at the lookup), treat "open / go to / show me" as
  navigation, and always finish a turn with a reply or completed action.

## [0.2.0] — 2026-06-02

### Added
- `@tool(confirm="…")` / `ToolSpec.confirm` — an optional human-readable
  confirmation prompt for a destructive tool, stamped into the JSON Schema as
  the `x-confirm` extension (`X_CONFIRM_KEY`) for the frontend to display.
- `DJANGO_AG_UI["API_KEY"]` and `["PROVIDER"]` — supply the model's API key (or
  a full `Provider` instance / dotted path) explicitly instead of inferring it
  from the environment, while keeping the built-in toolset wiring. `MODEL` may
  also be a pre-built `Model` instance.
- Provider extras `django-ag-ui[anthropic]`, `[openai]`, `[google]`.
- **Skills** — `SkillRegistry` + `SkillSpec` (pre-defined prompts) and a
  read-only catalog endpoint mounted by `get_urls(view, skills=registry)` at
  `<prefix>skills/`, serving the JSON the web component consumes.
- `DjangoAGUIView(require_authenticated=True)` fails closed (401) for
  unauthenticated requests, and a `get_user(request)` hook establishes the user
  (assigned to `request.user`) before tools run — closing the "tools run as
  AnonymousUser" footgun. The contract is documented on the view.
- `@tool(summary="…")` / `ToolSpec.summary` → `x-summary` (`X_SUMMARY_KEY`): a
  short display label the frontend shows on the tool-call card.

### Changed
- `DEFAULT_SYSTEM_PROMPT` now steers the model to call destructive tools
  directly and rely on the client's explicit confirmation step, instead of
  asking the user to confirm in prose.
- **Dependency: `pydantic-ai[ag-ui]` → `pydantic-ai-slim[ag-ui]`.** Drops the
  full meta-package's logfire / fastmcp / temporalio / otel footprint. **Action
  required:** install a provider — `pip install django-ag-ui[anthropic]` (or
  `[openai]` / `[google]`, or the provider lib directly) — to use a
  `"provider:model"` `MODEL` string.

### Notes
- The AG-UI endpoint now emits a one-time `RuntimeWarning` when served over WSGI
  (SSE can't stream there); deploy under ASGI (Daphne / Uvicorn).

## [0.1.1] — 2026-06-01

### Fixed
- `build_input_schema` derives parameter types from raw annotations
  (`inspect.signature(eval_str=True)`) instead of `typing.get_type_hints`, so
  the JSON Schema is identical across Python versions — Python ≤ 3.10 no longer
  adds a spurious `nullable: true` to `None`-defaulted parameters.

### Changed
- Expanded README (full badge set + quickstart); the release now publishes a
  coverage badge to `gh-pages` for the README's coverage shield.

## [0.1.0] — 2026-06-01

### Added
- `ToolRegistry` + the `@tool` decorator (`destructive=` / `category=`), with
  JSON-Schema derived from signatures and `x-destructive` / `x-category`
  extensions.
- `DjangoAGUIView`, an async endpoint over Pydantic-AI's `AGUIAdapter` (SSE),
  plus `get_urls()` for mounting.
- `AgentConfig` + `build_agent`, and the `DJANGO_AG_UI` settings (`MODEL`,
  `MODEL_SETTINGS`, `RETRIES`, `AGENT_FACTORY`, `TOOLSETS`, `CAPABILITIES`,
  `AUTO_CONFIRM`, `SYSTEM_PROMPT`).
- `AuditLogger` protocol with `NullAuditLogger` / `LoggingAuditLogger`.
- Opt-in server-side conversation persistence: the `ConversationStore` protocol,
  `NullConversationStore` (default, stateless), `DjangoSessionConversationStore`,
  and the abstract `ModelConversationStore` base.
- In-process `drf-mcp` toolset bridge behind the `[drf-mcp]` extra.

[Unreleased]: https://github.com/Artui/django-ag-ui/compare/v0.21.0...HEAD
[0.21.0]: https://github.com/Artui/django-ag-ui/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/Artui/django-ag-ui/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/Artui/django-ag-ui/compare/v0.18.1...v0.19.0
[0.18.1]: https://github.com/Artui/django-ag-ui/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/Artui/django-ag-ui/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/Artui/django-ag-ui/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/Artui/django-ag-ui/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/Artui/django-ag-ui/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Artui/django-ag-ui/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Artui/django-ag-ui/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/Artui/django-ag-ui/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/Artui/django-ag-ui/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/Artui/django-ag-ui/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/Artui/django-ag-ui/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Artui/django-ag-ui/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Artui/django-ag-ui/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Artui/django-ag-ui/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Artui/django-ag-ui/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Artui/django-ag-ui/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Artui/django-ag-ui/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artui/django-ag-ui/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Artui/django-ag-ui/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Artui/django-ag-ui/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Artui/django-ag-ui/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Artui/django-ag-ui/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Artui/django-ag-ui/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Artui/django-ag-ui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Artui/django-ag-ui/releases/tag/v0.1.0
