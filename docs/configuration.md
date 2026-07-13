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
| `AUDIT_LOGGER` | dotted `str` | `None` | `AuditLogger` implementation. |
| `SYSTEM_PROMPT` | `str` | `None` | Override the agent's instructions. |
| `MODEL_SETTINGS` | `dict` | `None` | Pydantic-AI `ModelSettings`. |
| `RETRIES` | `int` | `None` | Tool/output retry budget. |
| `AGENT_FACTORY` | dotted `str` | `None` | Escape hatch replacing `build_agent`. |
| `TOOLSETS` | `tuple[str, ...]` | `()` | Extra Pydantic-AI toolsets. |
| `CAPABILITIES` | `tuple[str, ...]` | `()` | Pydantic-AI capabilities. |
| `CONVERSATION_STORE` | dotted `str` | `None` | Server-side conversation persistence. |
| `THREAD_LIST_LIMIT` | `int` | `200` | Max threads the index endpoint returns per call; `?limit=N` requests fewer, a larger value is clamped down. |
| `ATTACHMENT_STORE` | dotted `str` | `None` | Server-side file-upload storage (uploads off when unset). |
| `ATTACHMENT_MAX_BYTES` | `int` | `10485760` | Max accepted upload size in bytes (`0` disables the cap). |
| `ATTACHMENT_ALLOWED_TYPES` | `tuple[str, ...]` | `()` | Allowed upload content types (empty = any). |
| `FORWARD_REASONING` | `bool` | `True` | Forward a reasoning model's thoughts to the client (`False` strips them). |
| `TRANSCRIPTION_BACKEND` | dotted `str` | `None` | Speech-to-text backend for voice input (voice off when unset). |
| `TRANSCRIPTION_MAX_BYTES` | `int` | `26214400` | Max accepted audio-clip size in bytes (`0` disables the cap). |
| `TRANSCRIPTION_ALLOWED_TYPES` | `tuple[str, ...]` | `()` | Allowed audio content types (empty = any). |
| `DRF_MCP_SERVER` | dotted `str` | `None` | drf-mcp server whose tools the agent gets. |
| `SERVICE_SPECS` | dotted `str` | `None` | drf-services specs mapping exposed as tools, no MCP hop (`[spec-tools]` extra). |
| `ALLOW_ANONYMOUS` | `bool` | `False` | Whether the model-backed stores serve anonymous requests (see [Authentication & anonymous scoping](#authentication-anonymous-scoping)). |

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
When this setting resolves to an active (non-`Null`) store, `AGUIServer` mounts
the **thread-history drawer** endpoints automatically — see
[Thread history](concepts.md#thread-history).

## `ATTACHMENT_STORE`

A dotted path to an [`AttachmentStore`][django_ag_ui.AttachmentStore] class,
importable with no arguments. `None` (the default) keeps **uploads disabled**
using [`NullAttachmentStore`][django_ag_ui.NullAttachmentStore] — the upload
endpoint answers `410 Gone`. Resolution is done by
[`resolve_attachment_store`][django_ag_ui.resolve_attachment_store], which raises
`TypeError` if the path does not produce an `AttachmentStore`.

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

DJANGO_AG_UI = {
    "ATTACHMENT_STORE": (
        "django_ag_ui.contrib.store.default_attachment_store.DefaultAttachmentStore"
    ),
}
```

When this setting resolves to an active (non-`Null`) store, `AGUIServer` mounts
the upload endpoints over HTTP automatically — see
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

## `TRANSCRIPTION_BACKEND`

A dotted path to a [`TranscriptionBackend`][django_ag_ui.TranscriptionBackend]
class, importable with no arguments. `None` (the default) keeps **voice input
disabled** using [`NullTranscriptionBackend`][django_ag_ui.NullTranscriptionBackend]
— the transcribe endpoint answers `410 Gone`. Resolution is done by
[`resolve_transcription_backend`][django_ag_ui.resolve_transcription_backend],
which raises `TypeError` if the path does not produce a `TranscriptionBackend`.

The package ships a ready reference backend over any OpenAI-compatible
`/audio/transcriptions` endpoint —
`django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend`
— self-configuring from the `OPENAI_API_KEY` environment variable (requires the
`[openai]` extra). Subclass it to change the model or point at another
OpenAI-compatible server (Azure OpenAI, Groq, a local Whisper server):

```python
DJANGO_AG_UI = {
    "TRANSCRIPTION_BACKEND": (
        "django_ag_ui.contrib.transcription.openai_transcription_backend"
        ".OpenAITranscriptionBackend"
    ),
}
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

## `DRF_MCP_SERVER`

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
DJANGO_AG_UI = {
    "DRF_MCP_SERVER": "myproject.mcp.server",
}
```
</content>

## `SERVICE_SPECS`

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

```python title="settings.py"
DJANGO_AG_UI = {"SERVICE_SPECS": "myproject.specs.SPECS"}
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
