"""In-process bridge from a ``drf-mcp-server`` registry to a Pydantic-AI toolset.

Requires the ``django-ag-ui[drf-mcp]`` extra. Imported lazily by the view only
when ``DJANGO_AG_UI["DRF_MCP_SERVER"]`` is set, so the dependency on
``rest_framework_mcp`` stays optional.

The agent acts as the **logged-in AG-UI user**: each call synthesises an
``MCPCallContext`` carrying ``request.user``, so drf-mcp's own validation and
permission checks apply exactly as they would over HTTP — just without the
network hop.
"""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets.external import ExternalToolset
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.schema.input_schema import build_input_schema

# Pinned protocol version for synthesised in-process calls (no wire negotiation).
_PROTOCOL_VERSION = "2025-06-18"


class DrfMcpToolset(ExternalToolset[Any]):
    """Exposes a drf-mcp ``MCPServer``'s tools as a Pydantic-AI toolset.

    Built per request so the acting user is the current AG-UI user. Tool
    schemas come straight from drf-mcp; execution routes through its async
    handler so serializer validation + permissions are honoured.
    """

    def __init__(self, server: Any, request: HttpRequest) -> None:
        self._server = server
        self._request = request
        tool_defs = [
            ToolDefinition(
                name=binding.name,
                description=binding.description,
                parameters_json_schema=build_input_schema(
                    getattr(binding.spec, "input_serializer", None),
                ),
            )
            for binding in server.tools.all()
        ]
        super().__init__(tool_defs, id="drf-mcp")

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: Any,
        tool: Any,
    ) -> Any:
        context = MCPCallContext(
            http_request=self._request,
            token=TokenInfo(user=self._request.user),
            tools=self._server.tools,
            resources=self._server.resources,
            prompts=self._server.prompts,
            protocol_version=_PROTOCOL_VERSION,
        )
        result = await handle_tools_call_async(
            params={"name": name, "arguments": tool_args},
            context=context,
        )
        if isinstance(result, JsonRpcError):
            raise RuntimeError(f"drf-mcp tool {name!r} failed: {result.message}")
        return result.get("structuredContent", result.get("content"))


__all__ = ["DrfMcpToolset"]
