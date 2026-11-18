from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence, Set
from typing import Any, cast

from asgiref.sync import markcoroutinefunction, sync_to_async
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.http.response import HttpResponseBase
from django_spec_agents.policy.audit.null_audit_logger import NullAuditLogger
from django_spec_agents.policy.audit.types.audit_event import AuditEvent
from django_spec_agents.policy.audit.types.audit_logger import AuditLogger
from django_spec_agents.utils import AuthorizePredicate, GetUser, aauthorize

from django_ag_ui.agent.build_mcp_bridge import build_mcp_bridge
from django_ag_ui.agent.types.throttle import Throttle
from django_ag_ui.reject_async_throttle import reject_async_throttle
from django_ag_ui.resolve_csrf_exempt import resolve_csrf_exempt

_PARSE_ERROR = -32700
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603
_UNAUTHORIZED = -32001
_RATE_LIMITED = -32005
_FORBIDDEN = -32006

_APP_METHODS = {"tools/call": "call_tool_for_app", "resources/read": "read_resource_for_app"}


class _AppCallError(Exception):
    def __init__(self, code: int, message: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


class MCPAppsView:
    """Relays an MCP Apps view's ``tools/call`` and ``resources/read`` to the toolset it names.

    The body, the answers and what a remote toolset needs: the MCP Apps views page."""

    def __init__(
        self,
        agent_toolsets: Callable[[], Sequence[Any]],
        *,
        mcp_server: Any = None,
        claimed_names: Callable[[], Set[str]] = frozenset,
        audit_logger: AuditLogger | None = None,
        throttle: Throttle | None = None,
        require_authenticated: bool = True,
        get_user: GetUser | None = None,
        authorize: AuthorizePredicate | None = None,
        csrf_exempt: bool | None = None,
    ) -> None:
        self._agent_toolsets = agent_toolsets
        self._mcp_server = mcp_server
        self._claimed_names = claimed_names
        self._audit_logger: AuditLogger = (
            audit_logger if audit_logger is not None else NullAuditLogger()
        )
        reject_async_throttle(throttle, allows="call")
        self._throttle = throttle
        self._require_authenticated = require_authenticated
        self._get_user = get_user
        self._authorize_predicate = authorize
        self.csrf_exempt = resolve_csrf_exempt(csrf_exempt)
        markcoroutinefunction(cast("Any", self))

    async def __call__(self, request: HttpRequest) -> HttpResponseBase:
        deny = await aauthorize(
            request,
            get_user=self._get_user,
            require_authenticated=self._require_authenticated,
            authorize=self._authorize_predicate,
        )
        if deny is not None:
            code, message = (
                (_UNAUTHORIZED, "authentication required")
                if deny == 401
                else (_FORBIDDEN, "forbidden")
            )
            return _error(code, message, status=deny)
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        throttled = await self._throttled(request)
        if throttled is not None:
            return throttled
        started = time.perf_counter()
        body: Any = None
        try:
            body = _parse_body(request.body)
            result = await self._dispatch(request, body)
        except _AppCallError as error:
            audited = f"{error.code}: {error.detail or error.message}"
            self._audit(request, body, started, error=audited)
            return _error(error.code, error.message)
        self._audit(request, body, started, error=None)
        return JsonResponse({"result": result})

    async def _dispatch(self, request: HttpRequest, body: dict[str, Any]) -> Any:
        """Find the toolset named by ``serverId`` and call its app method."""
        method = body.get("method")
        if not isinstance(method, str) or method not in _APP_METHODS:
            raise _AppCallError(_METHOD_NOT_FOUND, f"Method {method!r} is not relayed for apps")
        server_id, params = body.get("serverId"), body.get("params", {})
        if not isinstance(server_id, str) or not isinstance(params, dict):
            raise _AppCallError(_INVALID_PARAMS, "serverId must be a string, params an object")
        toolset = await self._find_toolset(request, server_id)
        call = getattr(toolset, _APP_METHODS[method], None)
        if not callable(call):
            raise _AppCallError(_METHOD_NOT_FOUND, f"Server {server_id!r} does not serve {method}")
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise _AppCallError(_INVALID_PARAMS, "params.name must be a string")
            return await _answer(call(name, params.get("arguments")))
        uri = params.get("uri")
        if not isinstance(uri, str):
            raise _AppCallError(_INVALID_PARAMS, "params.uri must be a string")
        return await _answer(call(uri))

    async def _find_toolset(self, request: HttpRequest, server_id: str) -> Any:
        """The one leaf toolset whose ``id`` is ``server_id``, unwrapping wrapper toolsets."""
        toolsets = list(await sync_to_async(self._agent_toolsets, thread_sensitive=True)())
        if self._mcp_server is not None:
            toolsets.append(
                build_mcp_bridge(self._mcp_server, request, exclude_names=self._claimed_names())
            )
        found: list[Any] = []
        for toolset in toolsets:
            toolset.apply(lambda leaf: found.append(leaf) if leaf.id == server_id else None)
        if len(found) != 1:
            reason = "no" if not found else "more than one"
            raise _AppCallError(_INVALID_PARAMS, f"The agent has {reason} server {server_id!r}")
        return found[0]

    async def _throttled(self, request: HttpRequest) -> HttpResponseBase | None:
        """Apply the ``throttle`` hook, or ``None`` when the call may proceed."""
        if self._throttle is None:
            return None
        retry_after = await sync_to_async(self._throttle.consume, thread_sensitive=True)(request)
        if retry_after is None:
            return None
        response = _error(_RATE_LIMITED, "rate limited", status=429)
        response["Retry-After"] = str(retry_after)
        return response

    def _audit(self, request: HttpRequest, body: Any, started: float, *, error: str | None) -> None:
        method = body.get("method") if isinstance(body, dict) else None
        known = isinstance(method, str) and method in _APP_METHODS
        name = f"mcp_apps.{method}" if known else "mcp_apps"
        shown = {k: body.get(k) for k in ("serverId", "params")} if isinstance(body, dict) else {}
        self._audit_logger.record(
            AuditEvent(
                tool_name=name,
                arguments_repr=json.dumps(shown, sort_keys=True, default=str),
                duration_ms=(time.perf_counter() - started) * 1000.0,
                success=error is None,
                error=error,
                ip_address=request.META.get("REMOTE_ADDR"),
            )
        )


def _parse_body(raw: bytes) -> dict[str, Any]:
    """The request body as a JSON object, or a coded error."""
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise _AppCallError(_PARSE_ERROR, "The body is not JSON") from exc
    if not isinstance(body, dict):
        raise _AppCallError(_INVALID_PARAMS, "The body must be a JSON object")
    return body


async def _answer(pending: Any) -> Any:
    """Await an app call; a returned or raised coded error becomes ``_AppCallError``."""
    try:
        result = await pending
    except Exception as exc:
        coded = _coded(exc)
        if coded is None:
            raise _AppCallError(_INTERNAL_ERROR, "The server failed the call", repr(exc)) from exc
        raise coded from exc
    coded = _coded(result)
    if coded is not None:
        raise coded
    return result


def _coded(error: object) -> _AppCallError | None:
    """A JSON-RPC-shaped error, an int ``code`` and a str ``message``, as ``_AppCallError``."""
    code, message = getattr(error, "code", None), getattr(error, "message", None)
    if isinstance(code, int) and isinstance(message, str):
        return _AppCallError(code, message)
    return None


def _error(code: int, message: str, *, status: int = 200) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


__all__ = ["MCPAppsView"]
