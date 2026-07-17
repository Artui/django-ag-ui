# Configuration

Configuration comes in two halves, split on a simple line: **objects are passed,
values are settings.**

**Collaborators are constructor arguments.** Toolsets, capabilities, stores, an
agent factory, a drf-mcp server — you build them and hand them to `AGUIServer`:

```python
# urls.py
agent = AGUIServer(
    registry,
    toolsets=[weather_toolset],
    conversation_store=DjangoSessionConversationStore(),
    drf_mcp_server=internal_mcp,
)
urlpatterns = [path("agent/", agent.urls)]
```

**Scalars live in a `DJANGO_AG_UI` dict**, read **once, when the server is
built** — never per request. Every key is optional.

```python
DJANGO_AG_UI = {"MODEL": "anthropic:claude-sonnet-4.6", "RETRIES": 2}
```

Override a scalar for one endpoint with
[`build_ag_ui_config`][django_ag_ui.build_ag_ui_config], which layers your
values over the settings:

```python
AGUIServer(registry, config=build_ag_ui_config(retries=5))
```

!!! note "Why the split"
    Read on every request, these could only ever be **global** — so an
    `/internal/agent` and a `/public/agent` were forced to share one tool-guard
    policy, one retry budget, one toolset list. And a dotted path to a
    collaborator only ever existed because `settings.py` cannot hold a live
    object; `urls.py` can. That is also why `drf_mcp_server=internal_mcp` is
    expressible at all — with one global path there was no way to say *which*
    agent bridges to *which* MCP server. See
    [Multiple endpoints](#multiple-endpoints).

| Key | Type | Default | Purpose |
| --- | --- | --- | --- |
| `MODEL` | `str` | `None` | Pydantic-AI model string (or pre-built `Model`). |
| `API_KEY` | `str` | `None` | Explicit provider key (builds the model via `build_model`). |
| `SYSTEM_PROMPT` | `str` | `None` | Override the agent's instructions. |
| `MODEL_SETTINGS` | `dict` | `None` | Pydantic-AI `ModelSettings`. |
| `RETRIES` | `int` | `None` | Tool/output retry budget. |
| `THREAD_LIST_LIMIT` | `int` | `200` | Max threads the index endpoint returns per call; `?limit=N` requests fewer, a larger value is clamped down. |
| `ATTACHMENT_MAX_BYTES` | `int` | `10485760` | Max accepted upload size in bytes (`0` disables the cap). |
| `ATTACHMENT_ALLOWED_TYPES` | `tuple[str, ...]` | `()` | Allowed upload content types (empty = any). |
| `MANAGE_SYSTEM_PROMPT` | `str` | `"server"` | Who owns the system prompt on the wire. |
| `ALLOW_UPLOADED_FILES` | `bool` | `False` | Honour `UploadedFile` refs in client messages. |
| `FORWARD_REASONING` | `bool` | `True` | Forward a reasoning model's thoughts to the client (`False` strips them). |
| `TRANSCRIPTION_MAX_BYTES` | `int` | `26214400` | Max accepted audio-clip size in bytes (`0` disables the cap). |
| `TRANSCRIPTION_ALLOWED_TYPES` | `tuple[str, ...]` | `()` | Allowed audio content types (empty = any). |
| `ALLOW_ANONYMOUS` | `bool` | `False` | Default for `ModelConversationStore(allow_anonymous=)` / `ModelAttachmentStore(allow_anonymous=)` — a **store** policy (see [Authentication & anonymous scoping](#authentication-anonymous-scoping)). |
| `TOOL_GUARD` | `dict` | `{}` | Server-side destructive-tool approval gate (off by default). See [`TOOL_GUARD`](#tool_guard). |

Every one of these is also a `build_ag_ui_config(...)` keyword.

### Collaborators (constructor arguments)

| Argument | Purpose |
| --- | --- |
| `toolsets=[...]` | Extra Pydantic-AI toolsets. |
| `capabilities=[...]` | Pydantic-AI capabilities. |
| `agent_factory=fn` | Escape hatch replacing `build_agent`. |
| `audit_logger=...` | `AuditLogger` implementation. |
| `provider=...` | Explicit Pydantic-AI `Provider`; takes precedence over `API_KEY`. |
| `conversation_store=...` | Server-side conversation persistence. |
| `attachment_store=...` | Server-side file-upload storage (uploads off when unset). |
| `transcription_backend=...` | Speech-to-text backend for voice input (voice off when unset). |
| `drf_mcp_server=...` | drf-mcp server whose tools the agent gets. |
| `service_specs={...}` | drf-services specs mapping exposed as tools, no MCP hop (`[spec-tools]` extra). |

## Multiple endpoints

Two AG-UI endpoints in one project, each with its own agent, tools and policy:

```python
# urls.py
internal = AGUIServer(
    internal_registry,
    namespace="internal-agent",
    drf_mcp_server=internal_mcp,
    conversation_store=ScopedConversationStore(store, scope="internal"),
    config=build_ag_ui_config(tool_guard=ToolGuardConfig(enabled=True)),
)
public = AGUIServer(
    public_registry,
    namespace="public-agent",
    conversation_store=ScopedConversationStore(store, scope="public"),
)

urlpatterns = [
    path("internal/agent/", internal.urls),
    path("public/agent/", public.urls),
]
```

[`ScopedConversationStore`][django_ag_ui.ScopedConversationStore] is what keeps
their thread histories apart: stores key by `(owner_id, thread_id)`, so without
it a conversation started at `/internal/agent` shows up in `/public/agent`'s
drawer — and resumes there under the *public* agent's model and tools. It is
opt-in on purpose: wrapping automatically would silently orphan an existing
project's history.

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

## `provider=`

A Pydantic-AI `Provider` instance, passed straight to the constructed model. Use it when you need a custom
`base_url` or HTTP client (a proxy, a gateway, a self-hosted endpoint). It
**takes precedence over [`API_KEY`](#api_key)**. As with `API_KEY`, the matching
provider extra must be installed.

```python
DJANGO_AG_UI = {"MODEL": "openai:gpt-4o"}

# urls.py
AGUIServer(registry, provider=OpenAIProvider(base_url="https://gateway.example"))
```

## `audit_logger=`

An [`AuditLogger`][django_ag_ui.AuditLogger] instance. Omitted (the default)
means [`NullAuditLogger`][django_ag_ui.NullAuditLogger] — no auditing. Pass
[`LoggingAuditLogger`][django_ag_ui.LoggingAuditLogger] or your own:

```python
AGUIServer(registry, audit_logger=LoggingAuditLogger())
```

Because you construct it, a logger that needs constructor arguments just works —
the old dotted-path form required one that was importable with no arguments at
all.

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

## `agent_factory=`

The escape hatch. A callable matching
[`AgentFactoryFn`][django_ag_ui.AgentFactoryFn] —
`(registry: ToolRegistry, config: AGUIConfig) -> Agent` — that **fully
replaces** the built-in [`build_agent`][django_ag_ui.build_agent]. Use it for
custom model providers, output types, instrumentation, or toolset wiring the
sugar arguments do not cover. When passed, the view hands construction entirely
to your factory and does **not** apply `MODEL_SETTINGS`, `RETRIES`, `toolsets`,
`capabilities`, or the drf-mcp bridge itself — your factory owns all of it.

```python
# myproject/agent.py
from pydantic_ai import Agent


def build_my_agent(registry, config):
    return Agent(model="anthropic:claude-sonnet-4.6", ...)


# urls.py
AGUIServer(registry, agent_factory=build_my_agent)
```

## `toolsets=`

Extra Pydantic-AI toolsets composed alongside the registry tools — e.g. an
MCP-client toolset. Empty by default. (Ignored when `agent_factory=` is passed.)

```python
AGUIServer(registry, toolsets=[weather_toolset])
```

Two endpoints can now hold different toolsets — the point of the change. With a
single global `TOOLSETS` setting, a public agent necessarily carried the internal
one's tools.

## `capabilities=`

Pydantic-AI capabilities passed to the `Agent`. Empty by default. (Ignored when
`agent_factory=` is passed.)

## `MANAGE_SYSTEM_PROMPT`

Who owns the system prompt on the AG-UI wire: `"server"` (the default — the
agent's configured prompt is authoritative and a client-posted system message
is stripped before it reaches the model) or `"client"` (the client-supplied
system message is honoured). `instructions` are always injected server-side
regardless. Client-submitted history is additionally passed through
Pydantic-AI's `sanitize_messages` hardening on every run.

## `ALLOW_UPLOADED_FILES`

Whether `UploadedFile` references in client-submitted messages are honoured.
`False` (the default) drops them with a warning before the messages reach the
model — an `UploadedFile` is fetched by the model provider using the server's
credentials, so it should only be accepted from trusted clients. The
[file-upload flow](concepts.md#file-uploads) is unaffected either way: it
travels server-issued refs in message text, not AG-UI file parts.

## `conversation_store=`

A [`ConversationStore`][django_ag_ui.ConversationStore] instance. Omitted (the
default) keeps the server stateless using
[`NullConversationStore`][django_ag_ui.NullConversationStore] — the conversation
lives entirely in the client's posted history.

The package ships
[`DjangoSessionConversationStore`][django_ag_ui.DjangoSessionConversationStore]
(session-backed, no migration) and an abstract
[`ModelConversationStore`][django_ag_ui.ModelConversationStore] base you can
subclass with your own model. See
[Conversation persistence](concepts.md#conversation-persistence).

```python
AGUIServer(registry, conversation_store=DjangoSessionConversationStore())
```

For a ready-made **durable, cross-device** store, opt into the
`django_ag_ui.contrib.store` app instead of writing your own model — add it to
`INSTALLED_APPS`, run `migrate`, and point the setting at its store:

```python
INSTALLED_APPS = [
    # ...
    "django_ag_ui.contrib.store",
]

```

```python title="urls.py"
from django_ag_ui.contrib.store.default_conversation_store import (
    DefaultConversationStore,
)

AGUIServer(registry, conversation_store=DefaultConversationStore())
```

The base package ships no model, so projects that don't opt in get no migration.
When an active (non-`Null`) store is passed, `AGUIServer` mounts the
**thread-history drawer** endpoints automatically — see
[Thread history](concepts.md#thread-history).

## `attachment_store=`

A dotted path to an [`AttachmentStore`][django_ag_ui.AttachmentStore] class,
An [`AttachmentStore`][django_ag_ui.AttachmentStore] instance. Omitted (the
default) keeps **uploads disabled** using
[`NullAttachmentStore`][django_ag_ui.NullAttachmentStore] — the upload endpoint
answers `410 Gone`.

When a store is set, the view wires a per-request `read_attachment(attachment_id)`
tool onto the agent, scoped to the acting user, so the model can read the bytes a
user attached — the AG-UI message stream stays free of file payloads (uploads go
out-of-band and travel as lightweight refs). A consumer that registers its own
`read_attachment` tool keeps it (registry tools win).

The package ships an abstract
[`ModelAttachmentStore`][django_ag_ui.ModelAttachmentStore] base you can subclass
with your own model + storage. For a ready-made store, opt into the
`django_ag_ui.contrib.store` app — add it to `INSTALLED_APPS`, run `migrate`, and
point the setting at its store (bytes go to Django `Storage`, so S3/GCS come free
via `STORAGES` / `DEFAULT_FILE_STORAGE`):

```python
INSTALLED_APPS = [
    # ...
    "django_ag_ui.contrib.store",
]

```

```python title="urls.py"
from django_ag_ui.contrib.store.default_attachment_store import (
    DefaultAttachmentStore,
)

AGUIServer(registry, attachment_store=DefaultAttachmentStore())
```

When an active (non-`Null`) store is passed, `AGUIServer` mounts the upload
endpoints over HTTP automatically — see
[File uploads](concepts.md#file-uploads).

## `ATTACHMENT_MAX_BYTES`

The maximum accepted upload size in bytes, enforced **server-side** by
[`AttachmentsView`][django_ag_ui.AttachmentsView] (an oversize upload → `413`).
Defaults to `10485760` (10 MiB); set `0` to disable the cap. Client-side checks
in the web component are a UX nicety — this is the authoritative limit.

## `ATTACHMENT_ALLOWED_TYPES`

A tuple of allowed (client-declared) content types for uploads, e.g.
`("image/png", "image/jpeg", "application/pdf", "text/plain")`. Empty (the
default) accepts any type; otherwise an upload whose `Content-Type` is not listed
is rejected with `415`. The content type is client-declared, so treat this as a
coarse filter — the store decides what to do with the bytes.

## `FORWARD_REASONING`

Whether a reasoning model's chain-of-thought is forwarded to the client. When
`True` (the default), the AG-UI reasoning events Pydantic-AI emits for a
`ThinkingPart` pass straight through to the browser (where the web component can
render a collapsible "thinking" region). Set `False` to let the model reason
privately — the events are stripped from the stream before encoding, so the
chain-of-thought never leaves the server.

Reasoning is only emitted when the model is actually configured to think, which
is a `MODEL_SETTINGS` concern, not a separate switch. For an Anthropic model:

```python
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",
    "MODEL_SETTINGS": {"anthropic_thinking": {"type": "enabled", "budget_tokens": 2048}},
    # "FORWARD_REASONING": False,  # think privately; don't stream the thoughts
}
```

It is a pure pass-through (no protocol extension): the events ride the standard
AG-UI reasoning event family, and the server-side transcript ignores them (they
are ephemeral and never persisted).

## `transcription_backend=`

A dotted path to a [`TranscriptionBackend`][django_ag_ui.TranscriptionBackend]
A [`TranscriptionBackend`][django_ag_ui.TranscriptionBackend] instance. Omitted
(the default) keeps **voice input disabled** using
[`NullTranscriptionBackend`][django_ag_ui.NullTranscriptionBackend] — the
transcribe endpoint answers `410 Gone`.

The package ships a ready reference backend over any OpenAI-compatible
`/audio/transcriptions` endpoint —
`django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend`
— self-configuring from the `OPENAI_API_KEY` environment variable (requires the
`[openai]` extra). Subclass it to change the model or point at another
OpenAI-compatible server (Azure OpenAI, Groq, a local Whisper server):

```python
from django_ag_ui.contrib.transcription.openai_transcription_backend import (
    OpenAITranscriptionBackend,
)

AGUIServer(registry, transcription_backend=OpenAITranscriptionBackend())
```

When this setting resolves to an active (non-`Null`) backend, `AGUIServer` mounts
the voice endpoint automatically: `POST <prefix>transcribe/` accepts a multipart
`audio` clip and returns `{"text": "<transcript>"}` for the web component's
`data-transcribe-url`.

## `TRANSCRIPTION_MAX_BYTES`

The maximum accepted audio-clip size in bytes, enforced **server-side** by
[`TranscribeView`][django_ag_ui.TranscribeView] (an oversize clip → `413`).
Defaults to `26214400` (25 MiB, the OpenAI transcription limit); set `0` to
disable the cap.

## `TRANSCRIPTION_ALLOWED_TYPES`

A tuple of allowed (client-declared) content types for voice clips, e.g.
`("audio/webm", "audio/mp4", "audio/mpeg")`. Empty (the default) accepts any
type; otherwise a clip whose `Content-Type` is not listed is rejected with `415`.

## `drf_mcp_server=`

A dotted path to a `djangorestframework-mcp-server` `MCPServer` instance whose
tools are exposed to the agent in-process (requires the `[drf-mcp]` extra).
`None` (the default) disables the bridge. When set, the view builds a per-request
[`DRFMCPToolset`](concepts.md#the-drf-mcp-toolset-bridge) carrying the current
`request`, so the agent acts as the logged-in user and drf-mcp's own validation
and permission checks apply. See
[Installation → the `[drf-mcp]` extra](installation.md#the-drf-mcp-extra).

The drf-mcp tools also appear in the
[tool metadata catalog](concepts.md#tool-metadata-catalog) (mounted automatically
by `AGUIServer`), which reads each tool's `display_name` / `display_description`
as the web component's card label.

```python
from myproject.mcp import server as mcp_server

AGUIServer(registry, drf_mcp_server=mcp_server)
```
</content>

## `service_specs=`

A dotted path to a `name -> spec` mapping (drf-services `ServiceSpec` /
`SelectorSpec` objects) exposed to the agent as tools **without an MCP server**,
via `djangorestframework-pydantic-ai`'s `SpecCapability`. `None` (the default)
disables it. Requires the `[spec-tools]` extra
(`pip install "django-ag-ui[spec-tools]"`), imported lazily.

This is the no-MCP-hop sibling of [`DRF_MCP_SERVER`](#drf_mcp_server): the specs
are dispatched in-process through drf-services' transport-neutral surface
(`dispatch_spec` + its off-HTTP helpers), enforcing each spec's
`permission_classes`. The agent acts as the **logged-in AG-UI user** (bound from
`request`), and a registry `@tool` wins a name collision. Beyond exposing the
tools, `SpecCapability` teaches the model the spec conventions — that list tools
accept `page` / `limit` / `order`, and how errors come back (an `{"error": …}`
result is a final answer, a retry message means fix the argument, a permission
error is final) — via instructions appended to the system prompt, so the model
doesn't rediscover them by failing a call. Use it when you have drf-services
specs but no reason to stand up an MCP server; use `DRF_MCP_SERVER` when you
already run one (or want MCP clients to share the tools).

```python
# myproject/specs.py
from rest_framework_services import SelectorSpec, ServiceSpec
SPECS = {
    "list_orders": SelectorSpec(serializer=OrderSerializer, queryset=Order.objects.all()),
    "create_order": ServiceSpec(service=create_order, input_serializer=CreateOrderInput),
}
```

```python title="urls.py"
from myproject.specs import SPECS

AGUIServer(registry, service_specs=SPECS)
```

The spec tools' card labels are surfaced to the web component through the same
`AGUIServer`-mounted tool catalog (`data-tools-url`).

## Authentication & anonymous scoping

The agent endpoint and every mounted sub-view (tools, skills, threads,
attachments, transcribe) share **one authentication seam**, and it defaults
**open** — an unauthenticated visitor can drive the agent and reach the stores.
Lock a mount down by passing the seam to `AGUIServer`; it forwards to every view
it builds, including the agent endpoint:

```python
from django.urls import path

from django_ag_ui import AGUIServer

agent = AGUIServer(
    registry,
    require_authenticated=True,   # 401 for anonymous requests
    # authorize=lambda r: r.user.is_staff,  # 403 for a non-staff user
    # get_user=lambda r: token_user(r),     # establish the acting user
)
urlpatterns = [
    path("agent/", agent.urls),
]
```

- **`require_authenticated=True`** → an anonymous request gets **401** (JSON).
- **`authorize=<predicate>`** runs after the user is established; a falsy return
  gives **403** (JSON, never an HTML login redirect). Use it for a staff gate.
- **`get_user=<hook>`** establishes `request.user` (sync or async — a sync ORM
  token lookup runs off the event loop).

### `ALLOW_ANONYMOUS`

Governs how the **model-backed stores** (`ModelConversationStore` /
`ModelAttachmentStore` and the `contrib.store` reference implementations) treat
anonymous requests. It exists because owner scoping alone can't isolate
anonymous visitors from one another — they have no user id.

- **`False` (default)** — anonymous store operations are **refused**
  (`AnonymousOperationError`, surfaced as **403** by the persistence views; the
  agent endpoint's save path skips persistence so the run still streams). This
  prevents every anonymous visitor from sharing one owner bucket and reading or
  deleting each other's threads and attachments.
- **`True`** — anonymous requests are bucketed per browser by
  `request.session.session_key` (`anon:<key>`; requires session middleware).

Whenever a store persists, prefer authenticating the endpoints
(`require_authenticated=True` / `get_user`) over relying on `ALLOW_ANONYMOUS`.

### `TOOL_GUARD`

An **opt-in server-side approval gate** for destructive tools. By default a
server-side tool (a `@tool` registry tool or a drf-mcp-bridged tool) runs
mid-stream with no confirmation — the `destructive` flag reaches only the model
as a schema hint, not a gate. `TOOL_GUARD` changes that: when enabled, a
`ToolGuard` capability flips destructive tools to require approval, so the run
**defers** and finishes on a `RUN_FINISHED` *interrupt* the client approves or
denies via the AG-UI tool-approval loop (`RunAgentInput.resume[]`) — the same
mechanism the web component already applies to client-registered destructive
tools, now for server-side ones. The wire stays vanilla AG-UI. For the full
end-to-end flow (what the user sees, custom clients, `ask_user`), see
[Tool approval](tool-approval.md).

```python
DJANGO_AG_UI = {
    # …
    "TOOL_GUARD": {
        "ENABLED": True,
        "EXEMPT": ["refresh_cache"],       # never gate these, even if destructive
        "REQUIRE_APPROVAL": ["export_pii"], # always gate these, even if not destructive
    },
}
```

| Key | Type | Default | Meaning |
| --- | --- | --- | --- |
| `ENABLED` | `bool` | `False` | Compose the `ToolGuard` capability. |
| `EXEMPT` | `list[str]` | `[]` | Tool names never gated (wins over `REQUIRE_APPROVAL`). |
| `REQUIRE_APPROVAL` | `list[str]` | `[]` | Tool names always gated, even if not flagged destructive. |

**What counts as destructive:** a registry `@tool(destructive=True)`, or a
drf-mcp tool whose MCP `readOnlyHint` annotation is `False` (selectors are
read-only, services mutate; a project can override per registration). A tool is
gated when it is destructive **or** named in `REQUIRE_APPROVAL`, **unless** it is
named in `EXEMPT`.

The gate is only useful with a client that renders the interrupt and resumes —
the web component's approval card is the front-end half of this feature; a bespoke
AG-UI client handles the interrupt itself.
