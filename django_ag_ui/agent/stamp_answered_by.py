from __future__ import annotations

from typing import TypeVar

from ag_ui.core import ToolCallResultEvent, ToolMessage

ANSWERED_BY_FIELD = "answered_by"

_Stamped = TypeVar("_Stamped", ToolCallResultEvent, ToolMessage)


def stamp_answered_by(event: _Stamped, answered_by: str) -> _Stamped:
    """A copy of ``event`` carrying ``answered_by`` in its ``metadata`` only, never top-level."""
    metadata = {**(event.metadata or {}), ANSWERED_BY_FIELD: answered_by}
    return event.model_copy(update={"metadata": metadata})


__all__ = ["ANSWERED_BY_FIELD", "stamp_answered_by"]
