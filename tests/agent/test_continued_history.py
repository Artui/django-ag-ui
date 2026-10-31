"""``continued_history``, and the parity it exists to keep with the adapter.

The adapter decides the cut of a server-held run in private helpers; this
module's tests hold the mirror to the run: the same inputs through a model that
records what it was sent, and through the store, must agree.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from ag_ui.core import Message
from django.test import RequestFactory
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.policy.audit.null_audit_logger import NullAuditLogger
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.agent_session import AgentSession
from django_ag_ui.agent.continued_history import continued_history
from django_ag_ui.config.build_ag_ui_config import build_ag_ui_config
from django_ag_ui.persistence.utils import messages_from_jsonable


def _wire(*messages: dict[str, Any]) -> list[Message]:
    return messages_from_jsonable(list(messages))


def _user(message_id: str, content: Any) -> dict[str, Any]:
    return {"id": message_id, "role": "user", "content": content}


def _answer(message_id: str, content: str) -> dict[str, Any]:
    return {"id": message_id, "role": "assistant", "content": content}


def _calling(message_id: str, call_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "role": "assistant",
        "toolCalls": [
            {"id": call_id, "type": "function", "function": {"name": "look", "arguments": "{}"}}
        ],
    }


def _result(message_id: str, call_id: str, content: str = "seen") -> dict[str, Any]:
    return {"id": message_id, "role": "tool", "toolCallId": call_id, "content": content}


def _reasoning(message_id: str, content: str) -> dict[str, Any]:
    return {"id": message_id, "role": "reasoning", "content": content}


def _snapshot(*messages: dict[str, Any]) -> list[ModelMessage]:
    """A snapshot as a prior run leaves it: loaded from the client, so it carries its ids."""
    return AGUIAdapter.load_messages(_wire(*messages))


def _ids(messages: list[Message]) -> list[str]:
    return [message.id for message in messages]


# --- The new turn ------------------------------------------------------------


def test_the_new_turn_is_what_follows_the_last_response() -> None:
    history = _snapshot(_user("u1", "a"), _answer("a1", "b"))
    posted = _wire(_user("u1", "a"), _answer("a1", "b"), _user("u2", "c"))

    kept, new_turn = continued_history(history, posted)

    assert kept == history
    assert _ids(new_turn) == ["u2"]


def test_a_post_with_no_response_is_all_new_turn() -> None:
    history = _snapshot(_user("u1", "a"), _answer("a1", "b"))

    kept, new_turn = continued_history(history, _wire(_user("u2", "c")))

    assert kept == history
    assert _ids(new_turn) == ["u2"]


def test_a_post_that_ends_on_a_response_has_no_new_turn() -> None:
    history = _snapshot(_user("u1", "a"), _answer("a1", "b"))

    kept, new_turn = continued_history(history, _wire(_user("u1", "a"), _answer("a1", "b")))

    assert kept == history
    assert new_turn == []


def test_a_tool_result_after_the_last_response_is_part_of_the_new_turn() -> None:
    history = _snapshot(_user("u1", "a"), _calling("a1", "call-1"))

    _, new_turn = continued_history(
        history, _wire(_calling("a1", "call-1"), _result("t1", "call-1"))
    )

    assert _ids(new_turn) == ["t1"]


def test_a_reasoning_message_is_a_response() -> None:
    history = _snapshot(_user("u1", "a"), _reasoning("r1", "thinking"))
    posted = _wire(_user("u1", "a"), _reasoning("r1", "thinking"), _user("u2", "c"))

    _, new_turn = continued_history(history, posted)

    assert _ids(new_turn) == ["u2"]


# --- The cut -----------------------------------------------------------------


def test_an_edited_message_cuts_the_history_before_its_turn() -> None:
    history = _snapshot(_user("u1", "a"), _answer("a1", "b"), _user("u2", "c"), _answer("a2", "d"))
    posted = _wire(_user("u1", "a"), _answer("a1", "b"), _user("u2", "c, edited"))

    kept, new_turn = continued_history(history, posted)

    assert kept == history[:2]
    assert [message.content for message in new_turn] == ["c, edited"]


def test_the_turn_is_named_by_its_last_message() -> None:
    # Everything after the last response loads into one request, which keeps
    # the id of the last message folded into it, so a tool result posted ahead
    # of the edited prompt does not hide the prompt's id.
    history = _snapshot(
        _user("u1", "a"),
        _calling("a1", "call-1"),
        _result("t1", "call-1"),
        _user("u2", "c"),
        _answer("a2", "d"),
    )
    posted = _wire(
        _user("u1", "a"),
        _calling("a1", "call-1"),
        _result("t1", "call-1"),
        _user("u2", "c, edited"),
    )

    kept, new_turn = continued_history(history, posted)

    # The cut request's tool result stays, as the server recorded it; the
    # client's copy of it is not stored a second time.
    assert kept[:2] == history[:2]
    (results,) = kept[2:]
    assert isinstance(results, ModelRequest)
    assert [type(part) for part in results.parts] == [ToolReturnPart]
    assert _ids(new_turn) == ["u2"]


def test_only_the_client_copy_of_a_kept_result_is_left_out() -> None:
    # A result for a call the kept request does not answer is stored as
    # posted, even when it rides beside a resubmission.
    history = _snapshot(
        _user("u1", "a"),
        _calling("a1", "call-1"),
        _result("t1", "call-1"),
        _user("u2", "c"),
        _answer("a2", "d"),
    )
    posted = _wire(
        _calling("a1", "call-1"),
        _result("t1", "call-1"),
        _result("stale", "call-9"),
        _user("u2", "c, edited"),
    )

    _, new_turn = continued_history(history, posted)

    assert _ids(new_turn) == ["stale", "u2"]


def test_a_message_that_loads_nothing_does_not_name_the_turn() -> None:
    history = _snapshot(_user("u1", "a"), _answer("a1", "b"))
    posted = _wire(_user("u1", "a, edited"), _user("empty", []))

    kept, new_turn = continued_history(history, posted)

    assert kept == []
    assert _ids(new_turn) == ["u1", "empty"]


def test_a_turn_of_messages_that_load_nothing_cuts_nothing() -> None:
    history = _snapshot(_user("u1", "a"), _answer("a1", "b"))

    kept, _ = continued_history(history, _wire(_user("u1", [])))

    assert kept == history


def test_only_a_stored_user_turn_is_cut_at() -> None:
    # A stored request that holds no user prompt cannot be resubmitted, even
    # when the client knows its id; nor can a response.
    history = _snapshot(
        _user("u1", "a"), _calling("a1", "call-1"), _result("t1", "call-1"), _answer("a2", "b")
    )

    for named in ("t1", "a2"):
        kept, _ = continued_history(history, _wire(_user(named, "c")))
        assert kept == history


def test_a_legacy_retry_bound_to_a_tool_is_kept_as_a_result() -> None:
    retry = RetryPromptPart(tool_name="look", content="again", tool_call_id="call-1")  # ty: ignore[deprecated]
    loose = RetryPromptPart(content="answer differently")  # ty: ignore[deprecated]
    turn = ModelRequest(parts=[retry, loose, UserPromptPart(content="c")])
    named = AGUIAdapter.load_messages(_wire(_user("u2", "c")))[0]
    turn.metadata = named.metadata
    history = [
        ModelRequest(parts=[UserPromptPart(content="a")]),
        ModelResponse(parts=[ToolCallPart(tool_name="look", tool_call_id="call-1")]),
        turn,
    ]

    kept, _ = continued_history(history, _wire(_user("u2", "c, edited")))

    assert kept[:2] == history[:2]
    assert kept[2].parts == [retry]


def test_a_stale_result_does_not_name_the_turn() -> None:
    # The adapter drops a result for a call that is neither posted nor pending
    # before it loads the turn, so it is the resubmitted prompt that names it.
    history = _snapshot(_user("u1", "a"), _answer("a1", "b"))

    kept, new_turn = continued_history(
        history, _wire(_user("u1", "a, edited"), _result("stale", "call-9"))
    )

    assert kept == []
    assert _ids(new_turn) == ["u1", "stale"]


def test_a_result_for_a_pending_call_still_names_the_turn() -> None:
    # A call the stored history waits on is answered by the result, so the
    # result is part of the turn and, being last, names it: nothing is cut.
    history = _snapshot(_user("u1", "a"), _calling("a1", "call-1"))

    kept, new_turn = continued_history(
        history, _wire(_user("u1", "a, edited"), _result("t1", "call-1"))
    )

    assert kept == history
    assert _ids(new_turn) == ["u1", "t1"]


def test_a_result_for_a_posted_call_names_the_turn_though_its_call_is_answered() -> None:
    # The call is posted, so the adapter does not drop the result as stale;
    # being last, the result names the turn and the prompt before it is not
    # resubmitted (the adapter sends the model both prompts here).
    history = _snapshot(
        _user("u1", "a"),
        _calling("a1", "call-1"),
        _result("t1", "call-1"),
        _user("u2", "c"),
        _answer("a2", "d"),
    )
    posted = _wire(_calling("a1", "call-1"), _user("u2", "c, edited"), _result("t1", "call-1"))

    kept, new_turn = continued_history(history, posted)

    assert kept == history
    assert _ids(new_turn) == ["u2"]


def test_an_answered_result_resent_without_a_cut_is_left_out() -> None:
    history = _snapshot(
        _user("u1", "a"), _calling("a1", "call-1"), _result("t1", "call-1"), _answer("a2", "d")
    )
    posted = _wire(
        _user("u1", "a"), _calling("a1", "call-1"), _result("t1", "call-1"), _user("u2", "next")
    )

    kept, new_turn = continued_history(history, posted)

    assert kept == history
    assert _ids(new_turn) == ["u2"]


# --- Parity with the run ------------------------------------------------------


class _Store:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    async def save(self, conversation: Any, *, request: Any) -> None:
        self.saved.append(conversation)

    async def load(self, thread_id: str, *, request: Any) -> Any:
        return None

    async def exists(self, thread_id: str, *, request: Any) -> bool:
        return False


async def _run(history: list[ModelMessage], posted: list[dict[str, Any]]) -> tuple[Any, Any]:
    """What the model was sent, and the row stored, for one resumed run."""
    seen: list[list[ModelMessage]] = []

    async def stream(messages: list[ModelMessage], info: Any) -> Any:
        seen.append(list(messages))
        yield "the new answer"

    store = _Store()
    run_input = AGUIAdapter.build_run_input(
        json.dumps(
            {
                "threadId": "t1",
                "runId": "r2",
                "messages": posted,
                "tools": [],
                "context": [],
                "state": None,
                "forwardedProps": None,
            }
        ).encode()
    )
    session = AgentSession(
        Agent(FunctionModel(stream_function=stream)),
        run_input,
        RequestFactory().post("/agent/"),
        deps=AgentDeps(user=None),
        audit_logger=NullAuditLogger(),
        config=build_ag_ui_config(),
        conversation_store=store,
        message_history=history,
    )
    _ = [chunk async for chunk in session.stream()]
    (conversation,) = store.saved
    return seen[0], conversation.messages


def _visible(messages: list[ModelMessage]) -> list[tuple[Any, ...]]:
    """What a model is shown of ``messages``, part by part, without the message boundaries."""
    out: list[tuple[Any, ...]] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, UserPromptPart):
                out.append(("user", part.content))
            elif isinstance(part, TextPart):
                out.append(("text", part.content))
            elif isinstance(part, ThinkingPart):
                out.append(("thinking", part.content))
            elif isinstance(part, ToolCallPart):
                out.append(("call", part.tool_call_id, part.tool_name))
            elif isinstance(part, ToolReturnPart):
                out.append(("return", part.tool_call_id, part.content))
    return out


def _stored_prior(row: list[dict[str, Any]]) -> list[ModelMessage]:
    """The row up to this run's own answer, loaded as the model would be sent it."""
    assert row[-1]["content"] == "the new answer"
    return AGUIAdapter.load_messages(messages_from_jsonable(row[:-1]))


_TOOL_ROUND = (
    _user("u1", "a"),
    _calling("a1", "call-1"),
    _result("t1", "call-1"),
    _user("u2", "c"),
    _answer("a2", "d"),
)

_PARITY_CASES = {
    "a replayed transcript": (
        (_user("u1", "a"), _answer("a1", "b")),
        [_user("u1", "a"), _answer("a1", "b"), _user("u2", "c")],
    ),
    "the new turn alone": ((_user("u1", "a"), _answer("a1", "b")), [_user("u2", "c")]),
    "an edited later message": (
        (_user("u1", "a"), _answer("a1", "b"), _user("u2", "c"), _answer("a2", "d")),
        [_user("u1", "a"), _answer("a1", "b"), _user("u2", "c, edited")],
    ),
    "an edited first message": (
        (_user("u1", "a"), _answer("a1", "b")),
        [_user("u1", "a, edited")],
    ),
    "a resubmitted message": (
        (_user("u1", "a"), _answer("a1", "b")),
        [_user("u1", "a")],
    ),
    "an edit after a tool round": (
        _TOOL_ROUND,
        [*_TOOL_ROUND[:3], _user("u2", "c, edited")],
    ),
    "a reasoning message last": (
        (_user("u1", "a"), _reasoning("r1", "thinking")),
        [_user("u1", "a"), _reasoning("r1", "thinking"), _user("u2", "c")],
    ),
}


@pytest.mark.parametrize("case", list(_PARITY_CASES))
async def test_the_stored_row_reads_the_way_the_run_did(case: str) -> None:
    snapshot, posted = _PARITY_CASES[case]

    sent, row = await _run(_snapshot(*snapshot), posted)

    assert _visible(_stored_prior(row)) == _visible(sent)


async def test_a_tool_result_for_no_pending_call_is_stored_as_posted() -> None:
    # The adapter drops it, with a warning; the row keeps what the client sent.
    posted = [_user("u1", "a"), _answer("a1", "b"), _result("stale", "call-9"), _user("u2", "c")]

    with pytest.warns(UserWarning, match="dropped"):
        sent, row = await _run(_snapshot(_user("u1", "a"), _answer("a1", "b")), posted)

    assert ("return", "call-9", "seen") not in _visible(sent)
    assert [message["id"] for message in row[:-1]] == ["u1", "a1", "stale", "u2"]


async def test_a_client_system_message_is_stored_as_posted() -> None:
    posted = [{"id": "s1", "role": "system", "content": "client rules"}, _user("u2", "c")]

    sent, row = await _run(_snapshot(_user("u1", "a"), _answer("a1", "b")), posted)

    assert "client rules" not in json.dumps([str(message) for message in sent])
    assert [message["id"] for message in row[:-1]] == ["u1", "a1", "s1", "u2"]


async def test_a_client_compaction_part_is_stored_as_posted() -> None:
    compaction = {
        "id": "c1",
        "role": "activity",
        "activityType": "pydantic_ai_compaction",
        "content": {"content": "client summary"},
    }

    sent, row = await _run(
        _snapshot(_user("u1", "a"), _answer("a1", "b")), [compaction, _user("u2", "c")]
    )

    assert "client summary" not in json.dumps([str(message) for message in sent])
    assert [message["id"] for message in row[:-1]] == ["u1", "a1", "c1", "u2"]


@pytest.mark.parametrize(
    "posted, shown, row_ids",
    [
        pytest.param(
            [_user("u1", "a, edited"), _result("stale", "call-9")],
            [("user", "a, edited")],
            ["u1", "stale"],
            id="an edited first message",
        ),
        pytest.param(
            [
                _user("u1", "a"),
                _answer("a1", "b"),
                _user("u2", "c, edited"),
                _result("stale", "call-9"),
            ],
            [("user", "a"), ("text", "b"), ("user", "c, edited")],
            ["u1", "a1", "u2", "stale"],
            id="an edited later message",
        ),
    ],
)
async def test_a_stale_result_posted_after_a_resubmission_still_cuts(
    posted: list[dict[str, Any]], shown: list[tuple[Any, ...]], row_ids: list[str]
) -> None:
    # The adapter drops the stale result before it names the turn, so the
    # resubmitted prompt does and the history is cut; the row keeps the result
    # as posted but holds each message once.
    snapshot = (_user("u1", "a"), _answer("a1", "b"), _user("u2", "c"), _answer("a2", "d"))

    with pytest.warns(UserWarning, match="dropped"):
        sent, row = await _run(_snapshot(*snapshot), posted)

    assert _visible(sent) == shown
    assert [message["id"] for message in row[:-1]] == row_ids


async def test_an_answered_result_resent_without_a_cut_is_stored_once() -> None:
    # The adapter drops the client's copy of a result the stored history
    # already answers; storing it would hold one id twice.
    snapshot = (
        _user("u1", "a"),
        _calling("a1", "call-1"),
        _result("t1", "call-1"),
        _answer("a2", "d"),
    )
    posted = [
        _user("u1", "a"),
        _calling("a1", "call-1"),
        _result("t1", "call-1"),
        _user("u2", "next"),
    ]

    with pytest.warns(UserWarning, match="dropped"):
        sent, row = await _run(_snapshot(*snapshot), posted)

    assert _visible(sent).count(("return", "call-1", "seen")) == 1
    ids = [message["id"] for message in row]
    assert len(ids) == len(set(ids))
    assert ids[:-1] == ["u1", "a1", "t1", "a2", "u2"]
