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
    """Derive the attachment manifest from the posted messages themselves.

    The web component puts the refs the composer uploaded on the user message it
    posts, in one of two places depending on its age. From its adoption of
    ``@ag-ui/client`` 1.0 it sends them at ``metadata["attachments"]``, because
    ``metadata`` is the one open slot AG-UI 1.0 declares on a message and that
    client strips every key it does not declare from the outgoing input. Every
    earlier release sends them as an undeclared top-level ``attachments`` array,
    which ``ag_ui.core`` keeps on ``model_extra`` because it validates with
    ``extra="allow"``. Both are read, by ``_attachment_refs``, and
    ``AGUIAdapter.load_messages`` ignores both. Nothing else on the wire carries
    the ids.

    Read from the messages rather than this run's own upload list because the
    client clears its per-run manifest once a run settles, whereas the message
    list is resent whole on every turn — so the refs ride every later turn and,
    once the thread is stored, survive a page reload.

    The parse is deliberately **total**, whichever carrier the refs came on: a
    non-mapping entry or one with no usable ``id`` is skipped in silence and a
    wrong-typed field falls back, so a shape change upstream degrades to "no
    manifest" rather than an exception mid-run. Only ``role == "user"`` messages
    are read, so a forged assistant turn cannot inject a manifest through either
    carrier. Nothing parsed here is trusted for authorisation: the ids are only
    useful through ``read_attachment``, which resolves them against an
    owner-scoped store.

    Returns a single item, or none — the model wants one list of the files it
    can read, not one section per message.
    """
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
    """The raw refs one message carries, from whichever carrier holds them.

    ``metadata`` first, and the top-level field only when ``metadata`` has no
    ``attachments`` key at all. The **key** decides, not whether its value
    parses: a message whose ``metadata`` names attachments has said where its
    refs are, and a value there that does not parse degrades to no refs for that
    message, exactly as a malformed top-level value always has, rather than
    reaching past it for a second copy. Only a message carrying both is affected,
    and the web component sends one or the other, never both -- the array moved
    in a single release -- so a message that has both was not shaped by it, and
    falling back would mean assembling one message's refs from two sources that
    disagree.

    Each half of the guard is held by its own test, because an ``and``-chain is
    one branch arc and coverage stays at 100% with either deleted. Without the
    ``isinstance``, a message with no ``metadata`` -- what every web component
    release up to 0.40 posts -- raises on ``in None``, and
    ``test_the_top_level_field_is_read_when_there_is_no_metadata`` fails. Without
    the key test, a ``metadata`` carrying something else hides the top-level
    refs, and ``test_metadata_without_attachments_falls_back_to_the_top_level_field``
    fails.
    """
    metadata = getattr(message, "metadata", None)
    if isinstance(metadata, Mapping) and _REFS_KEY in metadata:
        return metadata[_REFS_KEY]
    return (getattr(message, "model_extra", None) or {}).get(_REFS_KEY)


@dataclass(frozen=True)
class _AttachmentMention:
    """One attachment ref as it survived a permissive parse.

    Deliberately **not** an ``AttachmentRef``: that record describes a file the
    server issued and every field of it is known good, whereas here every field
    but ``id`` may be missing or wrong-typed. Sharing the server-side type would
    mean widening a trusted record to accommodate untrusted input.
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
