# A2UI surfaces

[A2UI](https://a2ui.org) lets the model describe a small interface (a form,
a card, a list) as data, and the browser draws it from a catalog of components
it knows. The model never writes markup. It names components from that catalog,
and the client renders only what the catalog allows.

This package implements the server half of `@ag-ui/a2ui-middleware`. The tool's
parameters, its guide and the surface snapshot match the middleware's (the
description differs in one sentence, below), and the action turn
carries the middleware's id and name. The turn's content is
`A2UI user action:\n` and the action as JSON, as the middleware writes it, with
an optional `userMessage` lead that this package adds. It provides two things:

- a **`render_a2ui` tool**, offered to the model only when the client has posted
  its catalog, which validates a surface and paints it as an `a2ui-surface`
  activity;
- an **action stage**, which turns a click on a painted surface into a user turn.

The browser half is the A2UI entry of `@artooi/agents-web-components`.

## Enabling it

Both halves are off by default and share one switch:

```python
DJANGO_AG_UI = {
    "A2UI": {"ENABLED": True},
}
```

Or for one endpoint only: `AGUIServer(registry, config=build_ag_ui_config(a2ui_enabled=True))`.

Validation needs the `[a2ui]` extra, which installs
[`ag-ui-a2ui-toolkit`](https://pypi.org/project/ag-ui-a2ui-toolkit/), the Python
port of the validator the middleware calls:

```bash
pip install "django-ag-ui[a2ui]"
```

An endpoint that enables A2UI without the extra fails with an `ImportError`
when it is mounted, not on its first run.

## The catalog comes from the client

The renderer posts its catalog on every run as a `RunAgentInput.context` entry.
The entry's `description` is exactly
[`A2UI_SCHEMA_CONTEXT_DESCRIPTION`][django_ag_ui.A2UI_SCHEMA_CONTEXT_DESCRIPTION],
and its `value` is the catalog as JSON:

```json
{"catalogId": "https://example.com/catalog.json", "protocolVersion": "1.0", "components": {...}}
```

The tool exists for a run only when the endpoint is enabled **and** that entry is
present with a string `catalogId` and an object of `components`, each of which is
itself an object. Otherwise the model is not offered it. An entry too deeply
nested for the JSON parser counts as no entry. The catalog id written into every
surface is the one in that entry. The server has no default catalog, so a surface always names a
catalog the client has registered.

The catalog's `protocolVersion` decides the wire version. `"1.0"` (or a semver
spelling such as `1.0.0`) stamps `"1.0"` on the operations. Anything else,
including no declaration, stamps `"v0.9"`.

### How the model learns the components

This package does not add the catalog to the prompt itself. It reaches the model
the way every client context entry does, through [`RUN_CONTEXT`](configuration.md#run_context),
which is on by default and fenced as client data. Mind the ceiling.
`MAX_CHARS` defaults to 20,000 characters, shared by every entry, and the A2UI
1.0 basic catalog is about 23,600 characters as the client sends it. With the
default, the component definitions arrive whole, but the end of the catalog
(the tail of `functions`, and `$defs`) is cut, and the block says so. Raise
`MAX_CHARS` if the model needs the whole catalog.

### The usage guide ships with the tool

The middleware asks the renderer to post a second context entry, a guide to
calling `render_a2ui`. This package does not wait for it. It carries the
middleware's guide text (`@ag-ui/a2ui-middleware` 0.0.16, for the protocol the
catalog declares) and gives it to the model as the tool's instructions, so the
tool's description points at nothing the client has to post. The guide does not
go through the run-context block, so it does not count against `MAX_CHARS`. A
test compares the packaged text with the middleware's own.

## The tool

`render_a2ui(surfaceId, components, data=None)` matches the middleware's
`renderA2UITool` in its parameters and description, except that the description
sends the model to the guide in its instructions rather than to one in context.
A call is checked in this order:

1. `surfaceId` must be a non-empty string, and `data`, when given, an object.
2. The toolkit validates the components against the catalog: known component
   types, required props, a component with id `root`, child references that
   resolve and do not cycle, and absolute `path` bindings that resolve in `data`.
3. The bindings must be spelled for the catalog's protocol. A 1.0 catalog
   rejects v0.9 `{"path": ...}` bindings and `literal*` values. A v0.9 catalog
   rejects 1.0 `{"@path": ...}` and `{"@call": ...}` directives.

A call that fails any check raises `ModelRetry`, with the problems listed, and
the model tries again within the agent's retry budget. **Nothing is painted for
a failed call.** A call that passes returns a short confirmation for the model,
and carries an [`a2ui_activity`][django_ag_ui.a2ui_activity] as metadata, which
pydantic-ai forwards to the stream, as with [pushed charts](charts.md). The
activity's `messageId` is `a2ui-surface-<tool call id>`. Its content is
`{"a2ui_operations": [createSurface, updateComponents, updateDataModel?]}`, the
last only when `data` was given. This is the same snapshot the middleware
emits, and a test compares the two on the wire.

!!! warning "Known limitation: 1.0 bindings are not resolved against `data`"

    The toolkit resolves absolute bindings only under `path` keys, which is the
    v0.9 spelling, so a 1.0 `{"@path": "/missing"}` passes even when `data` has
    no such path. The middleware has the same gap, and the surface is painted.

### Name conflicts

A run that offers `render_a2ui` has one tool of that name:

- **A client tool named `render_a2ui` is dropped** from the run's tools, and the
  server's tool is the one the model sees and calls, as the middleware's
  `injectToolAndFlag` replaces it. This applies only on an enabled endpoint, and
  only when the client posted a usable catalog. Otherwise the client's tool is
  left alone.
- **A tool of that name on the host is kept**, and the server's tool is not
  offered. This covers a `@tool` in the registry, a function toolset passed as
  `toolsets=` and a spec in `service_specs=`. The host's tool is its own, so the
  model calls it and nothing is painted by this package.
- **Any other source is not checked.** A `render_a2ui` that reaches the agent
  through a capability, an MCP client toolset or a wrapped toolset is not seen,
  and the duplicate name can end the run in `RUN_ERROR` (it does for a
  capability). Rename that tool before enabling A2UI on such an endpoint.

## Actions

When a user clicks a button on a surface, the renderer starts a new run whose
`forwardedProps` carries the action:

```json
{"a2uiAction": {"userAction": {"name": "book", "surfaceId": "s1", "context": {...}}}}
```

On an enabled endpoint, the server appends that action to the run's messages
**as a user message**, before the run starts. The run needs no other new user
message. The action is client input like any other: the model reads it as
something the user did, never as instructions from the operator. A
`forwardedProps` that is not an object, or an `a2uiAction` or `userAction` that
is not one, is ignored and the run goes on without a turn. On an endpoint that
has not opted in, the action is ignored.

### The action turn is a contract

A client that keeps its own transcript must post this turn back on later
requests, including a frontend tool's follow-up round. These are the turn's
fields:

| Field | Value |
| --- | --- |
| `id` | `a2ui-action-user-` + the hex SHA-256 of the run's `runId` |
| `name` | `a2ui_action_context_` + the unpadded base64url SHA-256 of the run's `runId` |
| `role` | `user` |
| `content` | `<userMessage>` + a blank line, only when `userAction.userMessage` is a non-empty string (the lead is this package's addition); then `A2UI user action:\n` + `userAction` as compact JSON, which is all the middleware writes |

The JSON is compact (`,` and `:` with no spaces), keeps the keys in the order
they were received, and leaves non-ASCII characters unescaped. A `userMessage`
is taken as given, so one of only spaces still leads the turn. The id and name
match the middleware's, byte for byte.

`forwardedProps.a2uiAction` belongs only on the action's own request. A
follow-up round or a later run that repeats it under a new `runId` adds a
second turn.

**The id and name are the turn's identity.** The server adds no turn when a user
message with that id or that name is already in the run's messages. A client's
copy whose content differs is still the same turn. For example, JavaScript
prints a float `1.0` as `1`. So:

- a later request that carries the turn under its id stores it once, in the
  place the client put it;
- a retry of the same run, with the same `runId`, re-sending `a2uiAction` while
  the turn is present, adds nothing;
- a new run carrying an action has a new `runId`, so it adds a new turn, which
  is a new action.

The turn is stored with the run's conversation, and a resumed run, whose history
the server holds, takes it as its new turn. A client that posts a later request
**without** the turn drops it from the stored conversation, because a run
stores what the client posts plus what the run produced.
