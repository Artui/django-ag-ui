from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)

from django_ag_ui.agent.continued_history import continued_history
from tests.agent.test_continued_history import _answer, _run, _snapshot, _user, _wire

_NOTHING_ON_THE_WIRE = [
    pytest.param([], id="empty"),
    pytest.param([CachePoint()], id="a cache point only"),
]


def _empty_turn(content: list[Any]) -> list[ModelMessage]:
    return [
        ModelRequest(parts=[UserPromptPart(content=content)]),
        ModelResponse(parts=[TextPart("b")]),
    ]


@pytest.mark.parametrize("content", _NOTHING_ON_THE_WIRE)
async def test_a_thread_with_a_stored_empty_turn_continues(content: list[Any]) -> None:
    history = _empty_turn(content)

    sent, row = await _run(history, [_user("u2", "c")])

    assert sent[: len(history)] == history
    assert [message["content"] for message in row[-2:]] == ["c", "the new answer"]


@pytest.mark.parametrize("content", _NOTHING_ON_THE_WIRE)
def test_an_empty_turn_does_not_stop_a_later_turn_being_cut_at(content: list[Any]) -> None:
    earlier = _empty_turn(content)
    history = [*earlier, *_snapshot(_user("u2", "c"), _answer("a2", "d"))]

    kept, _ = continued_history(history, _wire(_user("u2", "c, edited")))

    assert kept == earlier
