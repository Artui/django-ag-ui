from __future__ import annotations

from uuid import uuid4

from ag_ui.core import AssistantMessage, Message
from pydantic_ai.messages import ModelResponse, TextPart


def split_joined_texts(response: ModelResponse, dumped: list[Message]) -> list[Message]:
    """``dumped``, each text the dump joined across a dropped part split back as it streamed."""
    texts = [
        (index, part.content)
        for index, part in enumerate(response.parts)
        if isinstance(part, TextPart)
    ]
    out: list[Message] = []
    taken = 0
    for message in dumped:
        if not isinstance(message, AssistantMessage) or message.content is None:
            out.append(message)
            continue
        content, at, previous, cuts = message.content, 0, None, []
        while taken < len(texts):
            index, text = texts[taken]
            if previous is None and content.startswith(text):
                start = 0
            elif previous == index - 1 and content.startswith(text, at):
                start = at
            elif previous is not None and content.startswith("\n" + text, at):
                start = at + 1
                cuts.append(start)
            else:
                break
            at, previous, taken = start + len(text), index, taken + 1
        if at != len(content):
            return dumped
        out.extend(_pieces(message, cuts))
    return out


def _pieces(message: AssistantMessage, cuts: list[int]) -> list[Message]:
    """``message`` cut where each piece starts; the last piece keeps its id and tool calls."""
    content = message.content or ""
    starts = [0, *cuts]
    earlier = [
        AssistantMessage(id=str(uuid4()), role="assistant", content=content[start : end - 1])
        for start, end in zip(starts, cuts, strict=False)
    ]
    return [*earlier, message.model_copy(update={"content": content[starts[-1] :]})]


__all__ = ["split_joined_texts"]
