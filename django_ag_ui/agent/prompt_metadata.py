from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ag_ui.core import Message, UserMessage
from pydantic_ai.messages import ModelMessage, ModelRequest, TextContent, UserPromptPart


def _part_metadata(part: UserPromptPart) -> dict[str, Any] | None:
    """The metadata of the part's first ``TextContent`` that carries one."""
    content = [part.content] if isinstance(part.content, str) else part.content
    return next(
        (
            dict(item.metadata)
            for item in content
            if isinstance(item, TextContent) and item.metadata
        ),
        None,
    )


def _prompt_marks(messages: Sequence[ModelMessage]) -> list[dict[str, Any] | None]:
    """Each user prompt part's metadata, in the order a dump writes their messages."""
    return [
        _part_metadata(part)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]


def with_prompt_metadata(messages: Sequence[ModelMessage], dumped: list[Message]) -> list[Message]:
    """``dumped``, each user message carrying its prompt part's metadata, which upstream drops."""
    marks = _prompt_marks(messages)
    users = [message for message in dumped if isinstance(message, UserMessage)]
    if len(users) != len(marks) or not any(marks):
        return dumped
    by_id = {id(user): mark for user, mark in zip(users, marks, strict=True)}
    return [_stamped(message, by_id.get(id(message))) for message in dumped]


def stamp_enqueued_content(
    messages: Sequence[ModelMessage], content: dict[str, Any]
) -> dict[str, Any]:
    """An enqueued-messages activity's ``content``, each user entry carrying its part's metadata."""
    marks = _prompt_marks(messages)
    entries = content.get("messages") or []
    users = [entry for entry in entries if entry.get("role") == "user"]
    if len(users) != len(marks) or not any(marks):
        return content
    by_id = {id(user): mark for user, mark in zip(users, marks, strict=True)}
    return {
        **content,
        "messages": [_stamped_entry(entry, by_id.get(id(entry))) for entry in entries],
    }


def _stamped(message: Message, mark: dict[str, Any] | None) -> Message:
    if mark is None:
        return message
    return message.model_copy(update={"metadata": {**(message.metadata or {}), **mark}})


def _stamped_entry(entry: dict[str, Any], mark: dict[str, Any] | None) -> dict[str, Any]:
    if mark is None:
        return entry
    return {**entry, "metadata": {**(entry.get("metadata") or {}), **mark}}


__all__ = ["stamp_enqueued_content", "with_prompt_metadata"]
