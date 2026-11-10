from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from django_ag_ui.agent.types.untrusted_context_item import UntrustedContextItem

# The same key on both carriers: the web component moved the array from the
# message's top level into its ``metadata`` without renaming it.
_REFS_KEY = "attachments"
_LABEL = "Files the user has attached to this conversation"
_READ_HINT = "Use the read_attachment tool with an id to read a file's contents."


def attachment_context_items(messages: Sequence[Any]) -> tuple[UntrustedContextItem, ...]:
    """Derive the attachment manifest from the posted messages themselves."""
    mentions: dict[str, _AttachmentMention] = {}
    for message in messages:
        if getattr(message, "role", None) != "user":
            continue
        raw = _attachment_refs(message)
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


def _attachment_refs(message: Any) -> Any:
    """The raw refs one message carries, from whichever carrier holds them."""
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, Mapping) and _REFS_KEY in metadata:
        return metadata[_REFS_KEY]
    return (getattr(message, "model_extra", None) or {}).get(_REFS_KEY)


@dataclass(frozen=True)
class _AttachmentMention:
    """One attachment ref as it survived a permissive parse."""

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
