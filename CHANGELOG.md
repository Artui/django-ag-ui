# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Artui/django-ag-ui/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Artui/django-ag-ui/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Artui/django-ag-ui/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Artui/django-ag-ui/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Artui/django-ag-ui/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Artui/django-ag-ui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Artui/django-ag-ui/releases/tag/v0.1.0
