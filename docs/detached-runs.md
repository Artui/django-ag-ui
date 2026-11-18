# Detached and headless runs

A run normally lives inside its response. If the browser goes away, the run is cancelled
and the partial exchange is stored. A **run store** lets a run outlive its response, so
two more things become possible:

- **A detached run.** The browser asks for its run to carry on after the browser
  disconnects. The run finishes and is stored as if the browser had stayed.
- **A headless run.** The server starts a run itself, with no browser at all. A
  routine's firing or a parent run's child does this.

With no run store, a mount works exactly as before: no claim, no 409 and no detach.

## Configure a run store

`run_store=` takes a django-spec-agents
[`RunStore`][django_spec_agents.RunStore]. The reference
`DefaultRunStore` lives in the same app as the reference conversation store:

```python
from django_ag_ui import AGUIServer
from django_spec_agents.contrib.store.default_conversation_store import (
    DefaultConversationStore,
)
from django_spec_agents.contrib.store.default_run_store import DefaultRunStore
from django_spec_agents.contrib.store.default_step_store import DefaultStepStore

agent = AGUIServer(
    registry,
    id="assistant",
    conversation_store=DefaultConversationStore(),
    step_store=DefaultStepStore,
    run_store=DefaultRunStore(),
)
```

When an authenticated user starts a run, three things happen:

- **The run claims its thread.** If another run still holds the thread, the start gets
  a `409` that names the holder:
  `{"error": "the thread has a run in progress", "thread_id": ..., "run_id": ...}`.
  A run id that was used before gets a `409` of its own.
- **The run is logged.** An owner task, separate from the response, writes each AG-UI
  event to the run's log as it happens. Consecutive text deltas of one message are
  merged into a single entry. The transcript, partial output included, stays in the
  conversation store.
- **The run keeps a lease.** It renews the lease while it runs. If a cancel has been
  recorded for the thread, the run ends as a cancel.

An anonymous run is never claimed, logged or detached. Its owner scope needs a browser
session.

## Detach a run

The client opts in for each run, through `RunAgentInput.forwardedProps`:

```json
{"forwardedProps": {"detach": true}}
```

The response carries the same events as before. If the client disconnects, only its
subscription closes: the run finishes, its transcript is stored, and no cancel is
audited. A run that did not ask to detach is still cancelled when its client
disconnects.

A browser run detaches only on a mount with `RUN_TIMEOUT_SECONDS` set, because a run
that nobody returns to needs an end of its own: the
[cancel route](#reattach-and-cancel) stops a run only when a client asks. On a mount without it, a
run that asks to detach is cancelled on disconnect as before. The headless runner
requires `timeout=` for the same reason.

The owner task runs on the event loop that served the request. To outlive the request
it needs a loop that outlives it, which means ASGI. Under WSGI the run starts on the
loop that drains the buffered response and finishes there, so the response carries the
whole run and `detach` has nothing to outlive. A restart loses any run in flight:
its lease lapses, and the store then reports the thread as `failed` with the reason
`interrupted`.

## Reattach and cancel

A mount with a run store serves two more routes per thread. Both answer only the
thread's owner, with the same authentication as the endpoint, and a `404` for a thread
that is not theirs.

### Read a run's events

`GET <mount>threads/<id>/events/?after=<seq>` reads the log of the thread's current run,
or its last one once it has ended. `after` is a sequence number within that run, `0` to
read from the start. Any process can answer, since it reads only the run store.

With `Accept: text/event-stream` the answer is an AG-UI stream that replays the run and
then follows it to its end:

1. The run's `RUN_STARTED`.
2. A `MESSAGES_SNAPSHOT`: the stored conversation, without the messages this run
   streams, followed by the run's messages as they stood at `after`.
3. The start event of each item still open at `after`: a text message, a tool call with
   its arguments so far in one `TOOL_CALL_ARGS`, a reasoning span or message, a step, or
   a subagent.
4. Every logged event after `after`, each with an SSE `id:` holding its sequence number,
   until `RUN_FINISHED` or `RUN_ERROR`.

The replay passes `@ag-ui/client`'s `verifyEvents` from any `after`, and leaves a fresh
client with the messages a client that watched the whole run holds. A client that
reconnects can send the last `id:` it received. The items are re-opened after the
snapshot, not before, because the client's reducer would otherwise mark a tool call
it already holds for argument replacement. AG-UI 1.0 has no resumption, so this route is
an extension of the protocol.

`after=0` is also the first view of a run queued for a worker. The stream waits for the
run's first event. If a run ends with no `RUN_FINISHED` or `RUN_ERROR` in its log, the
stream closes on a `RUN_ERROR` with the message `the run ended without finishing` and a
`code` that is the thread's end reason, or its status. A lapsed lease, for example,
gives `interrupted`.

Without that `Accept` header the answer is one JSON page of raw entries:

```json
{
  "thread_id": "t1",
  "run_id": "r1",
  "status": "running",
  "events": [{"seq": 1, "event": {"type": "RUN_STARTED", "...": "..."}}],
  "after": 1,
  "done": false
}
```

Pass `after` back to read the next page. `limit` caps a page at 100 entries by default,
and at 500 at most. `done` is `true` on the last page of a run that has ended. Each page
carries an `ETag`, and a request that sends it back in `If-None-Match` gets a `304` while
nothing has changed. An `after` or `limit` that is not a whole number in range gets a `400`.

### Cancel a run

`POST <mount>threads/<id>/cancel/` records a cancel for the thread's live run and answers
`202` with `{"thread_id": ..., "cancel_requested": true}`. The run's owner acts on it the
next time it renews its lease, about every ten seconds. A thread
with no run in progress gets a `404`. The route is checked by CSRF unless the mount
says otherwise.

## Start a headless run

[`AGUIHeadlessRunner`][django_ag_ui.agent.headless_runner.AGUIHeadlessRunner]
implements django-spec-agents'
[`HeadlessRunner`][django_spec_agents.HeadlessRunner] over one or more mounts. Each
mount is keyed by its `id`:

```python
from django_ag_ui.agent.headless_runner import AGUIHeadlessRunner

runner = AGUIHeadlessRunner(agent)

stop = await runner.run_to_first_stop("assistant", "Summarise today's tickets", user, timeout=300)
```

- **Startup checks.** The runner refuses a mount, when it is built, that has no `id`, no
  `run_store`, or no `step_store`. It also refuses a mount whose conversation store is
  `NullConversationStore` or `DjangoSessionConversationStore`.
- **`timeout=` is required.** It is passed to the run as pydantic-ai's `timeout=`. A run
  that passes it ends `timed_out`, with the partial kept.
- **The principal must be a user.** An anonymous principal is refused. The caller
  authorizes the run: the mount's `get_user`, `authorize` and `throttle` are not
  consulted.
- **History comes from the thread.** The prompt is the run's only new message. The
  thread's stored conversation is its history. It carries no client tools.
- **Capabilities are added.** `capabilities=` are composed into the run beside the
  mount's own.
- **Starting.** `start` returns as soon as the run has claimed its thread. A thread that
  another run holds raises
  [`ThreadBusyError`][django_ag_ui.agent.thread_busy_error.ThreadBusyError].
- **Cancelling.** `cancel(thread_id, principal)` records a cancel. The run acts on it
  the next time it renews its lease.

## A run that parks

A run on a mount with a run store that ends on deferred calls finishes `waiting`, not
`completed`, and its thread reads `waiting` until a run takes it up. The thread's
`RunWaiting` names the first such call, approvals first:

| Field | Value |
| --- | --- |
| `kind` | `approval` for a gated call, `question` for a tool that raised `CallDeferred(kind="question")`, `external` for any other deferred call |
| `summary` | the call's `APPROVAL_PROMPTS` entry, or its tool's name |
| `tool_call_id` | the call's id; the run's `RUN_FINISHED` interrupt for it is `int-<tool_call_id>` |
| `destructive` | `ToolGuard.requires_approval` for the call's tool, whether the guard is on or not |

A browser run's own client tools count only when the run is detached: a connected client
answers their calls itself, so a run that ends on them alone finishes `completed`, as
before. A headless run's `RunStop` carries the same `waiting`.

## Child sessions

A child session is a thread with a parent: django-spec-agents' child-session tools start
one through `start(..., parent=, lineage=)`, which saves both on the child's conversation
before it returns, so `DefaultChildSessionIndex` lists the child under its parent at once.
`parent` is a `ThreadRef` in any scope, so the child may run on another agent.

Every run of that thread keeps its parent's bounds, the first included, whether a headless
start, a browser turn into the child, a resume after an approval, or a headless wake: the
mount rebuilds the stored lineage with `child_lineage_for` and composes it into the run.
A browser turn into a child marks its report as one the user joined, so the parent knows
the result may reflect the user's direction. The child sees only the
tools its parent had, a tool its parent gated still asks for approval, and the run's
settle enqueues a report on the parent's inbound queue. A run that parks reports nothing.
A mount that serves child sessions names its queue, and `id=` names the agent each report
comes from:

```python
agent = AGUIServer(
    registry,
    id="helper",
    run_store=DefaultRunStore(),
    step_store=DefaultStepStore,
    conversation_store=DefaultConversationStore(),
    inbound_queue=DefaultInboundQueue(),
    child_spend=spend,
)
```

A thread parked on a question is resumed with `start(..., answers={tool_call_id: text})`:
the run continues the stored history with those answers and sends no new prompt.
`wake_thread` resumes a parked thread this way when the park is recorded as waiting on a
question ([a run that parks](#a-run-that-parks)): an `ANSWER` on the child's inbound
queue resumes the parked call with its text.

After a headless run's end is recorded, the runner calls django-spec-agents'
`wake_thread` for its thread when mail arrived during the run, so a message that landed
after the run's last read starts the thread again. A run whose caller waits for its
result, as a queued worker or `start_from_sync` does, runs the woken run to its first
stop before it returns, since its event loop may end with it. Mail that was already
pending when the run started does not wake it again, since a mount that does not read
its queue would otherwise wake forever, and a run that failed does not wake. A browser
run's end wakes nothing.

`inbound_queue=` without `id=` is refused at startup, and a run of a child thread on a
mount with no queue raises `ImproperlyConfigured` rather than run unbounded. `child_spend=`
is optional; given, the run counts against the child-session budgets as well.

Every seam a run reaches takes a request: the stores, `deps_factory`, the MCP bridge and
`step_store`. A headless run gets the mount's `headless_request(principal)`, an
`HttpRequest` whose `user` is the principal, with no session. Override it in a subclass
when one of your hooks reads state that your middleware sets.
