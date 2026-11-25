from __future__ import annotations

from django_ag_ui.agent.subagent_progress import task_text


def test_text_of_exactly_8000_characters_is_kept_whole() -> None:
    assert task_text("message", "x" * 8000) == {"message": "x" * 8000}


def test_text_of_8001_characters_is_cut_to_8000_and_flagged() -> None:
    assert task_text("message", "x" * 8001) == {"message": "x" * 8000, "truncated": True}
