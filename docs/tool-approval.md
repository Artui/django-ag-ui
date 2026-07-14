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
- its name is listed in `TOOL_GUARD["REQUIRE_APPROVAL"]` (force-gate a tool that
  isn't flagged destructive).

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
