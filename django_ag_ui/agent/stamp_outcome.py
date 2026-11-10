from __future__ import annotations

from typing import TypeVar

from ag_ui.core import ToolCallResultEvent, ToolMessage

OUTCOME_FIELD = "outcome"
"""The key naming a tool call's outcome on ``TOOL_CALL_RESULT``, on both carriers.

The stored tool message a thread keeps for the call carries it the same way, so
a client replaying the thread reads what the stream told it: a web component
fed from the thread endpoint replays the server's copy, not its own.

AG-UI declares no outcome on the event, so the value is written twice, under
this one key:

- **In the event's ``metadata``**, which is what a client on ``@ag-ui/client``
  1.0 reads. ``metadata`` is the protocol's own open slot on events and
  messages from 1.0, and that client folds a result event's ``metadata`` onto
  the tool message it appends, so the outcome ends up on the message a renderer
  draws rather than only on an event that has already gone past.
- **As a top-level key**, which is what a client on ``@ag-ui/client`` 0.x reads.
  It is not a declared field: the Python models are ``extra="allow"``, so the
  key lands in ``__pydantic_extra__`` and encodes, and the 0.x client parsed
  events with zod ``passthrough`` schemas that kept it. The 1.0 client does not
  keep it -- its enforcement stage strips every key a schema does not declare,
  and logs the removal of ``/outcome`` as it does -- which is why the metadata
  carrier exists at all. The top-level key stays because every web component
  release up to 0.40 runs a 0.x client and reads nothing else.
"""


_Stamped = TypeVar("_Stamped", ToolCallResultEvent, ToolMessage)


def stamp_outcome(event: _Stamped, outcome: str) -> _Stamped:
    """A copy of ``event`` carrying ``outcome`` on both carriers ``OUTCOME_FIELD`` names."""
    metadata = {**(event.metadata or {}), OUTCOME_FIELD: outcome}
    return event.model_copy(update={OUTCOME_FIELD: outcome, "metadata": metadata})


__all__ = ["OUTCOME_FIELD", "stamp_outcome"]
