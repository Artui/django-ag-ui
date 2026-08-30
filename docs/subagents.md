# Delegating to sub-agents

A single request runs a single agent. When a task splits into specialised
sub-tasks — research this, then draft that — you can give the agent a roster of
**sub-agents** to delegate to.
[`pydantic-ai-harness`](https://github.com/pydantic/pydantic-ai-harness)'s
**`SubAgents`** capability exposes one `delegate_task(agent_name, task)` tool:
the parent picks a named sub-agent, hands it a task, and gets its result back —
each child runs as a fresh, isolated run.

Like [CodeMode](code-mode.md), this is pure composition over the
[`capabilities=`](configuration.md#capabilities) seam plus the optional
`[harness]` extra — **no django-ag-ui configuration beyond one capability in a
list.**

## Install

```bash
pip install "django-ag-ui[harness]"
```

The `[harness]` extra pulls `pydantic-ai-harness`; `subagents` ships in its base
package. The core install stays `django` + `pydantic-ai-slim` — the harness is
lazy, only imported by the capability you wire in.

## Wire it in

`capabilities=` takes capability instances, or zero-argument callables that
return one. Add one that returns a `SubAgents` with your child agents:

```python
# myproject/agent.py
from pydantic_ai import Agent
from pydantic_ai_harness.subagents import SubAgent, SubAgents

researcher = Agent(
    "anthropic:claude-sonnet-4.6",
    name="researcher",
    instructions="Research the topic and return concise findings.",
)
writer = Agent(
    "anthropic:claude-sonnet-4.6",
    name="writer",
    instructions="Draft prose from the findings you are given.",
)


def subagents():
    return SubAgents(
        agents=[
            SubAgent(researcher, description="Gathers facts on a topic."),
            SubAgent(writer, description="Turns findings into prose."),
        ],
        agent_folders=None,  # see the note below — disables on-disk auto-discovery
    )
```

```python
# settings.py
DJANGO_AG_UI = {"MODEL": "anthropic:claude-sonnet-4.6"}
```

```python
# urls.py
from myproject.agent import subagents

agent = AGUIServer(registry, capabilities=[subagents])
```

Now the agent exposes a `delegate_task` tool. When it calls
`delegate_task("researcher", "…")`, the child runs and its answer returns as the
tool result — which renders in the web component as an ordinary tool card. The
tool name is configurable (`SubAgents(tool_name="…")`).

!!! warning "Disable on-disk agent discovery unless you want it"
    `SubAgents` defaults `agent_folders="agents"`, which **auto-loads Markdown
    agent definitions** from `./.agents/agents/`, `~/.agents/agents/`, and the
    `.claude/` equivalents at construction time. In a server process that's
    rarely what you want — pass `agent_folders=None` to rely only on the
    `agents=[…]` you list explicitly.

## Per-delegate limits

Guardrails are fields on each `SubAgent` (there is no separate limits class), so
you can bound each child independently:

```python
from pydantic_ai.usage import UsageLimits

SubAgent(
    researcher,
    description="Gathers facts on a topic.",
    usage_limits=UsageLimits(request_limit=4),  # isolated child accounting
    timeout_seconds=30,  # cancels a runaway child
    max_calls=2,  # budget per parent run
    on_failure="Research is unavailable; answer from what you know.",
)
```

A child that trips a limit degrades softly — the parent receives a steering
message as the tool result rather than an exception, so the run continues.

!!! note
    `SubAgents` is one of several `pydantic-ai-harness` capabilities that drop
    into the same `capabilities=` seam (compaction, step-persistence, CodeMode,
    …). They ride the `[harness]` extra, except CodeMode, which needs the
    sandbox in `[code-mode]`.

## Showing the child's work

A delegated child runs to completion inside one `delegate_task` tool call, and a
tool call emits nothing between its arguments and its result. So a parent that
hands a long task to a sub-agent shows a tool card that simply sits there — for
a minute, for five — with no way to tell a working run from a wedged one.

Wrap the capability in a
[`SubAgentObserver`][django_ag_ui.SubAgentObserver] and the run reports itself as
it goes:

```python
from django_ag_ui import SubAgentObserver
from pydantic_ai_harness.subagents import SubAgent, SubAgents


def subagents():
    return SubAgentObserver(
        SubAgents(
            agents=[SubAgent(researcher, description="Gathers facts on a topic.")],
            agent_folders=None,
        )
    )
```

Opt-in by construction: passing `SubAgents` unwrapped emits nothing, and there is
no setting behind the wrapping. The observer installs itself onto the capability
you hand it — it needs `SubAgents.event_stream_handler`, which only the
capability that starts the child run can pass on — so wrapping one that already
carries a handler is refused rather than replacing it.

### What reaches the client

A stream of AG-UI `CUSTOM` events named `ag_ui.subagent`, interleaved into the
run **as they happen** rather than at the end of the delegation:

```json
{
  "type": "CUSTOM",
  "name": "ag_ui.subagent",
  "value": {
    "delegationId": "call_abc123",
    "agent": "researcher",
    "phase": "tool_call",
    "status": "researcher: calling search_docs",
    "tool": {"toolCallId": "call_def456", "name": "search_docs", "ok": null}
  }
}
```

- **`delegationId` is the parent's own `delegate_task` tool call id** — the
  `toolCallId` the client already drew a card for. That is what makes this an
  augmentation of the card on screen rather than a second row beside it.
- **`phase`** is one of `started`, `tool_call`, `tool_result`, `finished`,
  `failed`. One `started` opens a delegation and exactly one of
  `finished` / `failed` closes it, with any number of `tool_call` /
  `tool_result` pairs between.
- **`status`** is a rendered one-line summary, so a client that draws a collapsed
  row and never expands it needs nothing else.
- **`tool`** is present on the two tool phases only, always with all three keys.
  `ok` is `null` on `tool_call`, `true` on a result the child accepted and
  `false` on one that came back as a retry — a fixed shape, so a client creates
  the row on `tool_call` and updates it in place on `tool_result`.

A client that does not know the name ignores the event and the run streams
exactly as it did before: `name` is an open string the protocol leaves to
conventions like this one.

`tests/fixtures/subagent_progress_stream.json` in this repository is a full
recorded run — both a successful delegation and a failing one — generated by
`scripts/generate_subagent_fixture.py` driving the real endpoint, and
regenerated by the test suite on every run so it cannot drift from the server.
Build a client against that rather than against a hand-typed sample.

### What it deliberately does not carry

**The child's failure text.** A `failed` event names the sub-agent and stops. An
exception's own words are written for an operator, which is the same reasoning
`TOOL_FAILURE["INCLUDE_DETAIL"]` applies to `RUN_ERROR` — and nothing is lost,
because whatever the delegation returns to the parent model travels the ordinary
`TOOL_CALL_RESULT` and is rendered on the card this progress belongs to.

**The child's prose.** Progress is a status line, not a second transcript.

!!! note "Why `CUSTOM` rather than an activity"
    `@ag-ui/client` materialises an `ACTIVITY_SNAPSHOT` into a
    `role: "activity"` message, the message list is persisted wholesale, and the
    client replays it on every thread restore. That is right for a
    [chart](charts.md), which is content. Replayed *progress* is a lie: a run
    that finished last week would redraw "calling search_docs" on every reload.
    Shared state is out for a third reason — it round-trips into the next
    `RunAgentInput`, so progress placed there would be echoed back to the model.
