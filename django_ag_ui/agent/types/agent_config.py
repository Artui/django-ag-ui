from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django_ag_ui.policy.audit.types.audit_logger import AuditLogger


@dataclass(frozen=True)
class AgentConfig:
    """Resolved construction parameters for a Pydantic-AI ``Agent``.

    Bundles everything :func:`~django_ag_ui.agent.agent_factory.build_agent`
    needs so the call site passes one record instead of a long keyword list.
    The view resolves these from explicit overrides and the ``DJANGO_AG_UI``
    settings; ``toolsets`` and ``capabilities`` arrive already resolved to
    instances (not dotted paths).
    """

    model: Any
    """The Pydantic-AI model (a model string or ``Model`` instance)."""

    instructions: str | None = None
    """System/instructions prompt for the agent."""

    audit_logger: AuditLogger | None = None
    """Wraps every server-side tool call for timing + success/failure
    records. ``None`` means no auditing (a no-op logger)."""

    audit_ip_address: str | None = None
    """Client IP stamped onto every audit event this agent records (the view
    fills it from the driving request). ``None`` leaves the field unset."""

    model_settings: dict[str, Any] | None = None
    """Pydantic-AI ``ModelSettings`` (temperature, max_tokens, …)."""

    retries: int | None = None
    """Default tool/output retry budget."""

    toolsets: Sequence[Any] | None = None
    """Extra Pydantic-AI toolsets composed alongside the registry tools."""

    capabilities: Sequence[Any] | None = None
    """Pydantic-AI capabilities passed to the ``Agent``."""


__all__ = ["AgentConfig"]
