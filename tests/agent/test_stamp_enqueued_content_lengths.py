from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai.messages import ModelRequest, TextContent, UserPromptPart

from django_ag_ui.agent.prompt_metadata import stamp_enqueued_content


@pytest.mark.parametrize("users", [0, 2])
def test_content_whose_user_entries_do_not_match_the_prompts_is_left_alone(users: int) -> None:
    marked = UserPromptPart(content=[TextContent(content="note", metadata={"source": "wake"})])
    content: dict[str, Any] = {"messages": [{"role": "user", "content": "note"}] * users}

    assert stamp_enqueued_content([ModelRequest(parts=[marked])], content) is content
