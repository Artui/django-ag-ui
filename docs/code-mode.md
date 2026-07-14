# Batching tool calls with CodeMode

When the agent has many tools — especially a large drf-mcp bridge — each call is
its own model round-trip, and every tool's schema bloats the prompt.
[`pydantic-ai-harness`](https://github.com/pydantic/pydantic-ai-harness)'s
**`CodeMode`** capability collapses them into a single sandboxed `run_code` tool:
the model writes Python that calls the tools as functions (with loops,
`asyncio.gather`, intermediate variables) and gets the result in **one**
round-trip.

This is a composition of two things django-ag-ui already gives you — the
in-process [drf-mcp bridge](configuration.md#drf_mcp_server) and the
[`CAPABILITIES`](configuration.md#capabilities) seam — plus the optional
`[harness]` extra.

## Install

```bash
pip install "django-ag-ui[harness]"
```

The `[harness]` extra pulls `pydantic-ai-harness` and its sandbox
(`pydantic-monty`). The core install stays `django` + `pydantic-ai-slim` — the
harness is lazy, only imported by the capability you wire in.

## Wire it in

`CAPABILITIES` takes dotted paths to zero-argument callables that return a
capability. Add one that returns a `CodeMode`:

```python
# myproject/agent.py
from pydantic_ai_harness import CodeMode


def code_mode():
    return CodeMode()  # wraps every tool the agent has into `run_code`
```

```python
# settings.py
DJANGO_AG_UI = {
    "MODEL": "anthropic:claude-sonnet-4.6",
    "DRF_MCP_SERVER": "myproject.mcp.server",
    "CAPABILITIES": ("myproject.agent.code_mode",),
}
```

Now the agent exposes one `run_code` tool instead of the individual DRF tools;
the model batches its DRF work into a single sandboxed script.

### Wrapping only some tools

Pass a name list or a predicate to `tools=` to split the toolset — matching
tools go behind `run_code`, the rest stay as direct tool calls. This is useful
to keep, say, a destructive tool a [`ToolGuard`](configuration.md#tool_guard)
gates as a *direct* call (so its approval interrupt still fires) while batching
the read-only ones:

```python
def code_mode():
    # Batch everything except the approval-gated delete.
    return CodeMode(tools=lambda ctx, td: td.name != "projects.delete")
```

## Typed stubs — keep drf-mcp's output schema on

CodeMode renders each wrapped tool as a **typed** Python stub so the model knows
what each call returns. A tool with **no return schema** renders `-> Any` and
CodeMode emits a warning — the model loses that type information.

django-ag-ui's drf-mcp bridge carries drf-mcp's `outputSchema` onto the tool's
`return_schema`, so bridged tools render typed **as long as drf-mcp advertises
the output schema** — which it does by default
(`REST_FRAMEWORK_MCP["INCLUDE_OUTPUT_SCHEMA"] = True`). Keep it on (and give your
services an output serializer) for the best CodeMode experience; a service with
no output serializer advertises no schema and its stub falls back to `-> Any`.

!!! note
    `CodeMode` is one of several `pydantic-ai-harness` capabilities that drop
    into the same `CAPABILITIES` seam (compaction, step-persistence, subagents,
    …). They all ride the `[harness]` extra.
