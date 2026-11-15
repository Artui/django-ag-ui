from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from ag_ui.core import UserMessage

_ID_PREFIX = "a2ui-action-user-"
_NAME_PREFIX = "a2ui_action_context_"
_BLOCK_PREFIX = "A2UI user action:\n"


def with_a2ui_action(run_input: Any) -> Any:
    """``run_input`` with its ``forwardedProps.a2uiAction`` appended as a user turn, once."""
    props = run_input.forwarded_props
    action = props.get("a2uiAction") if isinstance(props, dict) else None
    user_action = action.get("userAction") if isinstance(action, dict) else None
    if not isinstance(user_action, dict):
        return run_input
    digest = hashlib.sha256(run_input.run_id.encode()).digest()
    message_id = _ID_PREFIX + digest.hex()
    name = _NAME_PREFIX + base64.urlsafe_b64encode(digest).decode().rstrip("=")
    if any(_is_turn(message, message_id, name) for message in run_input.messages):
        return run_input
    turn = UserMessage(id=message_id, name=name, content=_content(user_action))
    return run_input.model_copy(update={"messages": [*run_input.messages, turn]})


def _is_turn(message: Any, message_id: str, name: str) -> bool:
    """Whether ``message`` is this run's action turn, matched by id or by name."""
    return message.role == "user" and (message.id == message_id or message.name == name)


def _content(user_action: dict[str, Any]) -> str:
    """The turn's text: a non-empty ``userMessage``, a blank line, then the action as JSON."""
    block = _BLOCK_PREFIX + json.dumps(user_action, separators=(",", ":"), ensure_ascii=False)
    user_message = user_action.get("userMessage")
    if isinstance(user_message, str) and user_message:
        return f"{user_message}\n\n{block}"
    return block


__all__ = ["with_a2ui_action"]
