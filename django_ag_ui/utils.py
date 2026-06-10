"""Cross-package helpers shared by the agent endpoint and the catalog views."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from asgiref.sync import async_to_sync, iscoroutinefunction, sync_to_async
from django.http import HttpRequest

# The ``get_user`` hook contract shared by every view that authenticates:
# sync or async, returning the acting user. Sync hooks may freely use the
# ORM — the async callers below run them off the event loop.
GetUser = Callable[[HttpRequest], Any] | Callable[[HttpRequest], Awaitable[Any]]


async def acall_get_user(hook: GetUser, request: HttpRequest) -> Any:
    """Run a ``get_user`` hook from async code, ORM-safe either way.

    - **async hook** → awaited directly (detected with asgiref's
      ``iscoroutinefunction``, which also honours
      ``markcoroutinefunction``-marked callables). Detect-before-call
      matters: a sync ORM hook must not be invoked on the loop thread even
      once.
    - **sync hook** → run via ``sync_to_async(thread_sensitive=True)`` in
      Django's shared sync executor, where the ORM, transactions, and
      thread-locals behave correctly — the same pattern
      ``ModelConversationStore`` uses. A token → ``User`` lookup Just Works.
    - Belt-and-suspenders: a sync callable that itself returns a coroutine
      (e.g. a ``partial`` wrapping an async fn) is awaited rather than
      leaked onto ``request.user``.
    """
    if iscoroutinefunction(hook):
        return await hook(request)
    result = await sync_to_async(hook, thread_sensitive=True)(request)
    return await result if isawaitable(result) else result


def call_get_user(hook: GetUser, request: HttpRequest) -> Any:
    """Sync twin of :func:`acall_get_user` for the sync catalog views.

    Sync views run in a worker thread under ASGI, so a sync ORM hook is
    already safe here; an async hook is bridged via ``async_to_sync``.
    """
    if iscoroutinefunction(hook):
        return async_to_sync(hook)(request)
    result = hook(request)
    if isawaitable(result):
        return async_to_sync(_consume)(result)
    return result


async def _consume(awaitable: Awaitable[Any]) -> Any:
    return await awaitable


def materialize_request_user(request: HttpRequest) -> Any:
    """Force the lazy ``request.user`` and return it — call **off** the loop.

    With Django's default DB-backed sessions, the first touch of
    ``request.user`` (a ``SimpleLazyObject``) runs the session + user
    queries, which Django forbids on the async event loop
    (``SynchronousOnlyOperation``). Forcing it here — in a worker thread —
    caches the resolved user on the lazy wrapper, so every later read
    (auth gate, conversation ownership, the drf-mcp bridge's ``TokenInfo``)
    is loop-safe. (Django 5+ has ``request.auser()`` for this; the package
    floor is 4.2, so the thread hop is the portable spelling.)
    """
    user = getattr(request, "user", None)
    getattr(user, "is_authenticated", False)
    return user


async def aauthorize(
    request: HttpRequest,
    *,
    get_user: GetUser | None,
    require_authenticated: bool,
) -> bool:
    """The shared authorize policy, async flavour (the AG-UI SSE endpoint).

    Establishes the acting user — via the ``get_user`` hook when supplied,
    else by materializing the middleware-provided lazy user off the loop —
    then enforces ``require_authenticated``. Returns ``False`` when the
    gate is on and the resolved user is anonymous; the caller 401s.
    """
    if get_user is not None:
        request.user = await acall_get_user(get_user, request)
        user: Any = request.user
    else:
        user = await sync_to_async(materialize_request_user, thread_sensitive=True)(request)
    if not require_authenticated:
        return True
    return bool(getattr(user, "is_authenticated", False))


def authorize(
    request: HttpRequest,
    *,
    get_user: GetUser | None,
    require_authenticated: bool,
) -> bool:
    """Sync flavour of :func:`aauthorize` for the catalog views.

    Same policy, same hook contract — one place to reason about who may
    read the tool / skill catalogs and who the agent acts as.
    """
    if get_user is not None:
        request.user = call_get_user(get_user, request)
        user: Any = request.user
    else:
        user = getattr(request, "user", None)
    if not require_authenticated:
        return True
    return bool(getattr(user, "is_authenticated", False))
