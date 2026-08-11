from __future__ import annotations


def resolve_csrf_exempt(csrf_exempt: bool | None) -> bool:
    """Resolve the tri-state ``csrf_exempt`` argument to the flag Django reads.

    ``CsrfViewMiddleware`` reads a ``csrf_exempt`` attribute off the resolved
    view callable, and every view in this package is a callable *instance*, so
    each one has to set that attribute itself — an unset attribute is an
    enforced view, which is the same thing as ``False``.

    ⚠ **This exists so the default lives in exactly one place.** It used to be
    written inline on the agent view alone, and the sub-views set nothing —
    which made ``csrf_exempt=True`` mean "the run endpoint is exempt and the
    write endpoints are not", a state no caller asked for and none could reach
    from the outside. A shared resolver is what keeps the run endpoint and the
    endpoints beside it answering the same question the same way.

    ``None`` is *unstated* rather than ``False``: AG-UI clients typically
    authenticate by header, where CSRF does not apply, so exempt is the useful
    default — but the view keeps the three states apart so
    ``_warn_if_csrf_unstated`` can tell "nobody decided" from a deliberate
    ``True``. That distinction belongs to the agent view, which is the one
    place the warning fires; here only the resolved flag matters.
    """
    return True if csrf_exempt is None else csrf_exempt


__all__ = ["resolve_csrf_exempt"]
