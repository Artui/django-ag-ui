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

## What changes for the caller

- **`start` returns once the run is enqueued, not once it holds its thread.** `thread_id`
  is chosen before the enqueue, so it is the thread the worker runs on. `run_id` is the
  queue's id for the firing, not the run store's run id: read the thread's state for that.
- **A busy thread is refused on the worker**, not at the start: the firing ends without
  its answer, as a run that lost the claim does.
- **No `capabilities=`.** They cannot cross a queue, so a queued start that passes any
  raises `ValueError`.
- **Both paths write the same lease and log.** A worker that dies mid-run is not run
  again: the lease lapses and the thread reads `failed` with reason `interrupted`.

## From sync code

[`start_from_sync`][django_ag_ui.agent.queued_start.start_from_sync] enqueues with a queue
and, with none, runs the start to its first stop and returns once it ends, since a sync
bridge has no loop that outlives it. [`enqueue_headless`][django_ag_ui.agent.queued_start.enqueue_headless]
is the enqueue alone.
