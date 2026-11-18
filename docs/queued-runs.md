# Queued runs

By default a headless start runs as an asyncio task in the process that started it, so it
needs a loop that outlives the call (ASGI). Give the runner a
[`FiringQueue`][django_spec_agents.headless.types.firing_queue.FiringQueue] and every
start is enqueued instead: a worker runs it, and its owner lives there.

```python
runner = AGUIHeadlessRunner(server, queue=queue)
started = await runner.start("helper", "summarise the inbox", user, timeout=300)
```

The host fills the queue (django-spec-tasks' `enqueue` and `state_of`) over a registry that
holds `run_headless(runner, permissions=[...])`, built over a runner with no queue, so the
worker runs the start in its own process. Pass `spec_name=` when the spec is registered
under another name than `"run_headless"`.

A browser run that stays connected always runs in the process that serves its request. A
detached one is queued when its mount has a queue too: see
[A detached browser start](#a-detached-browser-start).

## What the caller sees

- **`start` holds the thread before it returns.** It takes the thread's inbox mark,
  claims the thread for a fresh `run_id`, saves a child's `parent` and `lineage`, then
  enqueues the firing with that `run_id` and that `inbox_mark`. The `HeadlessStart` it
  returns names the claimed run, the one the thread's state reports.
- **A busy thread is refused at the call**, with
  [`ThreadBusyError`][django_ag_ui.agent.thread_busy_error.ThreadBusyError], and nothing
  is enqueued.
- **A failed enqueue gives the thread back.** The claim is finished as an error, so the
  thread is free for the next start. A start that answers a parked thread (`answers=`)
  gives its claim back instead, on a store that can (a django-spec-agents
  `ResumableRunStore`): the thread keeps its park, and a later `answers=` still resumes
  it. The spec's permission refusal at enqueue, `NotPermitted` or `PermissionDenied`,
  is such a failure.
- **No `capabilities=`.** They cannot cross a queue, so a queued start that passes any
  raises `ValueError`. A child session needs none: django-spec-agents' child-session
  tools (0.21.0 and later) pass only `parent=` and `lineage=`, which the firing carries,
  and the worker composes the child's bounds from them.
- **`answers=` crosses the queue.** A start that resumes a thread parked on a question
  carries its answers in the firing, and the worker resumes the stored history with them.
  The start claims the thread only while it is still parked on the run it read, so of two
  answers to one park one is enqueued: the other raises `ThreadBusyError` naming the run
  that holds the thread, or django-spec-agents' `RunNotParkedError` once that run has
  ended. A thread that is not waiting takes a plain claim, as it always did.

## What the worker does

The worker's runner is called with the claimed `run_id` and takes the claim up rather than
claim the thread again. The claim waits for it for `queued_lease=` (ten minutes unless the
runner says otherwise); a worker that comes later raises
[`ClaimLapsedError`][django_ag_ui.agent.claim_lapsed_error.ClaimLapsedError] and does not
run. A child's run composes its stored lineage on the worker, its first run as much as any
later one. Both paths write the same lease and log, but for one entry: on a mount with an
inbound queue, the claim keeps the inbox mark taken at the start as the log's first entry,
which the events replay skips. After the run, the worker wakes the thread for mail newer
than that mark, so mail that arrives while the firing waits in the queue wakes it too
(`run_headless` does not hand the runner the firing's `inbox_mark`, so the worker reads it
back from the log). A worker that dies mid-run is not run again:
the lease lapses and the thread reads `failed` with reason `interrupted`.

## A detached browser start

Give the mount the same queue, `AGUIServer(firing_queue=queue)`, and a browser run that asks
to detach (`forwardedProps.detach`) is queued rather than run in the serving process:

1. The POST is checked as before (authentication, throttle, identifiers, and a resume's
   `404` or `409`), then claims the thread. A busy thread or a reused run id answers `409`,
   and nothing is enqueued. A resume of a parked run claims the thread only while it is
   still parked on that run, so of two resumes of one park (a double click, two tabs) one
   is enqueued; the other answers `409` naming the run that holds the thread, or `404`
   once that run has ended. The queue checks the spec's permissions at enqueue, after the
   claim, so a refusal (`NotPermitted`, or Django's `PermissionDenied`) answers `403` and
   the claim is given back: the thread keeps the state it had before, a park included, so
   the parked run can still be resumed, and the refused run's log is deleted. A run store
   that cannot give a claim back (one that is not a django-spec-agents
   `ResumableRunStore`) finishes the refused run as an error instead.
2. The run's whole `RunAgentInput` as the client sent it (client tools, state, context,
   `forwardedProps`, an A2UI catalog) is kept as the first entry of the run's log, with
   the run it resumes, if any, and the thread's inbox mark. A resume of a parked run
   records that it was parked while the thread still says so.
3. The start enqueues a `HeadlessFiring` with the claimed `run_id` and answers
   `200 {"thread_id", "run_id"}`. The client reads the run from
   [`events?after=0`](detached-runs.md#read-a-runs-events), which waits for its first
   event; the replay never sends the kept input.
4. The worker takes the claim up, reads the input back by `run_id`, and starts it as the
   POST would have, detached, so the browser's own client tools park the run. A firing
   whose input is gone finishes its claim as an error rather than run with no prompt.

The mount needs what a headless run needs (`id=`, `run_store=`, `step_store=`, and a
conversation store that keeps conversations without a browser session) and
`RUN_TIMEOUT_SECONDS`, which is the queued run's `timeout`; it refuses at construction
otherwise. A connected browser run on that mount still streams in-process.

On the worker every per-request hook (`model_for_request`, `instructions_for_request`,
`deps_factory`, the MCP bridge, the attachment store, `step_store`) is handed
`headless_request(principal)`, not the browser's request: the worker never sees its
headers, session or middleware state. Override `headless_request` when a hook reads them.

## From sync code

[`start_from_sync`][django_ag_ui.agent.queued_start.start_from_sync] claims and enqueues on
a queued runner. With no queue it runs the start to its first stop and returns once it
ends, since a sync bridge has no loop that outlives it. The enqueue runs on the caller's
thread, inside its transaction, as `FiringQueue` expects.
