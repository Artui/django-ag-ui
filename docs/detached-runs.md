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
  event to the run's log as it happens. Consecutive deltas are merged into one entry
  when they belong to the same text message, the same reasoning message, or the same
  tool call's arguments. While deltas keep arriving the merged entry is cut about every
  half second, and the last one is written with the next event, so a long tool call
  writes a few entries rather than one per token. The
  transcript, partial output included, stays in the conversation store.
- **The run keeps a lease.** It renews the lease while it runs. If a cancel has been
  recorded for the thread, the run ends as a cancel.

An anonymous run is never claimed, logged or detached. Its owner scope needs a browser
session.

### Why the log write is awaited

The owner waits for each log write before it takes the next event, so a slow database
slows the run's live stream. Handing the writes to a background writer would take that
cost off the stream, and was considered and rejected for two reasons:

- **A crash loses the unwritten tail.** The run reads as interrupted either way, but a
  reattach would show less of it than the client saw live.
- **A lost claim is seen late.** The store answers a write for a run that no longer holds
  its thread by refusing it, and that refusal is what cancels the run. With writes in the
  background, a run that lost its thread would keep streaming until the next flush.

Merging deltas is what keeps the number of writes down instead.

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

On a mount with a `firing_queue=`, a detached browser run is enqueued for a worker
instead and its POST answers `200 {"thread_id", "run_id"}`; see
[Queued runs](queued-runs.md#a-detached-browser-start).

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
read from the start. Any process can answer, since everything it needs is in the run
store. How it follows a live run is in [How a reader follows a run](#how-a-reader-follows-a-run).

With `Accept: text/event-stream` the answer is an AG-UI stream that replays the run and
then follows it to its end:

1. The run's `RUN_STARTED`. Its `input` carries the run's new turn (the messages after
   the last answer the client posted, without inline file bytes, tools, context or
   state), so a run still in flight can show the question it is answering before the
   conversation row is saved.
2. A `MESSAGES_SNAPSHOT`: the stored conversation, then the new turn where the row does
   not hold it yet, without the messages this run streams, followed by the run's
   messages as they stood at `after`.
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

### How a reader follows a run

The stream follows a run that is still going in one of two ways:

- **From memory, in the process that owns the run.** Each server keeps the runs it owns,
  keyed by owner, thread and run id. A reattach that this process owns subscribes to the
  run's feed first, then reads the log once for the replay. After that it takes each new
  entry from the feed as the owner writes it, and skips any it already replayed. It reads
  the log no further. The feed carries the logged entries, merged deltas included, so
  they have the same sequence numbers as the log. Each event arrives once, across the
  seam from replay to live.
- **From the store, anywhere else.** A reattach served by another worker or process reads
  the log after its cursor. When nothing is new and the run is still live, it waits on the
  run store's `wait`, if the store is a
  [`WatchableRunStore`](https://artui.github.io/django-spec-agents/storage/) such as
  `DefaultRunStore`, for up to ten seconds, and reads again. A store without `wait` is
  read again every half second, as before. A run whose owner died without finishing
  shows as failed once its lease lapses. The reader sees that when its wait times out.

An anonymous request has no owner to key on, so it always reads the store. Turn the
in-memory path off with [`LIVE_REATTACH`](configuration.md#live_reattach), for a
deployment where a reattach never lands in the process that owns the run. A miss reads
the store in either case.

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
`waiting` holds a `RunWaiting` for every such call, approvals first, each with:

| Field | Value |
| --- | --- |
| `kind` | `approval` for a gated call, `question` for a tool that raised `CallDeferred(kind="question")`, `external` for any other deferred call |
| `summary` | the call's `APPROVAL_PROMPTS` entry, or its tool's name |
| `tool_call_id` | the call's id; the run's `RUN_FINISHED` interrupt for it is `int-<tool_call_id>` |
| `destructive` | `ToolGuard.requires_approval` for the call's tool, whether the guard is on or not |

A browser run's own client tools count only when the run is detached: a connected client
answers their calls itself, so a run that ends on them alone finishes `completed`, as
before. A headless run's `RunStop` carries the same `waiting` tuple.

An answer to a parked run resumes it through `resume/<run_id>/`, with `resume` naming
every interrupt the thread lists (a client tool's call is answered by its tool message).
A resume that leaves a call unanswered is a `400` naming those calls in `unanswered`,
before the thread is claimed, so the park stands and a full answer can follow; so is an
answer to some of a parked thread's calls on the plain route.
The mount saves the parked run's messages when it parks, and the route
loads them only while the run store shows that run as its thread's `waiting` run; any
other run id answers 404. On a django-spec-agents `ResumableRunStore` (`DefaultRunStore`
is one) the resume claims the thread only while it is still parked on that run, in the
claim's one conditional update, so of two resumes of one park the approved call runs once:
the other answers `409` naming the run that holds the thread, or `404` once it has ended.

## Thread status

With a run store, each row of `GET <mount>threads/` also carries its thread's run status,
read from the run store and never written by the list. Every key is additive, so an older
client ignores them; without a run store the rows carry none of them.

| Key | Value |
| --- | --- |
| `status` | `idle`, `running`, `waiting` or `failed`; a thread no run claimed reads `idle`, and a run whose lease lapsed reads `failed` |
| `last_read_at` | when the caller last marked the thread read (ISO 8601), or `null` |
| `reason` | with `failed` only: `error` (the run raised) or `interrupted` (its lease lapsed); a timeout reads `idle` and keeps its reason server-side |
| `interrupts` | with `waiting` only: the protocol's interrupt list, one per call the run parked on |
| `run_id` | with `waiting` only, on a mount that serves `resume/<run_id>/` (one with a `step_store=`): the parked run's id, the one that route accepts; absent otherwise, so its presence means the row is answerable in place |
| `agent_id` | the mount's `id=`, when it has one |

The `run_id` assumes the mount's conversation, run and step stores are scoped alike
([Multiple endpoints](configuration.md#multiple-endpoints)): two mounts sharing an unscoped
conversation and run store, and only one a step store, list each thread on both: the mount
with the step store names a `run_id` for the other mount's parked runs too, and answers
their `resume/<run_id>/` with a `404`, having no snapshot of them. A `step_store=`
added to a mount with threads already waiting likewise advertises run ids it has no
snapshot for: the client's resume gets a `404` and the thread is answered in its
conversation.

A client answers a waiting row from the row alone: it POSTs the answer to `resume/<run_id>/`
with the row's `run_id`. The `run_id` changes when the thread parks again on a new run, so
it also changes the list's ETag.

An interrupt is an AG-UI `Interrupt` sharing its `id` and `toolCallId` with the one the
protocol uses for the call, so a client answers a parked approval with `@ag-ui/client`'s
`buildResumeArray` and the `resume/<run_id>/` route above:

```json
{
  "id": "int-call-9",
  "reason": "tool_call",
  "message": "Delete the invoice?",
  "toolCallId": "call-9",
  "metadata": {"destructive": true, "kind": "approval"}
}
```

`message` is the `RunWaiting` summary, `reason` is `tool_call` for every kind (each is a
deferred tool call) and `metadata.kind` keeps `approval`, `question` or `external`, which is
how a client tells them apart: a resume entry answers an approval only. No
deadline is stored, so there is no `expiresAt`. A run parked on several calls lists one
interrupt for each, approvals first.

`AGUIServer(name=, description=)` says who the mount is: when it or `id=` is set, the
list body carries `"agent": {"id", "name", "description"}` beside `threads`.

### Poll or stream the list

Both transports carry the same body, so a client keeps one parser:

- **Poll.** The list answers with an `ETag` over its body and `Cache-Control: private,
  no-cache`; send it back as `If-None-Match` and an unchanged list answers `304`.
- **Stream.** `Accept: text/event-stream` on the same URL streams a frame each time the
  body changes, polling the stores every two seconds, with `id:` the body's ETag and
  `data:` the body itself. A reconnect sending `Last-Event-ID` skips the first frame while
  the list is unchanged. The stream lives five minutes, then ends; `EventSource` reconnects
  on its own with `Last-Event-ID`, and the reconnect checks the caller's authentication
  again, so a signed-out session is refused at most five minutes late. The stream needs
  ASGI: under WSGI the same request is answered as a poll, with the JSON body and the
  `ETag`. The list does not wait on the run store's `wait`, which watches one run's log.
  The list changes on events a run log does not record, such as a new thread or a
  rename, and every streamed token would wake it. A browser `EventSource` treats that JSON answer as a fatal error and does not
  reconnect, so a client that may meet a WSGI deployment falls back to polling on the
  stream's `error`. Both answers carry `Vary: Accept`, since one URL serves either by that
  header.

An `If-None-Match` naming the list's current `ETag` answers `304` whether it is the exact
tag, a weak `W/"..."` copy (what `GZipMiddleware` turns it into), one of a list, or `*`.

### Mark a thread read

`POST <mount>threads/<id>/read/` sets the thread's `last_read_at` to now and answers `204`.
A thread the caller does not own answers `404`. A thread no run ever claimed has no run
state to mark, so it stays `null`. The route is mounted with a run store and a
conversation store, and it follows the mount's `csrf_exempt=`, as `cancel/` does.

## Child sessions

A child session is a thread with a parent: django-spec-agents' child-session tools start
one through `start(..., parent=, lineage=)`, which saves both on the child's conversation
before it returns, so `DefaultChildSessionIndex` lists the child under its parent at once.
`parent` is a `ThreadRef` in any scope, so the child may run on another agent.

The start also names the parent run as `parent_run_id`, and the child's first
`RUN_STARTED` carries it as `parentRunId`, so a client can tell which run of the parent
thread started the child. It is the parent's AG-UI run id: the mount hands that id to
pydantic-ai as the agent run's own, so `RunContext.run_id` is the id the client saw. A
wake that resumes a child parked on a question names the parked run the same way. A
queued start carries `parent_run_id`, and a routine firing's `routine_id`, to the worker.

Each row of `GET <mount>threads/` carries that link as `parent`, with or without a run
store: `{"thread_id": "...", "scope": "..."}` for a child, where `scope` is the parent's
agent id or `ScopedConversationStore` scope (`null` in an unscoped store), and `null` for
a thread of its own. A child listed by another mount names its parent in the parent's
scope, so a client opens the parent from the mount whose `agent_id` matches `scope`.

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
the run continues the stored history with those answers and sends no new prompt. The
start claims the thread only while it is still parked on the run it read, so of two
answers to one park the model runs once: the other raises `ThreadBusyError` naming the run
that holds the thread, or django-spec-agents' `RunNotParkedError` once that run has ended.
`answers` must cover every call the thread is parked on: one that leaves a call open
raises [`PartialAnswerError`][django_ag_ui.agent.partial_answer_error.PartialAnswerError]
naming those calls before the claim, queued or not, and the park stands.
A start that fails after its claim, or a queued one whose enqueue is refused, gives the
claim back where the store can, so the thread stays parked and the answer can be sent again.
`wake_thread` resumes a parked thread this way when every call the park waits on is a
question ([a run that parks](#a-run-that-parks)) and an `ANSWER` is pending on the
child's inbound queue for each: each answer resumes its call with its text.

After a headless run's end is recorded, the runner calls django-spec-agents'
`wake_thread` for its thread when mail arrived during the run, so a message that landed
after the run's last read starts the thread again; a queued run counts mail from when it
was enqueued, not from when a worker took it up. A run whose caller waits for its
result, as a queued worker or `start_from_sync` does, runs the woken run to its first
stop before it returns, since its event loop may end with it. Mail that was already
pending when the run started does not wake it again, since a mount that does not read
its queue would otherwise wake forever, and a run that failed does not wake. A browser
run's end wakes nothing.

### A message no user wrote says where it came from

The wake's own line is no user's words, so `wake_thread` passes
`prompt_metadata={"origin": "wake"}` and the runner stores it as the `metadata` of that
user message, queued or not; the model sees only the text. The reports and questions the
wake then hands the model ride the run's enqueued-messages activity, and each of its user
entries carries the `metadata` its part was built with: `origin` is `"task_output"` for a
child's report or `"task_question"` for its question, and `task` names it (`taskId`,
`threadId`, `agentName`, `outcome` on a report, `questionId` on a question, and `body`, the
plain text). The entry's `content` stays in the framed form the model saw:

```json
{"role": "user", "content": "<task_output …>all done</task_output>",
 "metadata": {"origin": "task_output",
              "task": {"taskId": "c1", "threadId": "c1", "agentName": "helper",
                       "outcome": "ok", "body": "all done"}}}
```

The stored activity carries the same, and so does a replay of server-held history. A
part with no metadata, such as a line the tool enqueued itself, carries none.

`inbound_queue=` without `id=` is refused at startup, and a run of a child thread on a
mount with no queue raises `ImproperlyConfigured` rather than run unbounded. `child_spend=`
is optional; given, the run counts against the child-session budgets as well.

Every seam a run reaches takes a request: the stores, `deps_factory`, the MCP bridge and
`step_store`. A headless run gets the mount's `headless_request(principal)`, an
`HttpRequest` whose `user` is the principal, with no session. Override it in a subclass
when one of your hooks reads state that your middleware sets.

### Delete a parent

A mount deletes a thread through its own conversation store, which knows nothing of the
thread's children in other mounts. Give it `child_threads=`, a callable returning the
`AGUIHeadlessRunner` that holds every mount, and give the runner the `index=` it walks:

```python
store, run_store = DefaultConversationStore(), DefaultRunStore()
runner: AGUIHeadlessRunner | None = None
planner = AGUIServer(
    registry,
    id="planner",
    run_store=run_store,
    step_store=DefaultStepStore,
    conversation_store=ScopedConversationStore(store, scope="planner"),
    child_threads=lambda: runner,
)
helper = AGUIServer(
    registry,
    id="helper",
    run_store=run_store,
    step_store=DefaultStepStore,
    conversation_store=ScopedConversationStore(store, scope="helper"),
)
runner = AGUIHeadlessRunner(planner, helper, index=DefaultChildSessionIndex(run_store))
```

It is a callable because the runner is built from the mounts. `DELETE <mount>threads/<id>/`
then keeps the thread's children: it records a cancel on every descendant whose run holds
its thread, in that descendant's own mount, so a running grandchild stops too. An idle
descendant is left alone, and one parked on a question keeps its park, since a cancel
reaches only a run that holds the thread. It clears each direct child's `parent` and
`lineage` in the child's own mount, so the child lists as a thread of its own there, and
it deletes the thread last. A
child is found in the mount whose `id` is its scope, so give each mount's
`ScopedConversationStore` the mount's id as its scope; in an unscoped store it is found in
its parent's mount. The whole tree is resolved first: a child in a scope no mount of the
runner serves raises `LookupError`, and nothing has changed; through the route, that
`DELETE` answers `409` with the error rather than `204`.

With `index=`, every mount's conversation store must be an `UnlinkableConversationStore`
(`DefaultConversationStore` is one, and so is a `ScopedConversationStore` over one), or
the runner is refused at construction. `child_threads=` without `id=` is refused too.
Without `child_threads=`, `DELETE` deletes the one thread, as before.

`GET <mount>threads/<id>/children/`, mounted with `child_threads=`, lists the thread's
children in every scope, oldest first:

```json
{"children": [
  {"thread_id": "c1", "agent_id": "helper", "status": "running"}
]}
```

`agent_id` is the child's scope (`null` in an unscoped store), and the status keys are a
thread row's, without `last_read_at` and `run_id`: `reason` when failed and `interrupts` when
waiting.
Like every route it is the caller's: another owner's children are absent, and a thread the
caller does not own answers `404`.
