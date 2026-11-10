from __future__ import annotations

from dataclasses import dataclass

from django_ag_ui.config.types.context_delivery import ContextDelivery


@dataclass(frozen=True)
class RunContextConfig:
    """What client-supplied context reaches the model, and how much of it."""

    client_context: bool
    """Whether ``RunAgentInput.context`` entries are delivered to the model.
    ``False`` restores the behaviour of every release before this feature: the
    client can announce whatever it likes and the model never sees it."""

    attachment_manifest: bool
    """Whether attachment refs carried on the posted user messages are derived
    into a manifest of files the model can read with ``read_attachment``."""

    max_chars: int
    """Ceiling on the combined length of the delivered values. Client context is
    unbounded text that reaches the model on *every* request of a run, so this
    is a ceiling rather than a budget — content over it is truncated visibly,
    not dropped in silence."""

    delivery: ContextDelivery = "instructions"
    """Which channel carries the block —
    [`ContextDelivery`][django_ag_ui.ContextDelivery]. ``"instructions"``
    survives compaction and is never echoed back; ``"tool"`` follows
    pydantic-ai's documented preference and keeps client text out of the slot
    that carries operator authority. Read that type's docstring before changing
    it: the two are a genuine trade, not a default and a fallback.

    Last and defaulted, unlike its siblings, so that adding a channel did not
    break every existing construction of this record. The default is what the
    package has always done."""


__all__ = ["RunContextConfig"]
