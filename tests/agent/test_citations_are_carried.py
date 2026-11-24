from __future__ import annotations

from typing import Any

import pytest
from ag_ui.core import AssistantMessage, ToolMessage
from pydantic_ai import Agent, BinaryContent
from pydantic_ai.messages import (
    Citation,
    ContentCitationAnchor,
    DocumentCitationSource,
    FilePart,
    MarkerCitationAnchor,
    ModelResponse,
    TextPart,
    WebCitationSource,
)
from pydantic_ai.models import function as function_module
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from django_ag_ui.agent.message_citations import CITATIONS_FIELD, with_dumped_citations
from tests.agent.test_stored_ids_match_the_stream import (
    USER_TURN,
    _after_a_tool_return,
    _new,
    _run,
    _session,
    _Store,
)

ASTRAL = "\U0001d400"
SKY = WebCitationSource(url="https://example.com/sky", title="Sky")


@pytest.fixture(autouse=True)
def _whole_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    original = function_module.FunctionStreamedResponse._get_event_iterator

    async def _one(item: Any) -> Any:
        yield item

    async def _iterator(self: Any) -> Any:
        async for item in self._iter:
            if isinstance(item, TextPart | FilePart):
                yield self._parts_manager.handle_part(vendor_part_id=None, part=item)
            else:
                self._iter = _one(item)
                async for event in original(self):
                    yield event

    monkeypatch.setattr(function_module.FunctionStreamedResponse, "_get_event_iterator", _iterator)


def _cited(content: str, phrase: str, **kw: Any) -> TextPart:
    start = content.index(phrase)
    anchor = ContentCitationAnchor(start=start, end=start + len(phrase))
    return TextPart(content=content, citations=[Citation(sources=[SKY], anchor=anchor)], **kw)


def _agent(*first: Any) -> Agent[None, Any]:
    async def stream_fn(messages: list, info: Any) -> Any:
        if _after_a_tool_return(messages):
            yield TextPart(content="done")
            return
        for item in first:
            yield item

    agent: Agent[None, Any] = Agent(FunctionModel(stream_function=stream_fn))

    @agent.tool_plain
    def lookup() -> str:
        return "ok"

    return agent


def _slice(content: str, citation: dict[str, Any]) -> str:
    units = content.encode("utf-16-le")
    anchor = citation["anchor"]
    return units[2 * anchor["start"] : 2 * anchor["end"]].decode("utf-16-le")


def _streamed(frames: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    texts: dict[str, str] = {}
    ends: list[tuple[str, list[dict[str, Any]]]] = []
    for frame in frames:
        if frame["type"] == "TEXT_MESSAGE_CONTENT":
            texts[frame["messageId"]] = texts.get(frame["messageId"], "") + frame["delta"]
        if frame["type"] == "TEXT_MESSAGE_END" and frame["messageId"] in texts:
            cited = (frame.get("metadata") or {}).get(CITATIONS_FIELD, [])
            ends.append((texts[frame["messageId"]], cited))
    return ends


def _stored(conversation: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (m["content"], (m.get("metadata") or {}).get(CITATIONS_FIELD, []))
        for m in _new(conversation, after=1)
        if m["role"] == "assistant" and m.get("content")
    ]


async def test_adjacent_parts_cite_into_the_one_message_they_stream_and_store() -> None:
    store = _Store()

    frames = await _run(
        _session(_agent(TextPart(content=f"{ASTRAL} sky: "), _cited("it is blue", "blue")), store)
    )

    for content, (citation,) in [*_streamed(frames), *_stored(store.saved[0])]:
        assert content == f"{ASTRAL} sky: it is blue"
        assert _slice(content, citation) == "blue"
        assert citation["sources"] == [
            {"kind": "web", "url": "https://example.com/sky", "title": "Sky", "excerpts": []}
        ]
        assert citation["anchor"]["kind"] == "content"


async def test_text_either_side_of_a_file_cites_into_the_text_each_copy_holds() -> None:
    store = _Store()
    file = FilePart(content=BinaryContent(data=b"png", media_type="image/png"))

    frames = await _run(_session(_agent(_cited("B is", "is"), file, _cited("C too", "too")), store))

    assert [(text, [_slice(text, c) for c in cited]) for text, cited in _streamed(frames)] == [
        ("B is", ["is"]),
        ("C too", ["too"]),
    ]
    ((content, cited),) = _stored(store.saved[0])
    assert (content, [_slice(content, c) for c in cited]) == ("B is\nC too", ["is", "too"])


async def test_text_after_a_tool_call_cites_from_the_start_of_its_own_message() -> None:
    store = _Store()
    call = {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id="c1")}

    frames = await _run(
        _session(_agent(_cited("first part", "part"), call, _cited("next", "next")), store)
    )

    expected = [("first part", ["part"]), ("next", ["next"])]
    for copy in (_streamed(frames), _stored(store.saved[0])):
        assert [(text, [_slice(text, c) for c in cited]) for text, cited in copy][:2] == expected


async def test_an_answer_without_citations_carries_no_citations_key() -> None:
    store = _Store()

    frames = await _run(_session(_agent(TextPart(content="plain")), store))

    assert all(CITATIONS_FIELD not in (f.get("metadata") or {}) for f in frames)
    assert CITATIONS_FIELD not in str(store.saved[0].messages)


async def test_a_posted_back_cited_answer_keeps_its_citations_in_the_next_save() -> None:
    store = _Store()
    await _run(_session(_agent(_cited("it is blue", "blue")), store))
    first = store.saved[0].messages

    await _run(
        _session(
            _agent(TextPart(content="again")),
            store,
            messages=[*first, {"id": "u2", "role": "user", "content": "more?"}],
        )
    )

    (kept,) = [m for m in _stored(store.saved[1]) if m[0] == "it is blue"]
    assert kept == _stored(store.saved[0])[0]
    assert [_slice(kept[0], c) for c in kept[1]] == ["blue"]
    assert store.saved[1].messages[0] == USER_TURN


def test_a_citation_without_an_anchor_or_with_a_marker_keeps_its_form() -> None:
    doc = DocumentCitationSource(title="Doc")
    part = TextPart(
        content="see [1]",
        citations=[
            Citation(sources=[doc]),
            Citation(sources=[doc], anchor=MarkerCitationAnchor(start=4, end=7)),
        ],
    )
    dumped = AssistantMessage(id="a", role="assistant", content="see [1]")

    (stamped,) = with_dumped_citations(ModelResponse(parts=[part]), [dumped])

    unanchored, marker = stamped.metadata[CITATIONS_FIELD]
    assert unanchored["anchor"] is None
    assert unanchored["sources"][0] == {
        "kind": "document",
        "document_id": None,
        "title": "Doc",
        "excerpts": [],
    }
    assert marker["anchor"] == {"kind": "marker", "start": 4, "end": 7}


def test_a_repeated_part_cites_where_it_follows_not_where_it_first_matches() -> None:
    response = ModelResponse(parts=[_cited("ab", "b"), _cited("ab", "b")])
    dumped = [AssistantMessage(id="a", role="assistant", content="abab")]

    (stamped,) = with_dumped_citations(response, dumped)

    assert [c["anchor"]["start"] for c in stamped.metadata[CITATIONS_FIELD]] == [1, 3]


def test_a_dump_the_parts_do_not_spell_is_left_unstamped() -> None:
    response = ModelResponse(parts=[_cited("it is blue", "blue")])
    tool = ToolMessage(id="t", role="tool", content="ok", tool_call_id="c")
    for dumped in (
        [tool, AssistantMessage(id="a", role="assistant", content="other words")],
        [AssistantMessage(id="a", role="assistant", content="it is blue and more")],
        [AssistantMessage(id="a", role="assistant", content="it is")],
    ):
        assert with_dumped_citations(response, dumped) == dumped


def test_parts_the_dump_never_reached_leave_it_unstamped() -> None:
    response = ModelResponse(parts=[_cited("one", "one"), TextPart(content="two")])
    dumped = [AssistantMessage(id="a", role="assistant", content="one")]

    assert with_dumped_citations(response, dumped) == dumped
