from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.utils.cache import patch_vary_headers
from django.utils.http import parse_etags

from django_ag_ui.agent.heartbeat_stream import heartbeat_stream

_STREAM_POLL_SECONDS = 2.0
_STREAM_MAX_SECONDS = 300.0

ListBody = Callable[[], Awaitable[dict[str, Any]]]


async def fresh_list_response(
    request: HttpRequest, body: ListBody, *, heartbeat_seconds: float, stream: bool = True
) -> HttpResponseBase:
    """An ETag-checked poll, or SSE frames over ASGI (a WSGI worker cannot hold a stream)."""
    if (
        stream
        and isinstance(request, ASGIRequest)
        and "text/event-stream" in request.headers.get("Accept", "")
    ):
        return _stream(request, body, heartbeat_seconds)
    current = await body()
    etag = _etag(current)
    if _matches(request.headers.get("If-None-Match"), etag):
        response: HttpResponseBase = HttpResponse(status=304)
    else:
        response = JsonResponse(current)
    response["ETag"] = etag
    response["Cache-Control"] = "private, no-cache"
    patch_vary_headers(response, ["Accept"])
    return response


def _stream(request: HttpRequest, body: ListBody, heartbeat_seconds: float) -> HttpResponseBase:
    frames = _frames(body, request.headers.get("Last-Event-ID"))
    if heartbeat_seconds > 0:
        frames = heartbeat_stream(frames, interval=heartbeat_seconds)
    response = StreamingHttpResponse(frames, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    patch_vary_headers(response, ["Accept"])
    return response


async def _frames(body: ListBody, seen: str | None) -> AsyncIterator[str]:
    deadline = asyncio.get_running_loop().time() + _STREAM_MAX_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        current = await body()
        etag = _etag(current)
        if etag != seen:
            seen = etag
            yield f"id: {etag}\ndata: {json.dumps(current, separators=(',', ':'))}\n\n"
        await asyncio.sleep(_STREAM_POLL_SECONDS)


def _matches(header: str | None, etag: str) -> bool:
    """Whether an ``If-None-Match`` value (a list, ``*`` or weakened by gzip) names ``etag``."""
    if header is None:
        return False
    return any(tag == "*" or tag.removeprefix("W/") == etag for tag in parse_etags(header))


def _etag(body: dict[str, Any]) -> str:
    """A strong ETag over the list body, so a poll and a stream frame agree on it."""
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return f'"{hashlib.sha256(encoded).hexdigest()[:32]}"'


__all__ = ["fresh_list_response"]
