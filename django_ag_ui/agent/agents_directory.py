from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponseNotAllowed
from django.http.response import HttpResponseBase
from django.urls import URLPattern, path, reverse
from django_spec_agents.utils import auth_error_response

from django_ag_ui.agent.agui_server import AGUIServer
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.fresh_list_response import fresh_list_response

DEFAULT_DIRECTORY_NAMESPACE = "ag_ui_agents"

# Route name to entry key, keyed as django-admin-agent's sidebar context keys them.
_URL_KEYS = (
    ("endpoint", "endpoint"),
    ("tools", "tools_url"),
    ("threads", "threads_url"),
    ("runs", "runs_url"),
    ("skills", "skills_url"),
    ("skill_editor", "skill_editor_url"),
    ("routines", "routines_url"),
    ("attachments", "attachments_url"),
    ("transcribe", "transcribe_url"),
)


class AgentsDirectory:
    """One view over several [`AGUIServer`][django_ag_ui.AGUIServer] mounts, mounted once.

    ```python
    home = AgentsDirectory([spa_agent, admin_agent])
    urlpatterns = [path("agents-home/", home.urls), ...]
    ```

    - ``GET agents/`` (name ``agents``): ``{"agents": [...]}``, one entry per mount the
      caller may use, in list order: ``id``, ``name``, ``description`` and one URL per
      list-level route (``endpoint``, ``tools_url``, ``threads_url``, ``runs_url``,
      ``skills_url``, ``skill_editor_url``, ``routines_url``, ``attachments_url``,
      ``transcribe_url``), ``None`` for a route that mount does not serve. ETag and
      ``304`` as on ``threads/``; no stream.
    - ``GET inbox/`` (name ``inbox``): ``{"threads": [...]}``, every admitted mount's own
      ``threads/`` rows, each with ``agent_id``, merged newest first by ``updated_at``
      (``None`` last, ties in list order). ``?limit`` caps each mount's share, clamped to
      that mount's ``thread_list_limit``; nothing is cut across mounts. ETag polling and,
      over ASGI with ``Accept: text/event-stream``, SSE exactly as ``threads/``.

    Which mounts a caller sees is each mount's own policy
    ([`AGUIServer.admits`][django_ag_ui.AGUIServer.admits]), run on its own copy of the
    request, so a token mount and a session mount each list under their own principal. A
    mount that denies is left out of both routes; when none admits, the answer is ``401``,
    or ``403`` when every denial was ``403``.

    Every server needs ``id=`` and its own ``namespace``, mounted at the top of the
    urlconf so ``reverse(f"{namespace}:endpoint")`` resolves. ``config`` supplies the
    stream's ``heartbeat_seconds``. One stream tick lists each admitted mount's threads
    (and reads its run store), every two seconds, per open stream.
    """

    def __init__(
        self,
        servers: Sequence[AGUIServer],
        *,
        namespace: str = DEFAULT_DIRECTORY_NAMESPACE,
        config: AGUIConfig | None = None,
    ) -> None:
        if not servers:
            raise ImproperlyConfigured("AgentsDirectory needs at least one AGUIServer")
        ids: set[str] = set()
        namespaces: set[str] = set()
        for server in servers:
            if server.id is None:
                raise ImproperlyConfigured("AgentsDirectory needs id= on every AGUIServer")
            if server.id in ids:
                raise ImproperlyConfigured(f"AgentsDirectory: id {server.id!r} is repeated")
            if server.namespace in namespaces:
                raise ImproperlyConfigured(
                    f"AgentsDirectory: namespace {server.namespace!r} is repeated"
                )
            ids.add(server.id)
            namespaces.add(server.namespace)
        self._servers = tuple(servers)
        self._namespace = namespace
        self._config = config if config is not None else build_ag_ui_config()

    @property
    def urls(self) -> tuple[list[URLPattern], str, str]:
        """The namespaced ``(patterns, app_name, namespace)`` triple ``path()`` mounts."""
        patterns = [
            path("agents/", self._agents, name="agents"),
            path("inbox/", self._inbox, name="inbox"),
        ]
        return patterns, self._namespace, self._namespace

    async def _agents(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        admitted: list[AGUIServer] = []
        denials: list[int] = []
        for server in self._servers:
            deny = await server.admits(request)
            if deny is None:
                admitted.append(server)
            else:
                denials.append(deny)
        if not admitted:
            return auth_error_response(_deny_status(denials))
        body = {"agents": [_entry(server) for server in admitted]}

        async def current() -> dict[str, Any]:
            return body

        return await fresh_list_response(request, current, heartbeat_seconds=0, stream=False)

    async def _inbox(self, request: HttpRequest) -> HttpResponseBase:
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        limit = _requested_limit(request)
        first = await self._merged(request, limit)
        if isinstance(first, int):
            return auth_error_response(first)
        pending: list[dict[str, Any]] = [first]

        async def current() -> dict[str, Any]:
            if pending:
                return pending.pop()
            merged = await self._merged(request, limit)
            return {"threads": []} if isinstance(merged, int) else merged

        return await fresh_list_response(
            request, current, heartbeat_seconds=self._config.heartbeat_seconds
        )

    async def _merged(self, request: HttpRequest, limit: int | None) -> dict[str, Any] | int:
        """Every admitted mount's rows newest first, or the deny status when none admits."""
        rows: list[dict[str, Any]] = []
        denials: list[int] = []
        for server in self._servers:
            listed = await server.thread_rows(request, limit=limit)
            if isinstance(listed, int):
                denials.append(listed)
            else:
                rows.extend(listed)
        if len(denials) == len(self._servers):
            return _deny_status(denials)
        return {"threads": sorted(rows, key=_newest_first)}


def _entry(server: AGUIServer) -> dict[str, Any]:
    """One ``agents/`` entry: the mount's identity and the URL of each list-level route."""
    mounted = set(server.routes())
    urls = {
        key: reverse(f"{server.namespace}:{name}") if name in mounted else None
        for name, key in _URL_KEYS
    }
    return {"id": server.id, "name": server.name, "description": server.description} | urls


def _newest_first(row: dict[str, Any]) -> tuple[bool, float]:
    updated = row.get("updated_at")
    if updated is None:
        return True, 0.0
    return False, -datetime.fromisoformat(updated).timestamp()


def _deny_status(denials: list[int]) -> int:
    return 401 if 401 in denials else 403


def _requested_limit(request: HttpRequest) -> int | None:
    """``?limit`` when it is a positive integer, else ``None`` (each mount's own cap)."""
    try:
        requested = int(request.GET.get("limit", ""))
    except ValueError:
        return None
    return requested if requested >= 1 else None


__all__ = ["AgentsDirectory"]
