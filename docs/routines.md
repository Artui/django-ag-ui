# Routines

A **routine** is a stored prompt for one agent, a schedule and the user it runs as:
"every weekday at 8, run the triage prompt and tell me what is overdue". The routine
itself is django-spec-agents' (`django_spec_agents.routines`): its abstract model, its
policy, the sweep that fires it and the spec a firing runs. This package serves the
**editor** for the routines of one agent mount, and announces a routine saved during a
run so an open routines panel refreshes.

Routines need the `[specs]` extra (`pip install "django-ag-ui[specs]"`): the editor
speaks the kernel's `NotPermitted`, so a mount without the extra refuses `routine_model=`
with an `ImportError` naming it.

Hand `AGUIServer` the model and an `id`, which names the mount every routine it serves
runs on:

```python
from django_ag_ui import AGUIServer

agent = AGUIServer(registry, id="helper", routine_model=Routine, run_store=...)
```

`routine_model` takes an `AbstractRoutine` subclass whose `routine_policy()` returns a
`RoutinePolicy` (`visible`, `can_write`, `can_run`, `new_routine`); a model without one,
or a mount without `id=`, is refused at construction. Firings themselves are
django-spec-agents': the project schedules `run_due_routines`, which enqueues the firing
spec on the model's `routine_sweep()` queue, and each firing runs headless into a fresh
thread owned by the routine's principal, through the mount's
[headless runner](detached-runs.md).

## The editor endpoints

| Route | Name | Methods |
| --- | --- | --- |
| `routines/` | `routines` | `GET` the list, `POST` a new routine (`201`) |
| `routines/<id>/` | `routine` | `PUT` replace it, `PATCH {"enabled": bool}` pause or resume |
| `routines/<id>/run/` | `routine_run` | `POST` fire it now (`202 {"runId", "threadId"}`) |
| `routines/<id>/runs/` | `routine_runs` | `GET` its firings, newest first |

**Which routines it serves.** Those whose principal is the caller, whose `agent_id` is
the mount's `id`, and that the policy's `can_write` admits. Any other id is a `404`,
whichever of the three left it out: a teammate's routine the policy lets the caller see
is not the caller's to edit, and one on another mount is that mount's. A create always
runs as the caller on this mount's agent; an `agentId` in the body is ignored.

**A routine on the wire** is `{"id", "name", "agentId", "prompt", "schedule",
"timezone", "enabled", "pausedReason", "nextFireAt", "preapprovedTools", "lastRunId",
"lastThreadId"}`, where `preapprovedTools` is `[{"name", "destructive"}]`, each flag
as pinned when the tool was granted. A write sends `{"name", "prompt", "schedule",
"timezone", "preapprovedTools", "enabled", "confirmDestructive"}`; `schedule` is the
closed vocabulary `{"kind": "weekly", "weekdays": [0-6], "at": "HH:MM"}` or
`{"kind": "interval", "every_hours": N}`, and `preapprovedTools` is a list of names.

The list's `GET` also returns `grantable`, `[{"name", "destructive"}]` for each server
tool a routine on this mount may be granted, so an editor knows which grants need the
confirmation below before it submits.

### A destructive grant is confirmed by name

A pre-approved tool runs in a firing without parking for approval, and a destructive
one does so unasked. So a `POST` or `PUT` whose grant holds a destructive tool is
refused with a `409` unless `confirmDestructive` names every destructive tool in it:

```json
{"error": "Granting drop_table lets each firing run a destructive tool unasked: ...",
 "destructive": ["drop_table"]}
```

Nothing is saved. Every write that sets the grant names them again, as the agent's own
`create_routine` and `update_routine` cards name every destructive grant on every edit:
this is the editor's form of that card. A tool keeps the flag it was pinned with, so one
granted while harmless and declared destructive later is still recorded harmless and its
call parks. `PATCH {"enabled": true}` resumes a routine with the grant it already holds,
so it needs no confirmation.

### Run now and recent runs

`run/` enqueues the firing spec on the model's `routine_sweep()` queue, as the sweep
does, into a fresh thread, and records it as the routine's last firing. It is a `409`
when the model has no sweep, when the last firing is still pending or its thread still
waits on a person (`thread_waits`), or when the policy's `can_run` refuses; a refusal the
queue raises is a `403`. Two clicks at once cannot fire it twice: the checks run again
on the row, locked for the write. `run/` does not look at `enabled`: **run now fires a
paused routine once; it stays paused.** `runs/` lists `{"runId", "threadId", "state"}`, `state` being
the queue's `pending`, `lapsed` or `finished`, for each firing, newest first. The routine
records only its last firing, so that one leads with its run id and state, even before
its thread exists. The earlier ones come from the conversation store: each thread among
the caller's newest `thread_list_limit` whose conversation names the routine, with
`runId` and `state` `null`, since the queue's id is not kept past the next firing.

### Errors

A broken rule (a name, prompt or timezone, a schedule outside the vocabulary, a tool the
mount does not offer, the routine cap) is a `400`; a write `can_write` refuses a `403`;
each `{"error": "..."}`. A write must be `application/json` (else `415`). Authentication
follows the mount's policy, and like the skill editor the routines editor is
CSRF-checked unless `csrf_exempt=True` is passed explicitly.

### What can be granted

The editor has no run, so it lists the tools the mount's own sources declare: the
`@tool` registry, a bridged `mcp_server`, and `service_specs`. It pins each through the
same `ToolGuard` the run uses, so `TOOL_GUARD`'s `EXEMPT` and `REQUIRE_APPROVAL` decide
here too. A tool attached through `toolsets=`, `capabilities=` or an `agent_factory`
cannot be granted from the editor; the agent's own `create_routine` tool, which sees the
run's tools, can grant it. A routine already holding such a grant keeps it through the
editor: a resume or a replace naming it again keeps its pinned flag, and a destructive one
is still named in `confirmDestructive`.

## A firing's thread names its routine

On a mount with `routine_model=`, each row of `GET <mount>threads/` carries `routine`:
`{"id", "name"}` when a firing of one of the caller's routines on this mount started the
thread, else `null`. A firing saves its routine's id on the thread's conversation
(django-spec-agents' `ConversationMeta.routine_id`), and later runs of the thread keep it,
so every firing's thread is named, not only the newest. The names come from one query on
the routine model per page; a routine since deleted, or another user's, names nothing. A
mount without routines sends `null` on every row.

## Refreshing an open panel

A run that saves a routine, through the agent's routine tools or any tool of yours,
sends the [`ag_ui.invalidate`](invalidation.md) CUSTOM event with the keys `routines`
and `routines/<id>`, once the write commits. The web component re-dispatches it as the
DOM `ag-ui-invalidate` event; a routines panel listening for the `routines` key refetches.
A write through the editor's own endpoints is not a run and sends nothing: the panel
that made it already has the answer.

## Reference

[`RoutineEditorView`][django_ag_ui.routines.routine_editor_view.RoutineEditorView] is on the
[API page](api.md).
