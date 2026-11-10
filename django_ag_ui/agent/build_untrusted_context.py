from __future__ import annotations

from typing import Any

from django_ag_ui.agent.attachment_context_items import attachment_context_items
from django_ag_ui.agent.render_untrusted_context import render_untrusted_context
from django_ag_ui.agent.types.untrusted_context_item import UntrustedContextItem
from django_ag_ui.config.types.run_context_config import RunContextConfig


def build_untrusted_context(run_input: Any, *, config: RunContextConfig) -> str | None:
    """This run's fenced client-supplied context, or ``None`` when there is none."""
    items: list[UntrustedContextItem] = []
    if config.client_context:
        items.extend(
            UntrustedContextItem(label=entry.description, value=entry.value)
            for entry in run_input.context
        )
    if config.attachment_manifest:
        items.extend(attachment_context_items(run_input.messages))
    return render_untrusted_context(items, max_chars=config.max_chars)


__all__ = ["build_untrusted_context"]
