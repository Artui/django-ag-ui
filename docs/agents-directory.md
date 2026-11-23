# One home for several agents

A host that mounts more than one [`AGUIServer`][django_ag_ui.AGUIServer] (a chat page's
agent, the admin sidebar's, an MCP-backed one) can give a client one place to find them
all: [`AgentsDirectory`][django_ag_ui.AgentsDirectory] is a view built from those servers
and mounted once. It serves two routes:

- `GET agents/` lists each mount the caller may use, with its identity and URLs.
- `GET inbox/` merges each mount's own thread rows, newest first, with the same ETag
  polling and SSE as a mount's `threads/`.

```python
from django.urls import path

from django_ag_ui import AgentsDirectory

home = AgentsDirectory([spa_agent, admin_agent, mcp_agent])

urlpatterns = [
    path("agent/", spa_agent.urls),
    path("admin-agent/", admin_agent.urls),
    path("mcp-agent/", mcp_agent.urls),
    path("agents-home/", home.urls),
]
```

Every server needs its own `id=` and its own `namespace=`, and is mounted at the top of the
urlconf so `reverse("<namespace>:endpoint")` resolves; the constructor raises
`ImproperlyConfigured` for an empty list, a server without `id=`, a repeated `id` or a
repeated namespace. The directory's own namespace defaults to `ag_ui_agents`
(`reverse("ag_ui_agents:inbox")`).

## Who sees which mount

There is no policy of the directory's own. Each mount's `require_authenticated`,
`get_user` and `authorize` decide, exactly as they do on that mount's routes, and each runs
on **its own copy of the request**. That matters when the mounts authenticate differently:
a `get_user` assigns `request.user`, so a token mount checked first would otherwise make a
session mount list under the token's principal. With a copy per mount, each lists its own
principal's threads whatever the order.

A mount that denies the caller is left out of both routes, as if it were not there. When no
mount admits the caller, the answer is `401`, or `403` when every denial was `403`.

A mount's store can still refuse a caller its policy admits: a mount with
`require_authenticated=False` over an owner-scoped store lists an anonymous caller in
`agents/` but counts as a `403` denial in `inbox/`, just as its own `threads/` answers
`403`.

## `agents/`

```json
{"agents": [{"id": "board", "name": "Board", "description": "The board agent",
  "endpoint": "/agent/", "tools_url": "/agent/tools/", "threads_url": "/agent/threads/",
  "runs_url": null, "skills_url": "/agent/skills/", "skill_editor_url": null,
  "routines_url": null, "attachments_url": null, "transcribe_url": null,
  "capabilities": {"identity": {"name": "Board", "description": "The board agent"},
    "transport": {"streaming": true, "resumable": true},
    "tools": {"supported": true, "clientProvided": true}}}]}
```

One entry per admitted mount, in the order the servers were given. The URL keys are named
as django-admin-agent's sidebar context names them, each `null` for a route that mount does
not serve; which routes it serves is [`AGUIServer.routes()`][django_ag_ui.AGUIServer.routes].
Per-thread routes (`threads/<id>/events/`, `cancel/`, `read/`) are derived from
`threads_url`, as the web component does. The answer carries an ETag and answers a matching
`If-None-Match` with `304`; it does not stream, since the set changes only with a deploy or
a permission.

### Capabilities

Each entry's `capabilities` is what that mount declares of the AG-UI protocol's
[`AgentCapabilities`](https://github.com/ag-ui-protocol/ag-ui/blob/37b4da90/docs/spec/1.0/basic/capabilities.mdx), so a host can adapt
before a run: show an approval affordance, hide a reasoning panel, offer an upload button.
The protocol has no route of its own for it, so the directory carries it. It is
[`AGUIServer.agent_capabilities()`][django_ag_ui.AGUIServer.agent_capabilities], the SDK's
own `ag_ui.core.AgentCapabilities` model, dumped under its camelCase wire keys with unset
fields left out.

| Key | Declared | From |
| --- | --- | --- |
| `transport.streaming` | `true` | always: the endpoint answers in SSE |
| `transport.resumable` | `true` / `false` | a `run_store`: `threads/<id>/events/?after=` replays a run's log from a sequence number |
| `tools` | `supported`, `clientProvided` | always: a browser run's posted tools reach the agent |
| `humanInTheLoop` | `supported`, `approvals`, `interrupts` | `TOOL_GUARD` enabled and no `agent_factory=` (a factory builds the agent the guard is composed into) |
| `reasoning` | `{"supported": false}` | `FORWARD_REASONING` off, which drops every reasoning event |
| `multimodal.input.file` | `true` | an attachment store, which mounts `attachments/` |
| `multiAgent.supported` | `true` | `inbound_queue=` (its threads run as child sessions) or `child_threads=` (its threads have them) |
| `execution.maxExecutionTime` | milliseconds | `RUN_TIMEOUT_SECONDS` |
| `identity` | `name`, `description` | `name=`, `description=` |

What a mount cannot know is left out, and the protocol reads an omitted field as "not
declared", never as "unsupported". So a mount with the guard off declares nothing about
approvals (a capability passed in `capabilities=` may still ask for one), one forwarding
reasoning declares nothing about it (whether any arrives is the model's), and
`multiAgent.subagents` is never listed. The declarations are informative: the event stream
stays authoritative, and a client still handles an interrupt or a reasoning event it was
not told to expect.

## `inbox/`

`{"threads": [...]}`, the body shape of a mount's `threads/`, so a client that reads one
reads the other. It holds every admitted mount's rows, exactly as that mount's `threads/`
would list them (status, interrupts, parent and routine included), each with `agent_id`
whether or not the mount has a run store. Rows are ordered newest first by `updated_at`,
rows without one last, ties in server order.

`?limit=` caps each mount's share, clamped to that mount's `thread_list_limit`; nothing is
cut across mounts, so the inbox never hides a waiting thread its own mount would show.

Freshness is the same code as `threads/`
([Thread status](detached-runs.md#thread-status)): a strong ETag and `304` for a poller,
and under ASGI, with `Accept: text/event-stream`, a stream whose frames carry the polled
body as `data` and its ETag as `id`, `Last-Event-ID` skipping an unchanged first frame,
heartbeats from the directory's `config=` (`heartbeat_seconds`), and an end after five
minutes so the client reconnects and is checked again. Under WSGI the same request answers
as a poll.

The cost to plan for: one stream tick lists each admitted mount's threads and reads its
run store, every two seconds, per open stream.

## The seams it reads

The directory uses only public members of `AGUIServer`, which a host may call too:

- `namespace`: the URL namespace the mount reverses in.
- `routes()`: the names of the routes the mount serves.
- `await admits(request)`: the deny status the mount's policy answers, or `None`.
- `await thread_rows(request, limit=None)`: the mount's `threads/` rows with `agent_id`, or
  the deny status.
