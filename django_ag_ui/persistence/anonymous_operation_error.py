from __future__ import annotations


class AnonymousOperationError(Exception):
    """Raised when a model-backed store is asked to act for an anonymous request.

    The reference stores refuse anonymous thread / attachment operations unless
    ``DJANGO_AG_UI["ALLOW_ANONYMOUS"]`` is set — otherwise every anonymous
    visitor would share one empty-string owner bucket and could read or delete
    each other's data. The persistence views catch this and return ``403``.
    """


__all__ = ["AnonymousOperationError"]
