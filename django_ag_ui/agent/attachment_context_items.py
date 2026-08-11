from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django_ag_ui.agent.types.untrusted_context_item import UntrustedContextItem

_LABEL = "Files the user has attached to this conversation"
_READ_HINT = "Use the read_attachment tool with an id to read a file's contents."


def attachment_context_items(messages: Sequence[Any]) -> tuple[UntrustedContextItem, ...]:
    """Derive the attachment manifest from the posted messages themselves.

    **Why a non-standard field is read at all.** The web component augments the
    user message it posts with an ``attachments`` array of the refs the composer
    uploaded (``id`` / ``name`` / ``mime`` / ``size``). AG-UI does not declare
    that field, but ``ag_ui.core`` validates with ``extra="allow"``, so it
    arrives intact on ``model_extra`` — and ``AGUIAdapter.load_messages`` simply
    ignores it. Nothing else on the wire carries the ids, so without reading it
    the model is told a file exists only by whatever the user typed.

    **Why the messages, rather than this run's own upload list.** The client
    clears its per-run manifest once a run settles, so a follow-up question
    about the same PDF would arrive with no refs at all. The message list is
    resent whole on every turn, so refs derived from it ride every subsequent
    turn of the conversation — and, once the thread is stored with the client's
    own messages, survive a page reload too.

    The parse is deliberately **total**: an entry that is not a mapping, or
    carries no usable ``id``, is skipped in silence, and a field of the wrong
    type falls back rather than raising. A shape change upstream degrades to
    "no manifest", never to an exception mid-run. Only ``role == "user"``
    messages are read, so a forged assistant turn in the posted history cannot
    inject a manifest of its own.

    Nothing parsed here is trusted for authorisation. The ids reach the model as
    text and are only useful through ``read_attachment``, which resolves them
    against an owner-scoped store — a forged or guessed id resolves to nothing.

    Returns a single item (or none at all), because the model wants one list of
    the files it can read, not one section per message.
    """
    mentions: dict[str, _AttachmentMention] = {}
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        raw = (getattr(message, "model_extra", None) or {}).get("attachments")
        if not isinstance(raw, list):
            continue
        for entry in raw:
            mention = _parse_mention(entry)
            if mention is None or mention.id in mentions:
                continue
            mentions[mention.id] = mention
    if not mentions:
        return ()
    lines = [_line(mention) for mention in mentions.values()]
    return (UntrustedContextItem(label=_LABEL, value="\n".join([*lines, _READ_HINT])),)


@dataclass(frozen=True)
class _AttachmentMention:
    """One attachment ref as it survived a permissive parse.

    Deliberately **not** an ``AttachmentRef``: that record describes a file the
    server issued and every field of it is known good. Here every field but
    ``id`` may be missing or the wrong type, because this is an undeclared wire
    field written by the browser. Sharing the server-side type would let the
    two be confused, and would mean widening a trusted record's field types to
    accommodate untrusted input.
    """

    id: str
    name: str
    mime: str
    size: int | None


def _parse_mention(entry: Any) -> _AttachmentMention | None:
    """One ref, or ``None`` when the entry carries nothing usable."""
    if not isinstance(entry, Mapping):
        return None
    identifier = entry.get("id")
    if not isinstance(identifier, str) or not identifier.strip():
        return None
    name = entry.get("name")
    mime = entry.get("mime")
    size = entry.get("size")
    return _AttachmentMention(
        id=identifier,
        name=name if isinstance(name, str) else "",
        mime=mime if isinstance(mime, str) else "",
        size=size if isinstance(size, int) else None,
    )


def _line(mention: _AttachmentMention) -> str:
    """One manifest line, with a stated fallback for every missing field."""
    size = f"{mention.size} bytes" if mention.size is not None else "size unknown"
    return (
        f"- {mention.name or 'attachment'} "
        f"(id: {mention.id}, {mention.mime or 'unknown type'}, {size})"
    )


__all__ = ["attachment_context_items"]
