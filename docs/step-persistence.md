# Durable step persistence

A normal run persists its **message history** when it finishes (see the
[conversation store](configuration.md#conversation_store)). That says nothing
about a run that died *mid-tool*: did the side effect land? And it offers no
safe point to resume or fork from.

[`pydantic-ai-harness`](https://github.com/pydantic/pydantic-ai-harness)'s
**`StepPersistence`** capability records the finer grain — an append-only event
log, a **tool-effect ledger** keyed on `(run_id, tool_call_id)`, and a
**continuable snapshot** at every provider-valid boundary. django-ag-ui backs it
with a durable, **owner-scoped** Django store so a run's lineage survives a
process restart and one user can never read another's runs.

## Install

```bash
pip install "django-ag-ui[harness]"
```

The `[harness]` extra pulls `pydantic-ai-harness`. The core install stays
`django` + `pydantic-ai-slim` — the harness is lazy, only imported when a
`step_store` is configured.

Add the reference store app and migrate its tables (the same app the reference
conversation store uses):

```python
# settings.py
INSTALLED_APPS = [
    # ...
    "django_pydantic_agent.contrib.store",
]
```

```bash
python manage.py migrate
```

## Wire it in

`step_store` is a **factory** — a `request -> StepStore` callable, *not* a shared
store instance. The harness step-store protocol's methods carry no request, so
the store binds one and is built fresh per run. `DefaultStepStore`'s constructor
*is* that factory, so pass the class itself:

```python
# urls.py
from django.urls import path

from django_ag_ui import AGUIServer
from django_pydantic_agent.contrib.store.default_step_store import DefaultStepStore

from myproject.agent import registry

agent = AGUIServer(
    registry,
    step_store=DefaultStepStore,
    csrf_exempt=False,  # cookie-authenticated deployment
)

urlpatterns = [path("agent/", agent.urls)]
```

Every run now attaches a `StepPersistence` capability keyed on the AG-UI
`run_id`, recording a run / event / snapshot / tool-effect ledger through the
store. This is a dedicated argument rather than one of the
[`capabilities`](configuration.md#capabilities) — unlike `CodeMode`, step
persistence needs the request (to scope the owner) and the run id, so it can't
ride a zero-argument capability callable.

## Owner scoping and anonymous runs

Every row is filtered by the resolved **owner** — the authenticated user's pk, or
an `anon:<session_key>` bucket under
[`ALLOW_ANONYMOUS`](configuration.md#allow_anonymous). The harness records carry
no owner; this store adds it, so `latest_snapshot(run_id=…)` for one user never
returns another user's snapshot even if the `run_id` is guessed — the `run_id`
is not a secret, the owner is the boundary.

An anonymous request with `ALLOW_ANONYMOUS` off has no durable identity, so the
store **degrades instead of crashing**: writes no-op and reads return empty (the
run still streams, it just isn't recorded — the capability's hooks fire mid-run,
so a hard refusal would abort it). The endpoint's `require_authenticated` default
already keeps that case off the table; if you waive it, supply a `get_user` hook
or `ALLOW_ANONYMOUS` whenever you want the store to persist.

## Classifying a crash

The capability records each tool call's status automatically — `started` before
it runs, then `completed` or `failed` — so no tool changes are needed to answer
"did it finish?". A call still `started` (no terminal record) after a restart is
the `unknown_after_crash` signal: its external side effect may or may not have
landed. The harness helper `list_unresolved_tool_effects(run_id=…)` surfaces
exactly those rows.

To make replay decisions sharper, a tool that writes external state can enrich
its ledger row with an `idempotency_key` / `effect_summary` via the harness
`annotate_tool_effect(store, ctx, …)` helper (given a handle to the store), so an
orchestrator inspecting the unresolved rows can tell whether re-running is safe.

## Bring your own store

`DefaultStepStore` is the batteries-included Django backend. Any
`request -> StepStore` callable works — implement the harness `StepStore`
protocol (ten async methods) over whatever backend you like and pass a factory:

```python
agent = AGUIServer(registry, step_store=lambda request: MyStepStore(request))
```

## Resume and fork

Configuring a `step_store` also mounts three owner-scoped endpoints:

- `GET runs/` — what may be resumed
- `POST resume/<run_id>/`
- `POST fork/<run_id>/`

Both **seed a new run from a prior run's last continuable snapshot**. The server
loads that run's snapshot — owner-scoped, so a `run_id` belonging to another user
is a clean `404`, never a leak — injects it as the new run's message history, and
streams the continuation. The client posts a normal `RunAgentInput` carrying
**only the new turn** and a **fresh `run_id`**; the prior turns come from the
snapshot:

```js
// Continue run "abc" with a new message, as a new run "def".
await fetch("/agent/resume/abc/", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    threadId: "t1", runId: "def", state: {},
    messages: [{ id: "u9", role: "user", content: "and now sort them" }],
    tools: [], context: [], forwardedProps: {},
  }),
});
```

The new run records its own events and snapshots under its `run_id`, with
`parent_run_id` set to the source — so the parent's ledger is **never mutated** (a
fork branches; a resume continues). `resume` and `fork` are two names for the one
mechanism — the harness's `continue_run` and `fork_run` are data-identical — so
pick the verb that matches your intent, and target a new `threadId` when you want
the branch to live in its own conversation.

!!! warning
    Send a **fresh `run_id`** in the resumed request: reusing the source run's id
    collides with the tool-effect ledger's `(run_id, tool_call_id)` key. And send
    only the **new** turn — the server supplies the prior history from the
    snapshot, so re-sending it duplicates it.

## Discovering what can be resumed

`resume` and `fork` address a run **by id**, so on their own a client can only
continue a run whose id it still holds — which rules out resuming after a page
reload or from another device, most of what durable persistence buys you.
`GET runs/` is the index:

```json
{
  "runs": [
    {
      "run_id": "abc",
      "thread_id": "t1",
      "parent_run_id": null,
      "started_at": "2026-07-27T12:00:00+00:00",
      "continuable": true
    }
  ]
}
```

**`continuable` is the field that matters.** It reports whether the run has a
saved snapshot to seed from, answered by making the same `latest_snapshot` call
`resume` itself makes — not inferred from event counts. A run that never reached
a provider-valid boundary has no snapshot, so resuming it would start from
nothing: offer the action only where this is `true`, and treat the other rows as
informational (a crashed run worth showing, not worth resuming).

`parent_run_id` exposes fork lineage, so a UI can show that a run branched from
another rather than listing near-identical transcripts side by side.

Rows are owner-scoped by the store, and nothing on the wire names an owner —
another user's runs are simply absent rather than a `403` that would confirm the
id exists.
