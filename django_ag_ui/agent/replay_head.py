from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django_spec_agents.persistence.types.run_log_entry import RunLogEntry

_TERMINAL = frozenset({"RUN_FINISHED", "RUN_ERROR"})

# Each opener the verifier tracks, the field naming its entity, and what closes it.
_OPENERS = {
    "TEXT_MESSAGE_START": ("messageId", ("TEXT_MESSAGE_END",)),
    "TOOL_CALL_START": ("toolCallId", ("TOOL_CALL_END",)),
    "REASONING_START": ("messageId", ("REASONING_END",)),
    "REASONING_MESSAGE_START": ("messageId", ("REASONING_MESSAGE_END",)),
    "SUBAGENT_STARTED": ("subagentRunId", ("SUBAGENT_FINISHED", "SUBAGENT_ERROR")),
    "STEP_STARTED": ("stepName", ("STEP_FINISHED",)),
}
_CLOSERS = {closer: opener for opener, (_, closers) in _OPENERS.items() for closer in closers}


def replay_head(
    log: Sequence[RunLogEntry], stored: Sequence[dict[str, Any]], *, after: int
) -> tuple[list[dict[str, Any]], int]:
    """The events opening a replay from ``after``, and the last seq they stand for."""
    if not log:
        return [], 0
    terminal = next((e.seq for e in log if e.event.get("type") in _TERMINAL), None)
    cut = after if terminal is None else min(after, terminal - 1)
    cut = max(min(cut, log[-1].seq), log[0].seq)
    whole, prefix = _Fold(), _Fold()
    for entry in log[1:]:
        whole.add(entry.event)
        if entry.seq <= cut:
            prefix.add(entry.event)
    first = log[0].event
    echoed = (first.get("input") or {}).get("messages") or []
    prior = [m for m in [*stored, *echoed] if m.get("id") not in whole.ids()]
    snapshot = {"type": "MESSAGES_SNAPSHOT", "messages": _unique([*prior, *prefix.messages])}
    return [first, snapshot, *prefix.reopen()], cut


class _Fold:
    """The client reducer's view of a run's events: its messages and what is still open."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._open: dict[tuple[Any, Any], tuple[int, dict[str, Any]]] = {}
        self._seen = 0

    def ids(self) -> set[str]:
        return {m["id"] for m in self.messages}

    def add(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        self._seen += 1
        if kind in _OPENERS:
            self._open[(kind, _key(kind, event))] = (self._seen, event)
        elif kind in _CLOSERS:
            opener = _CLOSERS[kind]
            self._open.pop((opener, _key(opener, event)), None)
        self._reduce(kind, event)

    def reopen(self) -> list[dict[str, Any]]:
        """Each open entity's opener, in log order; a tool call's carries its arguments so far."""
        out: list[dict[str, Any]] = []
        for _, event in sorted(self._open.values(), key=lambda opened: opened[0]):
            out.append(event)
            if event["type"] == "TOOL_CALL_START":
                args = self._call(event["toolCallId"])["function"]["arguments"]
                if args:
                    out.append(_args(event, args))
        return out

    def _reduce(self, kind: Any, event: dict[str, Any]) -> None:
        if kind == "TEXT_MESSAGE_START":
            self._message(event["messageId"], event.get("role") or "assistant", event)
        elif kind == "TEXT_MESSAGE_CONTENT" or kind == "REASONING_MESSAGE_CONTENT":
            self._by_id(event["messageId"])["content"] += event["delta"]
        elif kind == "REASONING_MESSAGE_START":
            self._message(event["messageId"], "reasoning", event)
        elif kind == "TOOL_CALL_START":
            self._start_call(event)
        elif kind == "TOOL_CALL_ARGS":
            self._call(event["toolCallId"])["function"]["arguments"] += event["delta"]
        elif kind == "TOOL_CALL_RESULT":
            self._result(event)

    def _message(self, message_id: str, role: str, event: dict[str, Any]) -> None:
        self.messages.append(_tag({"id": message_id, "role": role, "content": ""}, event))

    def _start_call(self, event: dict[str, Any]) -> None:
        call_id = event["toolCallId"]
        parent_id = event.get("parentMessageId")
        parent = next((m for m in self.messages if m["id"] == parent_id), None)
        if parent is None or parent["role"] != "assistant":
            fallback = call_id if parent is not None or parent_id is None else parent_id
            parent = _tag({"id": fallback, "role": "assistant", "toolCalls": []}, event)
            self.messages.append(parent)
        call = {"id": call_id, "type": "function"}
        call["function"] = {"name": event["toolCallName"], "arguments": ""}
        parent.setdefault("toolCalls", []).append(call)

    def _result(self, event: dict[str, Any]) -> None:
        message = {
            "id": event["messageId"],
            "toolCallId": event["toolCallId"],
            "role": event.get("role") or "tool",
            "content": event["content"],
        }
        self.messages.append(_tag(message, event))

    def _by_id(self, message_id: str) -> dict[str, Any]:
        return next(m for m in self.messages if m["id"] == message_id)

    def _call(self, call_id: str) -> dict[str, Any]:
        calls = (c for m in self.messages for c in m.get("toolCalls", ()))
        return next(c for c in calls if c["id"] == call_id)


def _key(opener: str, event: dict[str, Any]) -> Any:
    """What the verifier keys an entity by; a step is per owner, so its owner joins the name."""
    name = event[_OPENERS[opener][0]]
    return (event.get("subagentRunId"), name) if opener == "STEP_STARTED" else name


def _args(start: dict[str, Any], args: str) -> dict[str, Any]:
    return _tag({"type": "TOOL_CALL_ARGS", "toolCallId": start["toolCallId"], "delta": args}, start)


def _tag(target: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """``target`` carrying ``event``'s subagent, as the client's reducer attributes it."""
    if event.get("subagentRunId") is not None:
        target["subagentRunId"] = event["subagentRunId"]
    return target


def _unique(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first: dict[Any, dict[str, Any]] = {}
    for message in messages:
        first.setdefault(message.get("id"), message)
    return list(first.values())


__all__ = ["replay_head"]
