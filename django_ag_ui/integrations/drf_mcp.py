"""In-process bridge from a ``drf-mcp-server`` registry to a Pydantic-AI toolset.

Requires the ``django-ag-ui[drf-mcp]`` extra. Imported lazily by the view only
when ``DJANGO_AG_UI["DRF_MCP_SERVER"]`` is set, so the dependency on
``rest_framework_mcp`` stays optional.

The agent acts as the **logged-in AG-UI user**: each call synthesises an
``MCPCallContext`` carrying ``request.user``, so drf-mcp's own validation and
permission checks apply exactly as they would over HTTP — just without the
network hop.

Tool schemas are sourced from drf-mcp's own ``tools/list`` handler rather than
re-derived locally, so the in-process bridge advertises the *same* merged
``inputSchema`` the HTTP transport would — including a selector tool's
filter / ordering / pagination arguments and the ``additionalProperties`` policy,
not just the input serializer's fields.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError

# Pinned protocol version for synthesised in-process calls (no wire negotiation).
_PROTOCOL_VERSION = "2025-06-18"


class DrfMcpToolset(ExternalToolset[Any]):
    """Exposes a drf-mcp ``MCPServer``'s tools as a Pydantic-AI toolset.

    Built per request so the acting user is the current AG-UI user. Tool
    schemas and execution both route through drf-mcp's own handlers, so the
    advertised parameters, serializer validation, and permissions match the
    HTTP transport exactly.
    """

    def __init__(self, server: Any, request: HttpRequest) -> None:
        self._server = server
        self._request = request
        self._loaded = False
        # Tool defs are loaded lazily in ``get_tools`` (async): drf-mcp's
        # ``tools/list`` may touch the DB (per-user listing permissions), which
        # is unsafe to run synchronously inside the async view's ``__init__``.
        super().__init__([], id="drf-mcp")

    def _context(self) -> MCPCallContext:
        """Build the per-call context carrying the acting user + registries."""
        return MCPCallContext(
            http_request=self._request,
            token=TokenInfo(user=self._request.user),
            tools=self._server.tools,
            resources=self._server.resources,
            prompts=self._server.prompts,
            protocol_version=_PROTOCOL_VERSION,
        )

    async def get_tools(self, ctx: Any) -> Any:
        """Load tool defs from drf-mcp's ``tools/list`` once, then defer to base.

        Loading runs in a thread (``sync_to_async``) because the sync
        ``handle_tools_list`` may evaluate per-user listing permissions against
        the DB, which Django forbids on the async event loop.
        """
        if not self._loaded:
            self.tool_defs = await sync_to_async(self._load_tool_defs)()
            self._loaded = True
        tools = await super().get_tools(ctx)
        # ExternalToolset stamps every tool ``kind="external"``, which Pydantic-AI
        # *defers*: it yields the call to the client and ends the run, never
        # invoking our ``call_tool``. But this bridge executes drf-mcp tools
        # in-process (``call_tool`` → ``handle_tools_call_async``), so re-stamp
        # them ``kind="function"`` to route the run loop into ``call_tool`` — which
        # then emits a ``TOOL_CALL_RESULT`` and lets the model continue. Without
        # this the tool is silently handed off and never runs.
        return {
            name: replace(tool, tool_def=replace(tool.tool_def, kind="function"))
            for name, tool in tools.items()
        }

    def _load_tool_defs(self) -> list[ToolDefinition]:
        """Page through drf-mcp's ``tools/list``, mapping each tool to a def.

        Uses the authoritative merged ``inputSchema`` (serializer fields + a
        selector's filter / ordering / pagination args + the
        ``additionalProperties`` policy) verbatim, so nothing the model could
        send over HTTP is silently dropped in-process.
        """
        defs: list[ToolDefinition] = []
        context = self._context()
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor is not None else None
            payload = handle_tools_list(params, context)
            if isinstance(payload, JsonRpcError):
                raise RuntimeError(f"drf-mcp tools/list failed: {payload.message}")
            for tool in payload["tools"]:
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
        result = await handle_tools_call_async(
            params={"name": name, "arguments": tool_args},
            context=self._context(),
        )
        if isinstance(result, JsonRpcError):
            raise RuntimeError(f"drf-mcp tool {name!r} failed: {result.message}")
        return result.get("structuredContent", result.get("content"))


__all__ = ["DrfMcpToolset"]
