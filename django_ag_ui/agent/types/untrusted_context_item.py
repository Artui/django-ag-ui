from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UntrustedContextItem:
    """One labelled piece of client-supplied context, normalised."""

    label: str
    """What this item is, as the client described it. Rendered as the section's
    ``description:`` line after whitespace collapsing and capping."""

    value: str
    """The item's text. Rendered verbatim inside the fenced block, subject only
    to sentinel neutralisation and the shared character budget."""


__all__ = ["UntrustedContextItem"]
