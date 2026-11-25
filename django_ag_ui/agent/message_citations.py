from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ag_ui.core import AssistantMessage, Message
from pydantic import TypeAdapter
from pydantic_ai.messages import Citation, ModelResponse, TextPart

CITATIONS_FIELD = "citations"
"""The ``metadata`` key an assistant message's citations ride under, streamed and stored.

The value is a list of pydantic-ai's ``Citation`` in its JSON form. An anchor's
``start`` and ``end`` count UTF-16 code units into the message's whole
``content``, so a JavaScript client's ``content.slice(start, end)`` is the range.
"""

_CITATION = TypeAdapter(Citation)


def _utf16(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def message_citations(content: str, at: int, citations: Sequence[Citation]) -> list[dict[str, Any]]:
    """``citations`` of a part whose text starts at code point ``at`` of ``content``."""
    out: list[dict[str, Any]] = []
    for citation in citations:
        dumped = _CITATION.dump_python(citation, mode="json")
        if citation.anchor is not None:
            dumped["anchor"]["start"] = _utf16(content[: at + citation.anchor.start])
            dumped["anchor"]["end"] = _utf16(content[: at + citation.anchor.end])
        out.append(dumped)
    return out


def stamp_citations(message: Any, citations: list[dict[str, Any]]) -> Any:
    """A copy of ``message`` carrying ``citations`` in its ``metadata``, or itself when none."""
    if not citations:
        return message
    metadata = {**(message.metadata or {}), CITATIONS_FIELD: citations}
    return message.model_copy(update={"metadata": metadata})


def with_dumped_citations(response: ModelResponse, dumped: list[Message]) -> list[Message]:
    """``dumped``, each assistant message stamped with the citations of the parts it joined."""
    texts = [part for part in response.parts if isinstance(part, TextPart)]
    if not any(part.citations for part in texts):
        return dumped
    out: list[Message] = []
    taken = 0
    for message in dumped:
        if not isinstance(message, AssistantMessage) or message.content is None:
            out.append(message)
            continue
        content, at, cited = message.content, None, []
        while taken < len(texts):
            text = texts[taken].content
            if at is None and content.startswith(text):
                start = 0
            elif at is not None and content.startswith(text, at):
                start = at
            else:
                break
            cited += message_citations(content, start, texts[taken].citations or [])
            at, taken = start + len(text), taken + 1
        if at != len(content):
            return dumped
        out.append(stamp_citations(message, cited))
    return out if all(not part.content for part in texts[taken:]) else dumped


__all__ = ["CITATIONS_FIELD", "message_citations", "stamp_citations", "with_dumped_citations"]
