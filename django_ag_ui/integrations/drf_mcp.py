"""In-process bridge from a ``drf-mcp-server`` registry to a Pydantic-AI toolset.

Requires the ``django-ag-ui[drf-mcp]`` extra. Imported lazily by the view only
when ``DJANGO_AG_UI["DRF_MCP_SERVER"]`` is set, so the dependency on
``rest_framework_mcp`` stays optional.

The agent acts as the **logged-in AG-UI user**: every call hands ``request`` and
``request.user`` to drf-mcp's public in-process surface
(:meth:`~rest_framework_mcp.MCPServer.list_tools` /
:meth:`~rest_framework_mcp.MCPServer.acall_tool`, drf-mcp 0.9+), so drf-mcp's own
validation and permission checks apply exactly as they would over HTTP — just
without the network hop, and without reaching into handler internals.

Tool schemas are sourced from drf-mcp's own ``tools/list`` (via ``list_tools``)
rather than re-derived locally, so the in-process bridge advertises the *same*
merged ``inputSchema`` the HTTP transport would — including a selector tool's
filter / ordering / pagination arguments and the ``additionalProperties`` policy,
not just the input serializer's fields.

Error semantics follow the MCP protocol-vs-tool boundary:

- malformed *arguments shape* (JSON-RPC ``-32602``) and tool-level
  ``validation_error`` results → :class:`pydantic_ai.ModelRetry`, so the
  model retries with the field errors instead of the run dying;
- other tool-level failures (``service_error`` / ``not_found`` ``isError``
  results) → returned as the tool's content, model-readable;
- genuine protocol faults (unknown tool, auth, rate limits) → a hard
  ``RuntimeError`` that aborts the run.
"""

from __future__ import annotations

import json
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from pydantic_ai import ModelRetry
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool
from pydantic_core import SchemaValidator, core_schema
from rest_framework_mcp import JsonRpcError, JsonRpcErrorCode

# Tool args pass through unvalidated: the parameter schemas advertised to the
# model come verbatim from drf-mcp's ``tools/list`` (advisory, not a Pydantic
# model), and the real validation is drf-mcp's own serializer at call time — so
# the per-tool validator is a no-op, exactly the split the HTTP transport has.
_TOOL_ARGS_VALIDATOR = SchemaValidator(schema=core_schema.any_schema())


class DRFMCPToolset(AbstractToolset[Any]):
    """Exposes a drf-mcp ``MCPServer``'s tools as a Pydantic-AI toolset.

    Built per request so the acting user is the current AG-UI user. Tool
    schemas and execution both route through drf-mcp's public in-process
    surface (``MCPServer.list_tools`` / ``acall_tool``), so the advertised
    parameters, serializer validation, and permissions match the HTTP transport
    exactly. Tool definitions carry the default ``kind="function"`` — the
    in-process kind the run loop routes into ``call_tool``, which then emits a
    ``TOOL_CALL_RESULT`` and lets the model continue (an ``external`` tool
    would instead be deferred to the client and never run).

    ``exclude_names`` carries the ``@tool`` registry's names: on a collision
    the registry tool wins (the same rule ``build_tool_catalog`` applies) and
    the drf-mcp twin is skipped — otherwise pydantic-ai raises ``UserError``
    for the duplicate name at run time.

    ``max_retries`` is each tool's retry budget: how many times a
    :class:`pydantic_ai.ModelRetry` (malformed arguments, a service-raised
    validation error) is fed back to the model before the run aborts. Defaults
    to ``1``, matching pydantic-ai's own function-tool default.
    """

    def __init__(
        self,
        server: Any,
        request: HttpRequest,
        *,
        exclude_names: frozenset[str] = frozenset(),
        max_retries: int = 1,
    ) -> None:
        self._server = server
        self._request = request
        self._exclude_names = exclude_names
        self._max_retries = max_retries
        # Tool defs are loaded lazily in ``get_tools`` (async): drf-mcp's
        # ``tools/list`` may touch the DB (per-user listing permissions), which
        # is unsafe to run synchronously inside the async view's ``__init__``.
        self._tool_defs: list[ToolDefinition] | None = None

    @property
    def id(self) -> str | None:
        return "drf-mcp"

    async def get_tools(self, ctx: Any) -> dict[str, ToolsetTool[Any]]:
        """Load tool defs from drf-mcp's ``tools/list`` once, then wrap them.

        Loading runs in a thread (``sync_to_async``) because the sync
        ``list_tools`` may evaluate per-user listing permissions against the DB,
        which Django forbids on the async event loop.
        """
        if self._tool_defs is None:
            self._tool_defs = await sync_to_async(self._load_tool_defs)()
        return {
            tool_def.name: ToolsetTool(
                toolset=self,
                tool_def=tool_def,
                max_retries=self._max_retries,
                args_validator=_TOOL_ARGS_VALIDATOR,
            )
            for tool_def in self._tool_defs
        }

    def _load_tool_defs(self) -> list[ToolDefinition]:
        """Page through drf-mcp's ``tools/list``, mapping each tool to a def.

        Uses the authoritative merged ``inputSchema`` (serializer fields + a
        selector's filter / ordering / pagination args + the
        ``additionalProperties`` policy) verbatim, so nothing the model could
        send over HTTP is silently dropped in-process. Names colliding with
        the ``@tool`` registry are skipped (registry wins).
        """
        defs: list[ToolDefinition] = []
        cursor: str | None = None
        while True:
            payload = self._server.list_tools(
                cursor, user=self._request.user, request=self._request
            )
            if isinstance(payload, JsonRpcError):
                raise RuntimeError(f"drf-mcp tools/list failed: {payload.message}")
            for tool in payload["tools"]:
                if tool["name"] in self._exclude_names:
                    continue
                defs.append(
                    ToolDefinition(
                        name=tool["name"],
                        description=tool.get("description"),
                        parameters_json_schema=tool["inputSchema"],
                    )
                )
            cursor = payload.get("nextCursor")
            if cursor is None:
                break
        return defs

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: Any,
    ) -> Any:
        result = await self._server.acall_tool(
            name, tool_args, user=self._request.user, request=self._request
        )
        if isinstance(result, JsonRpcError):
            if result.code == JsonRpcErrorCode.INVALID_PARAMS:
                # Malformed arguments — the single most common failure mode.
                # ``ModelRetry`` feeds the field errors back so the model
                # corrects itself; anything else raised from a tool aborts
                # the whole run with a dead chat.
                raise ModelRetry(_retry_message(result.message, (result.data or {}).get("detail")))
            # Genuine protocol faults (unknown tool, auth, rate limit) are
            # unrecoverable mid-run.
            raise RuntimeError(f"drf-mcp tool {name!r} failed: {result.message}")
        if result.get("isError"):
            # drf-mcp returns business failures as ``isError`` tool results.
            # Service-raised validation still earns a retry; other tool-level
            # failures (business rules, missing rows) are returned as content
            # the model can read and act on.
            error = _parse_tool_error(result)
            if error.get("type") == "validation_error":
                # Single-line statements: Python 3.11's tracer attributes a
                # multi-line ``raise X(...)`` to the argument line, leaving
                # the ``raise`` line "uncovered" and tripping the 100% gate.
                message = error.get("message", "invalid arguments")
                raise ModelRetry(_retry_message(message, error.get("detail")))
            return {"error": error}
        return result.get("structuredContent", result.get("content"))


def _parse_tool_error(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the ``{"error": {...}}`` payload from an ``isError`` result.

    drf-mcp encodes it as JSON text in ``content[0]``; fall back to a generic
    shape if a future encoding changes (never raise while reporting an error).
    """
    content = result.get("content") or []
    text: Any = content[0].get("text", "") if content else ""
    try:
        error = json.loads(text)["error"]
    except (ValueError, KeyError, TypeError):
        return {"type": "unknown", "message": str(text) or "tool error"}
    return error if isinstance(error, dict) else {"type": "unknown", "message": str(error)}


def _retry_message(message: str, detail: Any) -> str:
    """Compose the ``ModelRetry`` text: human message + field-level detail."""
    if not detail:
        return message
    return f"{message}: {json.dumps(detail, default=str)}"


__all__ = ["DRFMCPToolset"]
