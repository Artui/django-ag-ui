# Shared state

AG-UI carries a **state object** alongside the conversation. The client sends it
with every run, a tool changes it, and the change streams back so the page can
re-render — agent and UI editing the same thing rather than passing messages
about it. A collaborative document, a form the agent fills in, a chart it
adjusts.

**There is nothing to configure and no package code involved.** The two ends
already exist: `AgentDeps.state` receives the client's state, and pydantic-ai's
AG-UI adapter forwards any AG-UI events a tool returns as `ToolReturn` metadata.
This page is the recipe joining them.

## Reading the client's state

Every run's `RunAgentInput.state` arrives on `ctx.deps.state`:

```python
from pydantic_ai import RunContext
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.registry.decorator import tool

from myproject.agent import registry


@tool(registry)
def summarize_document(ctx: RunContext[AgentDeps]) -> str:
    """Summarize the document the user is editing."""
    state = ctx.deps.state or {}
    return f"The document is {len(str(state.get('document', '')))} characters long."
```

By default `deps.state` is whatever the client sent — **a plain mapping,
unvalidated**, exactly like raw tool arguments.

## Validating it into a model

pydantic-ai validates inbound state against `type(deps.state)`, so it validates
only when the deps arrive already seeded with a model instance. Supply them with
`deps_factory`:

```python
from pydantic import BaseModel


class DocumentState(BaseModel):
    document: str = ""


server = AGUIServer(
    registry,
    deps_factory=lambda request: AgentDeps(
        user=request.user,
        state=DocumentState(),
    ),
)
```

Now `ctx.deps.state` is a `DocumentState`, validated on the way in — a client
sending the wrong shape is rejected before any tool runs:

```python
@tool(registry)
def summarize_document(ctx: RunContext[AgentDeps]) -> str:
    """Summarize the document the user is editing."""
    return f"The document is {len(ctx.deps.state.document)} characters long."
```

`deps_factory` replaces the default deps entirely, so it is also where a project
carries its own per-run context — subclass `AgentDeps` and add fields. Whatever
it returns reaches every tool, toolset and capability as `ctx.deps`.

## Writing state back

Nothing emits state automatically — a tool returns the event itself, as
`ToolReturn` metadata:

```python
from ag_ui.core import EventType, StateSnapshotEvent
from pydantic_ai import RunContext, ToolReturn


@tool(registry)
def write_document(ctx: RunContext[AgentDeps], body: str) -> ToolReturn:
    """Replace the shared document with `body`."""
    ctx.deps.state = {**(ctx.deps.state or {}), "document": body}
    return ToolReturn(
        return_value="Document updated.",
        metadata=[StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=ctx.deps.state)],
    )
```

`return_value` is what the **model** sees; `metadata` is what the **client**
sees. The adapter recognises AG-UI events in `metadata` and streams them
verbatim, so the browser applies the snapshot as it arrives.

`StateDeltaEvent` works the same way and carries a JSON-Patch list instead of a
whole snapshot — worth it for a large state object where a run touches one
field. A snapshot is simpler and harder to get wrong; reach for deltas when
payload size actually matters.

## The client end

`@artooi/ag-ui-web-component` seeds state and reports changes:

```js
chat.sharedState = { document: "" };

chat.addEventListener("ag-ui-state", (event) => {
  editor.value = event.detail.state.document;
});
```

That completes the loop: the host seeds it, the client sends it each run, a tool
changes it, the snapshot streams back, and the host re-renders.

## Shared state or a tool?

The web component also has `registerPageState`, which exposes host state to the
agent as ordinary `read_<name>` / `set_<name>` **tools**. Similar-sounding, and
they solve different problems:

| | Shared state | `registerPageState` |
| --- | --- | --- |
| Mechanism | Protocol state events | Ordinary client tools |
| Visible in the transcript | No | Yes, as a tool call |
| Can be gated by a confirmation card | No | Yes (`x-destructive`) |
| Good for | Agent and page editing the same object | The agent *asking* for a value or *requesting* a change |

Reach for shared state when the object is genuinely shared and the UI should
track it live. Reach for a tool when the user should see — and possibly approve —
that the agent touched something.

## Caveats

- **State is per-conversation, not durable.** It lives on the client and rides
  each request; nothing persists it server-side. A page reload starts from
  whatever the host seeds. Durable state is the conversation store's job.
- **It is client-supplied input.** A tool reading `ctx.deps.state` is reading
  something the browser sent, exactly like tool arguments. Validate it — with a
  model via `deps_factory`, or by hand — and never treat it as authorization.
- **Emit after mutating, not before.** The snapshot is a copy taken when you
  build the event; mutating `deps.state` afterwards won't reach the client until
  something emits again.
