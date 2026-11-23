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
    "transport": {"streaming": true, "resumable": false},
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
| `transport.resumable` | `true` / `false` | a `run_store` and `RUN_TIMEOUT_SECONDS`: a run survives a dropped stream only when it detaches, which needs both, and `threads/<id>/events/?after=` then replays its log from a sequence number (an authenticated run's; an anonymous run is not logged) |
| `tools` | `supported`, `clientProvided` | always: a browser run's posted tools reach the agent |
| `humanInTheLoop` | `supported`, `approvals`, `interrupts` | `TOOL_GUARD` enabled and no `agent_factory=` (a factory builds the agent the guard is composed into) |
| `reasoning` | `{"supported": false}` | `FORWARD_REASONING` off, which drops every reasoning event |
| `multimodal.input.file` | `true` | an attachment store, which mounts `attachments/`, and an empty `ATTACHMENT_ALLOWED_TYPES`: `file` means arbitrary uploads, and a listed type makes `attachments/` refuse the rest with a `415` |
| `multiAgent.supported` | `true` | `inbound_queue=` (its threads run as child sessions); `child_threads=` only lists children, so declares nothing |
| `execution.maxExecutionTime` | milliseconds | `RUN_TIMEOUT_SECONDS` |
| `identity` | `name`, `description` | `name=`, `description=` |

`transport.resumable` cannot see the deployment: under WSGI a response cannot outlive
its request, so a mount with both still declares it while a dropped stream ends the run.

What a mount cannot know is left out, and the protocol reads an omitted field as "not
declared", never as "unsupported". So a mount with the guard off declares nothing about
approvals (a capability passed in `capabilities=` may still ask for one), one forwarding
reasoning declares nothing about it (whether any arrives is the model's), and the mount never
lists `multiAgent.subagents`. A host that knows more says so by
[overriding the method](#adding-what-the-mount-cannot-see). The declarations are
informative: the event stream stays authoritative, and a client still handles an interrupt
or a reasoning event it was not told to expect.

#### Adding what the mount cannot see

Some of a mount's capabilities come from what it is handed rather than from its
configuration: [sub-agents](subagents.md) passed through `capabilities=`, or a tool guard a
host composes by hand. The mount declares none of them. A host that knows it has them
subclasses `AGUIServer` and overrides `agent_capabilities()`, which the directory calls on
the mount for every request. Start from `super()`, so the configuration's declarations
stay, and add to them:

```python
from ag_ui.core import AgentCapabilities, MultiAgentCapabilities, SubagentInfo

from django_ag_ui import AGUIServer


class DelegatingServer(AGUIServer):
    def agent_capabilities(self) -> AgentCapabilities:
        declared = super().agent_capabilities()
        multi_agent = declared.multi_agent or MultiAgentCapabilities()
        researcher = SubagentInfo(name="researcher", description="Finds sources")
        update = {"supported": True, "subagents": [researcher]}
        return declared.model_copy(update={"multi_agent": multi_agent.model_copy(update=update)})
```

Mounted as `DelegatingServer(...)` in place of `AGUIServer(...)`, its entry then carries
`"multiAgent": {"supported": true, "subagents": [{"name": "researcher", "description":
"Finds sources"}]}` beside what the configuration declared. A hand-composed guard is the
same move on `human_in_the_loop` with `HumanInTheLoopCapabilities`.

Three things the SDK will not catch for you:

- **Pass the SDK's models, never dicts.** `model_copy(update=...)` does not validate, so a
  dict value is stored as given and the entry fails to serialise: `agents/` answers `500`.
- **An update replaces a sub-model whole.** Copy the declared one (`declared.multi_agent or
  MultiAgentCapabilities()`) and update that, or a field the configuration set is lost.
- **The field is `subagents`.** The SDK's models accept unknown fields, so `sub_agents=`
  constructs without an error and is served under the wrong key.

What the override declares is informative too: the stream stays authoritative, and the
override is the host's word, not something the mount checks.

## `inbox/`

`{"threads": [...]}`, the body shape of a mount's `threads/`, so a client that reads one
reads the other. It holds every admitted mount's rows, exactly as that mount's `threads/`
would list them (status, interrupts, `run_id` where that mount serves `resume/<run_id>/`,
parent and routine included), each with `agent_id` whether or not the mount has a run store. Rows are ordered newest first by `updated_at`,
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
