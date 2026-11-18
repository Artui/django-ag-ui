from __future__ import annotations

import json
from typing import Any

from django_ag_ui.agent.mcp_apps_view import MCPAppsView
from tests.agent.test_mcp_apps_route import _call, _post, _read, _RemoteToolset


class _Throttle:
    def __init__(self, retry_after: int | None = 7) -> None:
        self._retry_after = retry_after

    def consume(self, request: Any) -> int | None:
        return self._retry_after


async def test_a_throttle_limits_app_calls() -> None:
    view = MCPAppsView(list, throttle=_Throttle(), csrf_exempt=True)
    response = await view(_post(_call("remote")))
    assert response.status_code == 429
    assert response["Retry-After"] == "7"
    assert json.loads(response.content)["error"]["code"] == -32005


async def test_a_throttle_that_allows_lets_the_call_through() -> None:
    view = MCPAppsView(lambda: [_RemoteToolset("remote")], throttle=_Throttle(None))
    assert (await view(_post(_read("remote")))).status_code == 200
