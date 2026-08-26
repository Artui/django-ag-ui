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

The `[harness]` extra pulls `pydantic-ai-harness`, and nothing else: the
[CodeMode](code-mode.md) sandbox lives behind its own `[code-mode]` extra, so a
project keeping a ledger does not install a code sandbox to do it. The core
install stays `django` + `pydantic-ai-slim` — the harness is lazy, only imported
when a `step_store` is configured.

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
[`allow_anonymous=`](configuration.md#allow_anonymous). The harness records carry
no owner; this store adds it, so `latest_snapshot(run_id=…)` for one user never
returns another user's snapshot even if the `run_id` is guessed — the `run_id`
is not a secret, the owner is the boundary.

An anonymous request with `allow_anonymous=False` has no durable identity, so the
store **degrades instead of crashing**: writes no-op and reads return empty (the
run still streams, it just isn't recorded — the capability's hooks fire mid-run,
so a hard refusal would abort it). The endpoint's `require_authenticated` default
already keeps that case off the table; if you waive it, supply a `get_user` hook
or build the store with `allow_anonymous=True` whenever you want it to persist.

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

!!! warning "Run-level resume, not stream resumability"
    These seed a **new run** from a saved snapshot. They do **not** reattach to
    an interrupted SSE stream — AG-UI has no such primitive, and neither does
    this package. If a client's connection drops mid-run, the events emitted
    while it was disconnected are gone; there is no `Last-Event-ID` replay and
    no way to rejoin the run that was in flight.

    What you get instead is a new run seeded from the last **provider-valid
    boundary**, with a fresh `run_id`. Anything the interrupted run did after
    that boundary is not replayed — which is why the tool-effect ledger exists:
    `list_unresolved_tool_effects(run_id=…)` is how you find out whether a side
    effect landed before deciding to continue.

    The practical consequence for a client: treat a dropped stream as a **lost
    run**, then offer resume as a deliberate action, rather than reconnecting
    and expecting the stream to carry on.

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

!!! warning "A branch may not land on top of an existing conversation"
    A saved conversation is stored as a whole row, so seeding a thread from
    another thread's run **replaces** what that thread held. `runs/` indexes
    every run the owner has across all their threads, while a client resumes the
    one it picked into whatever thread is open — which is how a user reading
    thread B could pick a run from thread A and lose B's turns entirely.

    So when a `conversation_store` is configured and the `threadId` you post
    already has a stored conversation that the source run does not belong to,
    the endpoint answers **`409`** and streams nothing:

    ```json
    {"error": "resuming that run would overwrite this thread",
     "run_id": "abc", "thread_id": "t2"}
    ```

    Continuing a run in its own thread is unaffected, and so is branching one
    into a `threadId` that holds nothing yet — the refusal is about overwriting,
    not about crossing threads. A client that offers cross-thread checkpoints
    should switch to the row's own `thread_id` (`runs/` reports it) or open a
    new thread before resuming. Endpoints with no conversation store have
    nothing to overwrite and are never refused.

!!! warning
    Send a **fresh `run_id`** in the resumed request, and send only the **new**
    turn — the server supplies the prior history from the snapshot, so re-sending
    it duplicates it. Duplicating history is the one of those two that goes
    through: reusing the source run's id does **not** corrupt its ledger, because
    the harness refuses it by name — *"run_id … is already in the store. Explicit
    `run_id` is single-shot"* — and the source's records are untouched. So there
    is nothing to defend against client-side beyond passing a new id.

    **That refusal arrives as a `RUN_ERROR` event, not an HTTP status**, and could
    not be otherwise: `RUN_STARTED` has already gone out, so the response is
    committed at `200` before the id is ever validated. This is true of *every*
    error a streaming endpoint raises after its first byte, which makes it a
    client-side rule rather than a detail of this endpoint: **read the event
    stream, not `response.ok`.** A client that checks only the status scores these
    runs as successes.

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
      "continuable": true,
      "preview": "Move standup to Friday at 11:00"
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

**`preview` is the field a person reads.** It is the run's first user message,
collapsed to one line and truncated, and it comes out of the snapshot the view
already loaded to answer `continuable` — so it costs no extra query, and it is
`null` exactly where that snapshot is missing. Without it a picker can only offer
a time and an opaque id, which is not a choice: two runs a minute apart both read
"just now", and the id is not something a person recognises. Rows arrive **newest
first**, which is the view's doing rather than the store's — a `StepStore` answers
oldest-first because the harness protocol says so.

`parent_run_id` exposes fork lineage, so a UI can show that a run branched from
another rather than listing near-identical transcripts side by side.

Rows are owner-scoped by the store, and nothing on the wire names an owner —
another user's runs are simply absent rather than a `403` that would confirm the
id exists.

**The response is capped at `RUN_LIST_LIMIT`** (default `50`, newest first),
because a row is not cheap: each one loads that run's snapshot and holds its
whole message list resident while the response is built. The cap is applied
before those loads, so the runs it drops cost nothing. There is no `?limit=` on
this route — the step-store protocol offers no offset, so a smaller page would
only be a client asking for less of a list it cannot page through. Raise or
disable it per endpoint with `build_ag_ui_config(run_list_limit=…)`.

## Scoping two endpoints

The ledger keys by `(owner_id, run_id)` and nothing else, so **two mounts handed
the same `step_store` share one user's runs**. A run recorded at
`/internal/agent` is listed by `/public/agent/runs/`, and since
`resume/<run_id>/` addresses a run by id, the same user can continue that
transcript under the public agent's model, tools and guard policy. Owner scoping
cannot catch it: it is the same user on both mounts.

[`ScopedStepStore`][django_ag_ui.ScopedStepStore] partitions the ledger by a
scope name, the way `ScopedConversationStore` partitions thread history:

```python
internal = AGUIServer(
    internal_registry,
    namespace="internal-agent",
    step_store=ScopedStepStore(DefaultStepStore, scope="internal"),
)
public = AGUIServer(
    public_registry,
    namespace="public-agent",
    step_store=ScopedStepStore(DefaultStepStore, scope="public"),
)
```

It wraps the **factory**, not a store, because that is what `step_store=` takes.
The partition is a run-id prefix, so it composes with any implementation and
needs no migration; a run belonging to another scope is simply not found, so a
probe cannot confirm the id exists either. Run ids on the wire are unchanged —
only the storage key carries the prefix.

**Opt in explicitly.** Adding a scope to a mount that has been running hides
that mount's earlier runs from `runs/` rather than migrating them, which is
exactly why wrapping does not happen by itself.
