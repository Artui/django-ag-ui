"""The heartbeat measured where a proxy measures it: on a socket, as bytes arrive.

Every other heartbeat test reads a response from inside the process, and every
in-process client holds the body it is reading: ``httpx.ASGITransport`` buffers
the whole of it before returning, and a ``streaming_content`` loop sees each
chunk the moment the generator yields it, whatever the transport would have done
with it afterwards. An idle proxy sees neither. It sees how long the socket went
without a byte, so that is what is measured here -- a real ASGI server, a raw TCP
client, and the longest gap between reads while the run is stalled.

The two tests are one experiment. The second runs the same harness with the
heartbeat off and requires it to *see* the silence, because a harness that
cannot see a silence would pass the first test for any transport that buffers.
"""

from __future__ import annotations

import asyncio
import json
import socket
import types
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from django.core.handlers.asgi import ASGIHandler
from django.test import override_settings
from django.urls import path
from django_pydantic_agent.registry.tool_registry import ToolRegistry
from pydantic_ai.models.function import FunctionModel

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config

_STALL = 1.0
"""How long the run stays silent between its two text deltas."""

_INTERVAL = 0.1
"""The heartbeat interval under test: ten beats inside one stall."""


async def _longest_silence_on_the_wire(heartbeat_seconds: float) -> tuple[float, bytes]:
    """Serve one stalled run over a real socket; return the longest gap and the bytes.

    The gap is measured only across the stall: from the read that carried the
    first delta to the moment the run is released. Before that the response is
    busy, and after it the run finishes on its own.
    """
    release = asyncio.Event()

    async def stream_fn(messages: list, info: Any) -> AsyncIterator[str]:
        yield "partial "
        await release.wait()
        yield "answer"

    server = AGUIServer(
        ToolRegistry(),
        model=FunctionModel(stream_function=stream_fn),
        require_authenticated=False,
        csrf_exempt=True,
        config=build_ag_ui_config(heartbeat_seconds=heartbeat_seconds),
    )
    urlconf = types.ModuleType("heartbeat_wire_urls")
    vars(urlconf)["urlpatterns"] = [path("agent/", server.urls)]

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(ASGIHandler(), lifespan="off", log_level="warning", access_log=False)
    )

    with override_settings(ROOT_URLCONF=urlconf):
        serving = asyncio.ensure_future(uvicorn_server.serve(sockets=[listener]))
        try:
            while not uvicorn_server.started:
                await asyncio.sleep(0.01)
            return await _read_one_stalled_run(port, release)
        finally:
            uvicorn_server.should_exit = True
            await serving
            listener.close()


async def _read_one_stalled_run(port: int, release: asyncio.Event) -> tuple[float, bytes]:
    body = json.dumps(
        {
            "threadId": "t1",
            "runId": "r1",
            "state": {},
            "messages": [{"id": "u1", "role": "user", "content": "hi"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        }
    ).encode()
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(
        b"POST /agent/ HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Accept: text/event-stream\r\n"
        b"Connection: close\r\n" + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
    )
    await writer.drain()

    loop = asyncio.get_running_loop()
    arrivals: list[tuple[float, bytes]] = []

    async def read_until_closed() -> None:
        while chunk := await reader.read(65536):
            arrivals.append((loop.time(), chunk))

    reading = asyncio.ensure_future(read_until_closed())
    try:
        deadline = loop.time() + 10.0
        while not any(b"partial " in chunk for _, chunk in arrivals):
            assert loop.time() < deadline, "the run never produced its first delta"
            await asyncio.sleep(0.005)
        stalled_at = next(at for at, chunk in arrivals if b"partial " in chunk)
        await asyncio.sleep(_STALL)
        released_at = loop.time()
        release.set()
        await asyncio.wait_for(reading, timeout=10.0)
    finally:
        writer.close()

    during_stall = [stalled_at] + [at for at, _ in arrivals if stalled_at < at < released_at]
    # Not strict: each arrival is paired with the next, so the second list is one short.
    gaps = [later - earlier for earlier, later in zip(during_stall, during_stall[1:], strict=False)]
    gaps.append(released_at - during_stall[-1])
    received = b"".join(chunk for _, chunk in arrivals)
    assert b"answer" in received, "the run did not finish after the stall"
    return max(gaps), received


async def test_a_stalled_run_never_leaves_the_socket_silent_for_an_interval() -> None:
    longest, received = await _longest_silence_on_the_wire(_INTERVAL)

    # Several intervals of slack for a loaded runner, and still well inside the
    # stall: a heartbeat that did not reach the socket would show the whole of it.
    assert longest < _STALL / 2, f"the socket went {longest:.2f}s without a byte"
    assert received.count(b": django-ag-ui heartbeat\n\n") >= 3


async def test_the_same_harness_sees_the_silence_with_the_heartbeat_off() -> None:
    longest, received = await _longest_silence_on_the_wire(0)

    assert longest > _STALL * 0.8, f"expected a silent stall, longest gap was {longest:.2f}s"
    assert b"heartbeat" not in received
