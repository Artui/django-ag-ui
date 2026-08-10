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
`[harness]` extra — **no django-ag-ui configuration beyond one dotted path.**

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
    …). They all ride the `[harness]` extra.

    Today a delegated child's answer surfaces as the parent's `delegate_task`
    tool result. Streaming a child's *own* turn (its intermediate tool calls and
    tokens) into the UI as a nested run is a possible future enhancement — the
    capability accepts an `event_stream_handler`, but django-ag-ui does not yet
    route child events onto the SSE stream.
