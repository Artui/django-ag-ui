# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- **drf-services specs as tools, no MCP hop (PAI-2).** A new `SERVICE_SPECS`
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
  sessions** (ASYNC-1). The lazy `request.user` is materialized in a worker
  thread before the gate, instead of being resolved on the event loop; the
  cached resolution also makes later loop-side readers (the drf-mcp
  bridge's `TokenInfo`, conversation ownership) safe.
- **Bridge errors no longer kill the chat** (ERR-2). The drf-mcp bridge
  previously raised `RuntimeError` for every `JsonRpcError`, which
  pydantic-ai treats as fatal — the most common failure (the model sending
  slightly wrong arguments) emitted `RUN_ERROR` and ended the run. Now:
  malformed-arguments (`-32602`) and service-raised validation raise
  `pydantic_ai.ModelRetry` carrying the field errors so the model
  self-corrects; business-rule failures and missing rows (drf-mcp 0.7's
  `isError` results) are returned as model-readable `{"error": {...}}`
  content; a hard `RuntimeError` is reserved for genuine protocol faults
  (unknown tool, auth, rate limits).
- **Tool-name collisions no longer break the agent** (DUP-1). The catalog
  deduped registry-vs-drf-mcp collisions ("registry wins") but the agent
  registered both, so pydantic-ai raised `UserError` at the first run. The
  drf-mcp toolset now receives the registry's names and skips collisions —
  catalog and agent agree on one rule.
- **The bridge no longer pins a hardcoded MCP protocol version** (PROTO-1).
  Synthesised in-process calls advertise drf-mcp's own first supported
  version (`REST_FRAMEWORK_MCP["PROTOCOL_VERSIONS"][0]`), so the bridge
  tracks the server across upgrades.

### Added

- **Auth seam on the catalog views** (SEC-6). `ToolsView` and `SkillsView`
  accept the same `require_authenticated` / `get_user` (sync or async) pair
  as `DjangoAGUIView`, so one policy covers the agent endpoint and the
  catalogs it advertises — previously both answered any anonymous GET, even
  with a locked-down agent endpoint. Defaults stay open for backwards
  compatibility; lock the catalogs down whenever the endpoint is.
- Shared authorize helpers in `django_ag_ui.utils` (`aauthorize` /
  `authorize` / `acall_get_user` / `call_get_user`) — the single policy
  implementation behind all three views.

### Changed

- **Dependency ranges tightened** (VER-2): `pydantic-ai-slim[ag-ui]` is now
  capped `>=1.0,<2` (the bridge touches semi-internal pydantic-ai surface —
  `ExternalToolset.tool_defs`, tool-def re-stamping), and the `drf-mcp`
  extra now requires `djangorestframework-mcp-server>=0.7,<0.8` — the range
  actually tested, and the floor that returns business failures as
  `isError` results (which ERR-2's mapping consumes).
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

[Unreleased]: https://github.com/Artui/django-ag-ui/compare/v0.10.0...HEAD
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
