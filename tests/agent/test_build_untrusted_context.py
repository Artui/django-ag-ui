from __future__ import annotations

import json
from typing import Any

from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.build_untrusted_context import build_untrusted_context
from django_ag_ui.config.types.run_context_config import RunContextConfig

PAGE_MAP = "Order #42, status shipped"
ATTACHMENT = {"id": "a1f3", "name": "report.pdf", "mime": "application/pdf", "size": 91231}


def _run_input(
    *,
    context: list[dict[str, str]] | None = None,
    attachments: list[dict[str, Any]] | None = None,
) -> Any:
    message: dict[str, Any] = {"id": "m1", "role": "user", "content": "what is the budget?"}
    if attachments is not None:
        message["attachments"] = attachments
    payload = {
        "threadId": "t1",
        "runId": "r1",
        "messages": [message],
        "tools": [],
        "context": context or [],
        "state": None,
        "forwardedProps": None,
    }
    return AGUIAdapter.build_run_input(json.dumps(payload).encode())


def _config(
    *, client_context: bool = True, attachment_manifest: bool = True, max_chars: int = 20000
) -> RunContextConfig:
    return RunContextConfig(
        client_context=client_context,
        attachment_manifest=attachment_manifest,
        max_chars=max_chars,
    )


def test_both_sources_off_delivers_nothing() -> None:
    run_input = _run_input(
        context=[{"description": "Current page", "value": PAGE_MAP}], attachments=[ATTACHMENT]
    )
    config = _config(client_context=False, attachment_manifest=False)
    assert build_untrusted_context(run_input, config=config) is None


def test_client_context_alone_is_delivered() -> None:
    run_input = _run_input(
        context=[{"description": "Current page", "value": PAGE_MAP}], attachments=[ATTACHMENT]
    )
    block = build_untrusted_context(run_input, config=_config(attachment_manifest=False))
    assert block is not None
    assert "description: Current page" in block
    assert PAGE_MAP in block
    assert "report.pdf" not in block


def test_the_attachment_manifest_alone_is_delivered() -> None:
    run_input = _run_input(
        context=[{"description": "Current page", "value": PAGE_MAP}], attachments=[ATTACHMENT]
    )
    block = build_untrusted_context(run_input, config=_config(client_context=False))
    assert block is not None
    assert "report.pdf" in block
    assert PAGE_MAP not in block


def test_the_ambient_situation_is_rendered_before_the_specific_handles() -> None:
    run_input = _run_input(
        context=[{"description": "Current page", "value": PAGE_MAP}], attachments=[ATTACHMENT]
    )
    block = build_untrusted_context(run_input, config=_config())
    assert block is not None
    assert block.index(PAGE_MAP) < block.index("report.pdf")


def test_both_sources_on_with_nothing_to_say_delivers_nothing() -> None:
    # The common case for a project that never populated either: no block at
    # all rather than an empty fence on every request of every run.
    assert build_untrusted_context(_run_input(), config=_config()) is None


def test_a_tight_ceiling_truncates_visibly() -> None:
    run_input = _run_input(context=[{"description": "Current page", "value": PAGE_MAP}])
    block = build_untrusted_context(run_input, config=_config(max_chars=8))
    assert block is not None
    assert "[truncated: client context is capped at 8 characters]" in block
