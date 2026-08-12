from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ag_ui.core import Message

_TEXT_PART = "text"
_INLINE_SOURCE = "data"


def strip_binary_content(messages: Sequence[Message]) -> list[Message]:
    """Copies of ``messages`` with inlined file bytes removed.

    ``read_attachment`` hands the model a PDF or image as a ``ToolReturn``
    carrying ``BinaryContent``, which serialises onto the wire as a synthetic
    ``user`` message holding a base64 ``data`` source. Persisted, a 2.6 MB PDF
    becomes roughly 3.5 MB of base64 in one conversation row, refetched on every
    thread load and re-posted on every turn after a reload. The bytes never reach
    the live event stream, so dropping the stored copy just makes the model call
    ``read_attachment`` again — which is already what a same-session follow-up
    does.

    **Only server-generated messages are ever passed through this**, under
    :class:`~django_ag_ui.agent.agent_session.AgentSession`'s rule that the
    server never persists bytes it generated and never discards bytes the client
    sent. An inline image a front end sends reaches both the model and the row
    untouched.

    Textual parts survive, so a mixed message loses only its payload, and a part
    carrying a ``url`` rather than inline ``data`` is a reference, not a payload,
    and is kept. A message left with no content at all is dropped rather than
    stored as a blank chat row. A message whose content is a plain string is
    returned unchanged — the same object, not a copy.

    Copies are made with ``model_copy``, **never by re-validating**: a round-trip
    through ``load_messages`` / ``dump_messages`` regenerates every message id and
    discards ``model_extra``, which is what drops the client's ``attachments``
    refs.
    """
    kept: list[Message] = []
    for message in messages:
        content = message.content
        if not isinstance(content, list):
            kept.append(message)
            continue
        parts = [part for part in content if not _is_inline_binary(part)]
        if not parts:
            continue
        if len(parts) == len(content):
            kept.append(message)
            continue
        kept.append(message.model_copy(update={"content": parts}))
    return kept


def _is_inline_binary(part: Any) -> bool:
    """Whether ``part`` carries bytes inline rather than a reference to them."""
    if part.type == _TEXT_PART:
        return False
    source = getattr(part, "source", None)
    if source is None:
        # The deprecated ``binary`` part predates ``InputContentSource`` and
        # holds its alternatives flat: ``data`` is a payload, ``id`` / ``url``
        # are references.
        return part.data is not None
    # ``image`` / ``audio`` / ``video`` / ``document`` parts, whose source is
    # either inline base64 (``data``) or a ``url`` pointing at the bytes.
    return source.type == _INLINE_SOURCE


__all__ = ["strip_binary_content"]
