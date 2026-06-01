# django-ag-ui

A Django ↔ [Pydantic-AI](https://ai.pydantic.dev) ↔ [AG-UI](https://docs.ag-ui.com)
integration. It turns a Pydantic-AI agent into a streaming Django endpoint that
any AG-UI client can talk to, and gives you a typed, server-side tool registry
with per-tool risk metadata.

## What it is

`django-ag-ui` provides the server half of an AG-UI conversation:

- A **tool registry** ([`ToolRegistry`][django_ag_ui.ToolRegistry], the
  [`@tool`][django_ag_ui.tool] decorator) with `destructive=` and `category=`
  metadata. [`build_input_schema`][django_ag_ui.build_input_schema] derives a
  JSON Schema from each tool's signature and stamps an `x-destructive`
  extension when the tool is destructive.
- An **async Django view**
  ([`DjangoAGUIView`][django_ag_ui.DjangoAGUIView]) that wraps Pydantic-AI's
  `pydantic_ai.ui.ag_ui.AGUIAdapter`, parses the posted `RunAgentInput`, builds
  a per-request `Agent`, and returns a `StreamingHttpResponse` of AG-UI events
  (Server-Sent Events).
- An **audit boundary**: the [`AuditLogger`][django_ag_ui.AuditLogger] Protocol
  with [`NullAuditLogger`][django_ag_ui.NullAuditLogger] (default) and
  [`LoggingAuditLogger`][django_ag_ui.LoggingAuditLogger] implementations.
- Optional, pluggable **conversation persistence**
  ([`ConversationStore`][django_ag_ui.ConversationStore]), off by default.
- An optional **drf-mcp bridge** that exposes a
  [`djangorestframework-mcp-server`](https://github.com/Artui/djangorestframework-mcp-server)
  registry to the agent in-process.

## Where it sits

`django-ag-ui` is **Phase 1** of a three-package design — the Django side of
the bridge, with no admin specifics:

| Package | Role |
| --- | --- |
| **`django-ag-ui`** (this package) | Django ↔ Pydantic-AI ↔ AG-UI bridge for *any* Django project. Async view, tool registry, audit + persistence protocols. No admin code, no frontend bundle. |
| **`@artui/ag-ui-web-component`** | Framework-free `<ag-ui-chat>` Web Component for any frontend. Wraps `@ag-ui/client`, drives the DOM, and renders the confirmation modal that gates `x-destructive` tools client-side. |
| **`django-admin-agent`** | Depends on both. Ships the admin-specific server tools, the admin-aware frontend handlers, and the admin-site plumbing. |

The wire between them stays vanilla AG-UI. This package never re-implements the
AG-UI wire types; they come from `pydantic-ai[ag-ui]`.

## How a turn flows

```
Browser (AG-UI client)
   │  POST RunAgentInput { messages, tools, context, state }
   ▼
DjangoAGUIView.__call__               (async)
   │  build per-request Pydantic-AI Agent from the ToolRegistry
   ▼
AGUIAdapter (pydantic-ai)
   │  merges FE-declared tools, runs the agent, encodes events
   ▼
StreamingHttpResponse  ──SSE──▶  RUN_STARTED, TEXT_MESSAGE_*,
                                 TOOL_CALL_*, RUN_FINISHED, …
```

Server-side tools run in-process; tools declared by the client in
`RunAgentInput.tools` execute in the browser. Conversation state lives in the
posted message history by default, so the server stays stateless and
multi-worker deployments are safe.

## Next steps

- [Installation](installation.md) — `pip install`, the ASGI requirement, the
  `[drf-mcp]` extra.
- [Quickstart](quickstart.md) — register tools and mount the view.
- [Configuration](configuration.md) — every `DJANGO_AG_UI` settings key.
- [Key concepts](concepts.md) — registry, audit, streaming, persistence, the
  drf-mcp bridge.
- [API reference](api.md) — autodoc of the public surface.
</content>
</invoke>
