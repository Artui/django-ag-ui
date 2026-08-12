from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UntrustedContextItem:
    """One labelled piece of client-supplied context, normalised.

    Both sources reduce to this shape before rendering — a ``RunAgentInput.context``
    entry and the attachment manifest derived from the posted messages — so
    :func:`~django_ag_ui.agent.render_untrusted_context.render_untrusted_context`
    never learns where an item came from and cannot treat one source as more
    trustworthy than the other.

    Neither field is sanitised here: both are raw client text, and the renderer
    is the single place that neutralises the sentinel and caps the label, so
    there is one implementation to audit rather than one per source.
    """

    label: str
    """What this item is, as the client described it. Rendered as the section's
    ``description:`` line after whitespace collapsing and capping."""

    value: str
    """The item's text. Rendered verbatim inside the fenced block, subject only
    to sentinel neutralisation and the shared character budget."""


__all__ = ["UntrustedContextItem"]
