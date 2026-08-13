from __future__ import annotations

from typing import Any

from django_ag_ui.agent.attachment_context_items import attachment_context_items
from django_ag_ui.agent.render_untrusted_context import render_untrusted_context
from django_ag_ui.agent.types.untrusted_context_item import UntrustedContextItem
from django_ag_ui.config.types.run_context_config import RunContextConfig


def build_untrusted_context(run_input: Any, *, config: RunContextConfig) -> str | None:
    """This run's fenced client-supplied context, or ``None`` when there is none.

    Composes the two sources a browser client populates, in the order the model
    reads best: ``RunAgentInput.context`` first — the ambient situation, what
    page the user is on, what they have selected — then the attachment manifest,
    the specific handles the model can act on. Each is a flag, so a project can
    take one without the other, and both reduce to
    ``UntrustedContextItem``
    so the fence treats them identically.

    ``context`` is a required, typed field on ``RunAgentInput``, so an entry is
    always a ``description`` / ``value`` pair and there is nothing to guard
    against. ``run_input`` is ``Any`` in keeping with the rest of this layer,
    where the AG-UI wire types arrive from pydantic-ai's adapter.
    """
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
