from __future__ import annotations


def resolve_csrf_exempt(csrf_exempt: bool | None) -> bool:
    """Resolve the tri-state ``csrf_exempt`` argument to the flag Django reads.

    ``CsrfViewMiddleware`` reads a ``csrf_exempt`` attribute off the resolved
    view callable, and every view in this package is a callable *instance*, so
    each one has to set that attribute itself — an unset attribute is an enforced
    view. One resolver is what keeps the run endpoint and the endpoints beside it
    answering the same question the same way.

    ``None`` means *unstated*, not ``False``: AG-UI clients typically
    authenticate by header, where CSRF does not apply, so exempt is the useful
    default. Callers keep the three states apart so
    ``_warn_if_csrf_unstated`` can tell "nobody decided" from a deliberate
    ``True``; here only the resolved flag matters.
    """
    return True if csrf_exempt is None else csrf_exempt


__all__ = ["resolve_csrf_exempt"]
