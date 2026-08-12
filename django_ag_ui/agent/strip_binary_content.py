from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ag_ui.core import Message

_TEXT_PART = "text"
_INLINE_SOURCE = "data"


def strip_binary_content(messages: Sequence[Message]) -> list[Message]:
    """Copies of ``messages`` with inlined file bytes removed.

    **The problem.** ``read_attachment`` hands the model a PDF or an image as a
    ``ToolReturn`` carrying ``BinaryContent``. That is right for the *run* — the
    model has to see the file — but the bytes then serialise onto the wire as a
    synthetic ``user`` message whose content is a single ``document`` part with a
    base64 ``data`` source. Persist that message and a 2.6 MB PDF becomes roughly
    3.5 MB of base64 in one conversation row, refetched by the browser on every
    thread load and re-posted by it on every turn after a reload.

    The bytes never reach the live event stream, so a same-session follow-up
    already posts a bytes-free history and the model simply calls
    ``read_attachment`` again. Dropping the stored copy makes that the behaviour
    everywhere rather than only before the first reload.

    **Only server-generated messages are ever passed through this.** The rule
    :class:`~django_ag_ui.agent.agent_session.AgentSession` applies it under is
    that the server never persists bytes it generated and never discards bytes
    the client sent — so this sees a run's own dumped messages and a resumed
    run's dumped snapshot, and never the messages a client posted. An inline
    image a front end sends reaches both the model and the row untouched.

    **What survives.** Textual parts are kept, so a mixed message loses only its
    payload. The note the tool wrote alongside the bytes is a *separate* ``tool``
    message with plain string content, so it is never touched: a reader of the
    stored thread still sees which file was read and what the model was told
    about it. A part that carries a ``url`` rather than inline ``data`` is a
    reference, not a payload, and is kept too — dropping it would lose
    information and save nothing.

    **What is dropped.** A message left with no content at all is dropped rather
    than stored as an empty bubble: the bytes *were* the whole message, and an
    empty user turn renders as a blank chat row. This only ever applies to
    messages whose content is a *list* of parts — a message whose content is a
    plain string is returned unchanged, the same object rather than a copy, so
    the common case costs a type check and nothing else.

    **Copies are made with ``model_copy``, never by re-validating.** A round-trip
    through ``AGUIAdapter.load_messages`` / ``dump_messages`` regenerates every
    message id and discards ``model_extra`` — the exact step that dropped the
    client's ``attachments`` refs before. Keeping ids and extras intact is what
    makes this safe to point at any message list, rather than only at the
    server-generated ones it is pointed at today.
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
