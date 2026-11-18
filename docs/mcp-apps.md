# MCP Apps views

An [MCP App](https://modelcontextprotocol.io/extensions/apps/overview) is an interactive
HTML view an MCP server links to one of its tools. When the agent calls such a tool, the
run streams an `mcp-apps` activity naming the view's `resourceUri` and, when the toolset
was given one, its `serverId`. The view runs in the browser and cannot reach the MCP
server, so the host relays two kinds of request for it: loading the view's HTML
(`resources/read`), and the view's own tool calls (`tools/call`).

`AGUIServer` mounts that relay as `mcp-apps/`, beside `tools/`:

```python
urlpatterns = [path("agent/", server.urls)]  # POST /agent/mcp-apps/
```

It reverses as `reverse("ag_ui:mcp_apps")`.

## The request

```http
POST /agent/mcp-apps/
Content-Type: application/json

{"serverId": "mcp", "method": "tools/call", "params": {"name": "bump", "arguments": {"n": 1}}}
```

```json
{"serverId": "mcp", "method": "resources/read", "params": {"uri": "ui://counter/view"}}
```

Only `tools/call` and `resources/read` are relayed. The route acts as `request.user`, and
it never reads or writes the conversation: a view's calls are not turns, and the model
does not see them.

## The answer

A relayed call answers `200` with `{"result": ...}`, the MCP result in wire form (a
`CallToolResult` or a `ReadResourceResult`), to be handed to the view as it is. A tool
result with `isError: true` is still a `result`.

Anything the route cannot relay answers `{"error": {"code": ..., "message": ...}}` with
a JSON-RPC code:

| Case | HTTP | `code` |
| --- | --- | --- |
| The body is not JSON | 200 | `-32700` |
| The body, `serverId`, `params`, `params.name` or `params.uri` has the wrong type | 200 | `-32602` |
| `method` is neither `tools/call` nor `resources/read` | 200 | `-32601` |
| No toolset, or more than one, has `id == serverId` | 200 | `-32602` |
| The toolset has no app method for `method` | 200 | `-32601` |
| The toolset refuses the call (a model-only tool, an unknown URI, a permission) | 200 | the toolset's own code |
| The toolset fails without a code (a network error) | 200 | `-32603` |
| The caller is not authenticated | 401 | `-32001` |
| The `authorize=` predicate refuses the caller | 403 | `-32006` |

A `GET` answers `405`.

## Authorisation

The route takes the endpoint's own authorisation and CSRF policy, the one `tools/` takes:
`require_authenticated`, `get_user`, `authorize` and `csrf_exempt` on `AGUIServer`. The
run endpoint's `throttle=` is not applied, as it is not to `tools/`: one limiter is one
counter, and a view's calls would spend the run budget.

Every call that passes authorisation is recorded through the endpoint's `audit_logger=`,
refused ones included: `tool_name` is `mcp_apps.tools/call` or `mcp_apps.resources/read`
(`mcp_apps` for a request naming neither), `arguments_repr` is the JSON of `serverId`
and `params`, and `error` holds the code and message, or the failure's own text when it
had no code.

## Which toolsets answer

The route looks for the toolset whose `id` equals `serverId` among:

- the agent's toolsets (`toolsets=`, or those an `agent_factory=` agent carries);
- the in-process bridge `mcp_server=` builds for the request, whose id is always `"mcp"`.

Wrapper toolsets (`prefixed`, `filtered`, `approval_required`, a combined toolset) are
unwrapped to the toolset that does the work, and the view's call goes straight to it.
So a wrapper's filter or approval does not apply to a view's call, as pydantic-ai's own
`call_tool_for_app` skips `process_tool_call`. A dynamic toolset built by a function has
nothing to find until a run builds it, so the route cannot reach it.

The found toolset is called duck-typed, through its `call_tool_for_app(name, arguments)`
and `read_resource_for_app(uri)`: pydantic-ai's `MCPToolset` and django-spec-agents'
in-process bridge both have them. Visibility is the toolset's to enforce: each refuses a
tool whose `_meta.ui.visibility` leaves out `"app"`.

### The in-process bridge

With `mcp_server=`, the bridge is built per request, so a view's call runs as the
request's user, under the spec's own permissions. A tool the `@tool` registry shadows is
left out of the bridge, for a view as for a run.

### A remote `MCPToolset`

A pydantic-ai `MCPToolset` (from `pydantic_ai.mcp`, with `pydantic-ai-slim[mcp]`)
pointing at a remote server needs an explicit `id=`:

```python
server = AGUIServer(registry, toolsets=[MCPToolset("https://mcp.example.com/mcp", id="billing")])
```

Without one the toolset takes its id from the URL's hostname, but the `mcp-apps` activity
carries a `serverId` only for an id that was given, so the browser has nothing to route
the view's calls by.

A remote toolset is built once and shared by every run of the agent, so it connects as
its own configured identity (its headers or token), not as `request.user`. Any user the
route admits reaches every tool that server shows to apps, and every resource it serves:
gate the route with `authorize=`, or give the server an identity that may only do what
every admitted user may.
