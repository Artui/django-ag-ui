# Human-in-the-loop tool approval

A **server-side** tool runs mid-stream: by the time the browser sees the tool
call, the agent has already executed it. So the `destructive` flag on a
[`@tool`][django_ag_ui.tool] (and the `x-confirm` prompt) reaches only the
model as a hint — it is **not** a gate. This page shows how to add a real
server-side gate: a destructive tool **pauses** for the user to approve or deny
before it runs.

The whole flow rides the AG-UI protocol's own **interrupt / resume** mechanism —
the wire stays vanilla AG-UI, no custom events. The two halves live in two
packages:

- **Server (this package):** the `TOOL_GUARD` setting flips destructive tools to
  *require approval*, so instead of executing they **defer** and the run finishes
  on an interrupt.
- **Client (the web component):** it renders an inline approval card and, on the
  user's decision, resumes the run with the answer. Requires
  `@artooi/ag-ui-web-component >= 0.11.0` — no configuration, the gate is driven
  entirely by the server.

## Turn the gate on

The gate is **off by default** (no surprise gates). Opt in with the
[`TOOL_GUARD`](configuration.md#tool_guard) setting:

```python
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",
    "TOOL_GUARD": {"ENABLED": True},
}
```

Now any **destructive** tool defers for approval. A tool counts as destructive
when:

- it is a registry tool declared `@tool(destructive=True)`; **or**
- it is a drf-mcp bridged tool whose MCP `readOnlyHint` annotation is `False`
  (selectors are read-only, services mutate — the bridge maps this automatically,
  and a project can override it per registration); **or**
- it is an **in-process** drf-services spec attached through
  [`service_specs=`](configuration.md#service_specs) whose MCP annotations say
  `readOnlyHint` is `False` — the same `ServiceSpec`, reached without the MCP
  hop; **or**
- its JSON Schema carries the `x-destructive` stamp at the root, which is what
  [`build_input_schema`][django_ag_ui.build_input_schema] writes — so a tool you
  attach through `toolsets=` with a schema derived by that helper is gated
  without being in the registry; **or**
- its name is listed in `TOOL_GUARD["REQUIRE_APPROVAL"]` (force-gate a tool that
  isn't flagged destructive).

A hint has to **say** the tool mutates. An absent `readOnlyHint`, a missing stamp
or metadata of another shape leaves the tool alone — silence is not a claim, and
`REQUIRE_APPROVAL` is the answer for a tool whose source declares nothing.

!!! note "Requires django-pydantic-agent 0.18"

    The last two vocabularies arrived in the substrate's `ToolGuard` in 0.18.0.
    Below that floor the guard read only the drf-mcp metadata key, so the *same*
    spec was gated over the bridge and ungated in process — a transport swap
    silently removed the gate. This package floors at `>=0.18` for that reason.

`TOOL_GUARD["EXEMPT"]` un-gates a name even if it is destructive (`EXEMPT` wins).

```python
from django_ag_ui import ToolRegistry, tool

registry = ToolRegistry()


@tool(registry, destructive=True, confirm="Delete this project?")
def delete_project(project_id: int) -> dict:
    """Permanently delete a project."""
    ...
    return {"deleted": project_id}


@tool(registry)  # read-only → never gated
def list_projects() -> list[dict]:
    """List the caller's projects."""
    ...
```

## What the user sees

1. The model calls `delete_project`. Because it is gated, the tool **does not
   run** — the run finishes on a `RUN_FINISHED` *interrupt* outcome carrying the
   tool call id and an approve/deny response schema.
2. The web component renders an inline **approval card** next to the tool-call
   card, showing the tool and its arguments.
3. The user decides:
   - **Approve** → the client resumes the run; the server runs the tool and its
     result streams back into the same card, and the model continues.
   - **Deny** → the client resumes with a *cancelled* answer, so the model learns
     the tool was declined and responds accordingly; the card settles as
     declined. Denying is **not** the same as stopping the run — the model still
     gets a turn to react.

A **Stop** while an approval card is open denies every open card and cancels the
run.

Only `function`-kind tools are gated: a client-registered (frontend) tool is
already gated in the browser by the web component's confirmation card, and an
output tool is not executed.

## What the card asks

An interrupt carries the question the client will show, and pydantic-AI generates
it from the call itself:

```
Approve delete_project({"project_id": 7, "cascade": true})?
```

Accurate, and not something to put in front of a person. Two sources replace it,
in this order:

**1. The tool's own `confirm=`.** A destructive registry tool already carries a
human-readable question — the one the web component asks when *it* gates the call
in the browser — and the server-side gate now asks the same thing:

```python
@tool(registry, destructive=True, confirm="Delete this project and everything in it?")
def delete_project(project_id: int) -> None:
    """Delete a project."""
```

**2. `APPROVAL_PROMPTS`, for tools whose schema carries none.** A spec tool
reaching the agent in-process, or a bridged MCP tool, has no `confirm=` to read.
Name them per endpoint, which also overrides a tool's own wording:

```python
AGUIServer(
    registry,
    service_specs=spec_registry,
    config=build_ag_ui_config(
        tool_guard=ToolGuardConfig(enabled=True, require_approval=frozenset({"move_event"})),
        approval_prompts={"move_event": "Move this event, saving straight away?"},
    ),
)
```

or globally in settings:

```python
DJANGO_AG_UI = {"APPROVAL_PROMPTS": {"move_event": "Move this event?"}}
```

The phrase is stamped onto the interrupt's `metadata` as **`x-confirm`** — the
same key the web component already reads off a tool's schema for a browser-side
confirmation, so one concept covers both gates. A tool with neither source keeps
the generated question, and an interrupt whose tool supplied its own `x-confirm`
(by raising `ApprovalRequired(metadata=...)`) is left alone: it has said something
more specific than any static mapping can.

## After the approval: telling the page

An approved call runs a **server-side** tool, which means it can change data the
host page is showing without the page hearing about it. Approving a booking writes
the row; a calendar that fetched its events on mount keeps showing the week it
loaded, and nothing has gone wrong from either end's point of view.

Two channels close that gap, and they are not interchangeable:

- **The client's own signal.** The web component fires `ag-ui-run-finished` when an
  interaction ends, listing the tools that ran and which side ran them, so a host
  can refetch when any of them was a server tool. Needs nothing of the agent.
- **[Shared state](shared-state.md).** A tool mutates `ctx.deps.state` and returns
  a `STATE_SNAPSHOT`, which the component applies and reports. Richer — the two
  ends are editing one object — but it needs the *agent* to emit it, which is not
  the host page's decision to make.

### Ordering note

`TOOL_GUARD` composes a `ToolGuard` capability alongside the audit capability.
`build_agent` relies on each capability declaring its own ordering
(`AuditCapability` pins itself outermost so it still records every tool
execution) — you don't need to order them yourself.

## Custom clients

The gate is pure AG-UI, so any client can drive it — you don't need the web
component. A bespoke client:

1. POSTs a `RunAgentInput` as usual.
2. On a `RUN_FINISHED` whose `outcome.type == "interrupt"`, reads
   `outcome.interrupts[]` (each carries a `toolCallId` and a `message`).
3. Collects the user's decisions and POSTs a follow-up run with
   `RunAgentInput.resume[]` — one entry per interrupt, keyed by interrupt id,
   `status: "resolved"` (with `payload.approved = true`) to run the tool or
   `status: "cancelled"` to deny it.

**The resumed request carries two things, and the second is easy to miss.** The
`resume[]` array answers the interrupt; it does not describe the call being
resumed. That call has to be in `messages[]` as well — the assistant turn holding
the pending tool call, with **no tool message beside it**, because nothing has run
yet:

```json
{
  "messages": [
    {"id": "u1", "role": "user", "content": "delete project 7"},
    {"id": "a1", "role": "assistant", "content": "",
     "toolCalls": [{"id": "call-1", "type": "function",
                    "function": {"name": "delete_project", "arguments": "{\"project_id\": 7}"}}]}
  ],
  "resume": [{"interruptId": "int-call-1", "status": "resolved", "payload": {"approved": true}}]
}
```

Send the answer without the call and the run has no deferred request to attach it
to: it simply starts the turn over. The web component gets this right because it
keeps the agent's message list; a hand-rolled client has to remember it.

A denial arrives at the model as an ordinary **tool return** whose
`ToolReturnPart.outcome` is `"denied"` — not an error, and not a stopped run. Read
the outcome rather than the message text if your model or script reacts to
refusals.

The web component does exactly this; see its
[README](https://github.com/Artui/ag-ui-web-component#server-side-tool-approval-interrupts)
for the client-side hooks (`resolveInterrupts`, `requestApproval`) and how to
restyle or fully replace the approval card.

## Asking the user a question — `ask_user`

Approval answers yes/no. When the agent needs a **typed answer** — pick one of
these options, or type something — the web component ships a built-in `ask_user`
frontend tool. It is opt-in on the client (`chat.askUser = true`); no server
setup is required, and nothing new crosses the wire (it rides the ordinary
frontend-tool path). The agent calls
`ask_user(question, options?, allow_custom?)` and the user's answer comes back as
the tool result. See the web component's
[README](https://github.com/Artui/ag-ui-web-component#asking-the-user-a-question-ask_user).

## Security note

`TOOL_GUARD` is a **UX gate**, not an authorization boundary. It asks the acting
user to confirm an action *they are already allowed to take*. It does not decide
*who* may call a tool — that is the job of your permissions (drf-mcp permission
classes, the `authorize` hook, `get_user`). Enable the gate for destructive
actions a user should consciously confirm; enforce access separately.
