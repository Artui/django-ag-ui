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

Only headless starts are queued. A browser run, detached or not, still runs in the process
that serves its request.

## What the caller sees

- **`start` holds the thread before it returns.** It claims the thread for a fresh
  `run_id`, saves a child's `parent` and `lineage`, then enqueues the firing with that
  `run_id`. The `HeadlessStart` it returns names the claimed run, the one the thread's
  state reports.
- **A busy thread is refused at the call**, with
  [`ThreadBusyError`][django_ag_ui.agent.thread_busy_error.ThreadBusyError], and nothing
  is enqueued.
- **A failed enqueue gives the thread back.** The claim is finished as an error, so the
  thread is free for the next start.
- **No `capabilities=`.** They cannot cross a queue, so a queued start that passes any
  raises `ValueError`. django-spec-agents' child-session tools pass the child's lineage
  this way, so a queued runner cannot serve them yet.

## What the worker does

The worker's runner is called with the claimed `run_id` and takes the claim up rather than
claim the thread again. The claim waits for it for `queued_lease=` (ten minutes unless the
runner says otherwise); a worker that comes later raises
[`ClaimLapsedError`][django_ag_ui.agent.claim_lapsed_error.ClaimLapsedError] and does not
run. A child's run composes its stored lineage on the worker, as any later run of a child
does. Both paths write the same lease and log. A worker that dies mid-run is not run again:
the lease lapses and the thread reads `failed` with reason `interrupted`.

## From sync code

[`start_from_sync`][django_ag_ui.agent.queued_start.start_from_sync] claims and enqueues on
a queued runner. With no queue it runs the start to its first stop and returns once it
ends, since a sync bridge has no loop that outlives it. The enqueue runs on the caller's
thread, inside its transaction, as `FiringQueue` expects.
