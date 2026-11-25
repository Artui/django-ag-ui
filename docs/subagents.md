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
from pydantic_ai_harness.sub_agents import SubAgent, SubAgents

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
from pydantic_ai_harness.sub_agents import SubAgent, SubAgents


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

Three kinds of event, interleaved into the run **as they happen** rather than at
the end of the delegation. The delegation's own lifetime rides the protocol's
events; each tool call the child makes rides a `CUSTOM` step and the protocol's
`TOOL_CALL_*` events tagged with the delegation's `subagentRunId`.

#### The delegation opens and closes on the protocol's own events

```json
{"type": "SUBAGENT_STARTED", "subagentRunId": "subagent-call_abc123",
 "name": "researcher", "parentToolCallId": "call_abc123"}
```

and closes with exactly one `SUBAGENT_FINISHED` carrying the same
`subagentRunId`, or a `SUBAGENT_ERROR` carrying it plus a `message`.

- **`parentToolCallId` is the parent's own `delegate_task` tool call id** — the
  `toolCallId` the client already drew a card for. That is what makes this an
  augmentation of the card on screen rather than a second row beside it, and it
  is the protocol's field for exactly this "agents as tools" shape.
- **`subagentRunId` names the child run.** It is derived from the delegation's
  tool call id, which is already unique per invocation — but read the link from
  `parentToolCallId`, never by taking the prefix off this one.
- **Exactly one open, exactly one close, on every path including cancellation.**
  `@ag-ui/client` verifies this: it refuses a reused `subagentRunId`, and
  refuses `RUN_FINISHED` while any delegation is still open.

#### Each of the child's tool calls rides a `CUSTOM` event

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

- **`delegationId`** is the same parent tool call id the lifecycle events carry
  as `parentToolCallId`. That is what joins the two carriers.
- **`phase`** is `tool_call` or `tool_result` on a tool step. Any number of the
  pair sit between the delegation's open and its close; under task control the
  task phases below join them.
- **`status`** is a rendered one-line summary, so a client that draws a collapsed
  row and never expands it needs nothing else.
- **`tool`** has all three keys on a tool step, and is `null` on a task step.
  `ok` is `null` on `tool_call`, `true` on a result the child accepted and
  `false` on one that came back as a retry — a fixed shape, so a client creates
  the row on `tool_call` and updates it in place on `tool_result`.

#### The same tool calls also ride tagged `TOOL_CALL_*` events

Each child tool call is sent a second time on the protocol's own vocabulary:
`TOOL_CALL_START`, `TOOL_CALL_ARGS` and `TOOL_CALL_END` as the child calls it, and
`TOOL_CALL_RESULT` when the result comes back, every one carrying the
delegation's `subagentRunId`:

```json
{"type": "TOOL_CALL_START", "subagentRunId": "subagent-call_abc123",
 "toolCallId": "call_def456", "toolCallName": "search_docs"}
```

They sit strictly between the delegation's `SUBAGENT_STARTED` and its close, carry
no `parentMessageId` (the child's messages are never announced), and the result's
`content` is the tool's return as the child's model read it. A client that renders
sub-agent tool calls natively reads these; one that draws a status row reads the
`CUSTOM` steps. Both describe the same calls, keyed by the same `toolCallId`.

A client that does not know the `CUSTOM` name ignores those events and the run
streams exactly as it did before: `name` is an open string the protocol leaves
to conventions like this one. A client that speaks AG-UI at all understands the
three lifecycle events without knowing anything about this package.

`tests/fixtures/subagent_progress_stream.json` in this repository is a full
recorded run — both a successful delegation and a failing one, on every carrier
— generated by `scripts/generate_subagent_fixture.py` driving the real endpoint,
and regenerated by the test suite on every run so it cannot drift from the
server. It records the wire every supported pydantic-ai writes: a retried tool
call's `TOOL_CALL_RESULT` is followed by a `REASONING_ENCRYPTED_VALUE`, and from
2.63 `RUN_STARTED` carries the `protocolVersion` and `TEXT_MESSAGE_START` its
`role` again. Build a client against that rather than against a hand-typed sample: the
interleaving of the two carriers is the part neither contract states on its own.

### What it deliberately does not carry

**The child's failure text.** A `SUBAGENT_ERROR` names the sub-agent and stops:
its required `message` says only which one failed, and for a failure the optional
`code` is left unset (a cancelled task carries `cancelled`, and a delegation still
open when the run finishes `unfinished`, below). An exception's own words are written for an operator, which is the
same reasoning `TOOL_FAILURE["INCLUDE_DETAIL"]` applies to `RUN_ERROR` — and
nothing is lost, because whatever the delegation returns to the parent model
travels the ordinary `TOOL_CALL_RESULT` and is rendered on the card this
progress belongs to.

**The child's prose.** Progress is a status line, not a second transcript.

!!! note "Why the steps ride two carriers"
    `@ag-ui/client` materialises an `ACTIVITY_SNAPSHOT` into a
    `role: "activity"` message, the message list is persisted wholesale, and the
    client replays it on every thread restore. That is right for a
    [chart](charts.md), which is content. Replayed *progress* is a lie: a run
    that finished last week would redraw "calling search_docs" on every reload.
    Shared state is out for a third reason: it round-trips into the next
    `RunAgentInput`, so progress placed there would be echoed back to the model.

    The tagged `TOOL_CALL_*` events are materialised into `agent.messages` too,
    with their `subagentRunId`, which is why the `CUSTOM` steps stay: they are
    the carrier a client can draw as a live status line without it coming back
    on reload. The server leaves tagged tool rounds out of the conversation it
    stores and drops posted messages carrying `subagentRunId` before the run
    loads them, so the child's calls never read as calls the parent made.

## Task control

With `SubAgents(task_control=TaskControl())`, a delegation becomes a task the
parent's model manages: it can run in the background, ask the parent a question,
be steered, be cancelled, and be resumed. The observer follows the task rather
than the `delegate_task` call, so the same wrapping covers it:

```python
from pydantic_ai_harness.sub_agents import SubAgent, SubAgents, TaskControl

SubAgentObserver(
    SubAgents(
        agents=[SubAgent(researcher, description="Gathers facts on a topic.")],
        agent_folders=None,
        task_control=TaskControl(),
    )
)
```

- **The delegation opens when the task's run starts and closes when it settles**:
  `SUBAGENT_FINISHED` for a completed task, `SUBAGENT_ERROR` for a failed one, and
  `SUBAGENT_ERROR` with `code: "cancelled"` for a cancelled one. A background
  task's `delegate_task` returns a receipt at once; its delegation stays open until
  the task's report arrives, so the child's steps land inside it.
- **A resumed task opens a new delegation** on the `delegate_task` call that
  resumed it, with a fresh `subagentRunId`, because the client refuses a reused one.
- **Nothing stays open past the run.** A delegation still open when the run
  finishes or fails is closed first with a `SUBAGENT_ERROR` carrying
  `code: "unfinished"`, because `@ag-ui/client` refuses `RUN_FINISHED` while one is
  open and a delegation panel would otherwise never settle after `RUN_ERROR`.
  Anything announced after either is dropped.

Task-control moments are `CUSTOM` steps with `tool: null` and a `task` key naming
the task:

| `phase` | When | `task` carries |
| --- | --- | --- |
| `backgrounded` | the task moved to the background | `taskId`, `reason` (`requested`, `auto`, `question`) |
| `steered` | the parent or the user sent the running task a message | `taskId`, `message`, `source` |
| `question` | the task asked the parent a question | `taskId`, `questionId`, `question` |
| `answered` | the parent or the user answered it | `taskId`, `questionId`, `answer`, `source` |
| `cancel_requested` | the parent or the user asked the task to stop | `taskId`, `forced`, `source` |
| `retried` | a remote task's worker is running it again | `taskId`, `attempt` |

Like the tool steps they carry a `status` line. They also carry what was said:

- `message` is the steering text, `question` is the child's question, and `answer` is the
  answer. Each is cut to 8000 characters, with `truncated: true` beside it when it was cut.
  The question is the child model's own words: render it as untrusted text.
- `source` is `"model"` when the run itself acted (the parent model's tool, or a harness
  listener that answered a question), and `"user"` when one of the
  [task controls](#task-controls) did. A user's act reads `<agent>: steered by you`,
  `<agent>: answered by you` or `<agent>: cancellation requested by you`.
- A steer, an answer or a cancellation that a listener vetoed before the observer saw it
  is not reported.

The steps are `CUSTOM` events, so they are never materialised into messages and never
come back on a thread restore. The run log keeps every event, though, so with a run store
the text is stored there and replays through `threads/<id>/events/`.

These follow the harness's task events under their current names
(`TaskStartedEvent`, `TaskBackgroundedEvent`, `TaskSteeredEvent`,
`TaskQuestionAskedEvent`, `TaskAnsweredEvent`, `TaskCancelRequestedEvent`,
`TaskRetriedEvent`, `TaskCompletedEvent`, `TaskFailedEvent`, `TaskCancelledEvent`).
Without `task_control` nothing here changes: the delegation opens and closes on the
`delegate_task` call, as above.

## Task controls

With a [run store](detached-runs.md), a user can act on the tasks of a run in progress.
The mount serves four routes under the same auth, CSRF and owner scope as
`threads/<id>/cancel/`. `<task_id>` is the `taskId` the steps carry.

| Route | Name | Method | Body | Answer |
| --- | --- | --- | --- | --- |
| `threads/<id>/tasks/` | `thread_tasks` | `GET` | none | `200` `{"thread_id", "run_id", "tasks": [...]}` |
| `threads/<id>/tasks/<task_id>/steer/` | `thread_task_steer` | `POST` | `{"message": str}` | `202` `{"thread_id", "task_id", "steered": true}` |
| `threads/<id>/tasks/<task_id>/answer/` | `thread_task_answer` | `POST` | `{"question_id": str, "answer": str}` | `202` `{"thread_id", "task_id", "answered": true}` |
| `threads/<id>/tasks/<task_id>/cancel/` | `thread_task_cancel` | `POST` | `{"force": bool}` (default `false`) | `202` `{"thread_id", "task_id", "cancel_requested": true, "forced": bool}` |

What each route does:

- **`steer/`** sends the running task a message, which it reads at its next model
  request.
- **`answer/`** answers the question named by `question_id`, the step's `questionId`. An
  answer to a question the task no longer waits on is refused, so a stale page cannot
  answer a newer question.
- **`cancel/`** stops the task. `force: false` stops it before its next model request, and
  `force: true` stops it at once. The task settles as a `SUBAGENT_ERROR` with
  `code: "cancelled"`.
- **`GET`** lists each running top-level task this process can control (a remote task is
  not listed) as
  `{"task_id", "delegation_id", "agent", "status", "background", "question"}`. `question` is `null`, or
  `{"question_id", "question"}` with `truncated` when it was cut. It is for a page that
  has no log to replay. A page that reattaches through `events/` already has the steps.

**A `POST` acknowledges and nothing more.** What it did arrives on the run's stream as the
step above, with `source: "user"`, so a live chat and a reattached one draw the same thing.

A refusal is `{"error", "reason", "thread_id"}`, with `"task_id"` on the per-task routes.
`error` is a sentence for an operator; `reason` is a code a client can word.

| Status | `reason` | When |
| --- | --- | --- |
| `400` | `invalid` | the body is not a JSON object; a field is the wrong type, blank, or over 8000 characters |
| `404` | `no_run` | the thread has no run in progress |
| `404` | `not_live_here` | the run is not live in this process |
| `404` | `no_task` | the run has no top-level task by that id |
| `409` | `not_running` | the task has finished; a finished task is not resumed from here, so send a user turn instead |
| `409` | `not_waiting` | the task is not waiting on that `question_id` |
| `409` | `remote` | the task runs on a remote worker (`TaskControl.remote_agents`), which these routes do not reach |

An anonymous caller gets `401`; on a mount built with `require_authenticated=False` it gets
`403`, since the run has no owner to scope it to. The wrong method gets `405`.

**Only a run live in this process is reached.** The harness keeps task control in memory,
on the run's own `DelegationTasks`, so the routes reach a run that the process serving the
request is running:

- a live run, or a detached one that carries on after its browser left;
- from any event loop, because the control hops onto the run's own loop.

A run in another process, such as a [queued run's](queued-runs.md) worker or another web
worker, answers `404` `not_live_here`. `threads/<id>/cancel/` still stops the whole run
from anywhere. The routes also need [`LIVE_REATTACH`](configuration.md#live_reattach) on,
which is the default.

**A user's control bypasses the harness's listeners.** Those listeners may veto the
parent model's `message_task`, `answer_task` and `cancel_task`. A user's control is the
user's own act under the mount's policy, so no listener sees it.

**What stays with the model:** the tools themselves. The routes and the model reach the
same task, so the first act wins, and a second answer to one question is refused on
either side. Only top-level tasks, the ones with a card, are reachable; a nested task is
steered by the task that started it.
