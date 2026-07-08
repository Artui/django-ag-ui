"""Cross-package helpers shared by the agent endpoint and the catalog views."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import Any

from asgiref.sync import async_to_sync, iscoroutinefunction, sync_to_async
from django.http import HttpRequest, JsonResponse

# The ``get_user`` hook contract shared by every view that authenticates:
# sync or async, returning the acting user. Sync hooks may freely use the
# ORM — the async callers below run them off the event loop.
GetUser = Callable[[HttpRequest], Any] | Callable[[HttpRequest], Awaitable[Any]]

# The ``authorize`` predicate contract: a fast, synchronous check run *after*
# the acting user is established, returning ``True`` to allow the request. It
# reads already-resolved request attributes (e.g. ``request.user.is_staff``);
# a failing predicate denies with **403** (authenticated-but-forbidden), as
# distinct from ``require_authenticated``'s **401** (no user at all). This is
# the seam a staff-gated mount (django-admin-agent) uses to return JSON, not an
# HTML login redirect. The async callers run it off the event loop, so a
# predicate that does touch the ORM stays safe.
AuthorizePredicate = Callable[[HttpRequest], bool]


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
    authorize: AuthorizePredicate | None = None,
) -> int | None:
    """The shared authorize policy, async flavour (the AG-UI SSE endpoint).

    Establishes the acting user — via the ``get_user`` hook when supplied,
    else by materializing the middleware-provided lazy user off the loop —
    then applies the two gates in order. Returns the HTTP status the caller
    should deny with, or ``None`` when the request is allowed:

    - ``401`` — ``require_authenticated`` is set and the resolved user is
      anonymous (no acting user).
    - ``403`` — an ``authorize`` predicate is supplied and rejects the
      established user (authenticated but not permitted, e.g. non-staff).

    The predicate runs off the event loop so it may safely read the ORM.
    """
    if get_user is not None:
        request.user = await acall_get_user(get_user, request)
        user: Any = request.user
    else:
        user = await sync_to_async(materialize_request_user, thread_sensitive=True)(request)
    if require_authenticated and not getattr(user, "is_authenticated", False):
        return 401
    if authorize is not None and not await sync_to_async(authorize, thread_sensitive=True)(request):
        return 403
    return None


def authorize(
    request: HttpRequest,
    *,
    get_user: GetUser | None,
    require_authenticated: bool,
    authorize: AuthorizePredicate | None = None,
) -> int | None:
    """Sync flavour of :func:`aauthorize` for the catalog views.

    Same policy, same hook contract, same ``int | None`` deny-status return —
    one place to reason about who may read the tool / skill catalogs and who
    the agent acts as.
    """
    if get_user is not None:
        request.user = call_get_user(get_user, request)
        user: Any = request.user
    else:
        user = getattr(request, "user", None)
    if require_authenticated and not getattr(user, "is_authenticated", False):
        return 401
    if authorize is not None and not authorize(request):
        return 403
    return None


def auth_error_response(status: int) -> JsonResponse:
    """The JSON deny response for an :func:`authorize` / :func:`aauthorize` status.

    ``401`` → ``authentication required``; ``403`` → ``forbidden``. Keeps every
    view's deny branch identical (and JSON, never an HTML login redirect).
    """
    message = "authentication required" if status == 401 else "forbidden"
    return JsonResponse({"error": message}, status=status)
