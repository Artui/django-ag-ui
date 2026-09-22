"""``stamp_outcome`` — write a tool call's outcome onto its ``TOOL_CALL_RESULT``."""

from __future__ import annotations

from ag_ui.core import ToolCallResultEvent

OUTCOME_FIELD = "outcome"
"""The key naming a tool call's outcome on ``TOOL_CALL_RESULT``, on both carriers.

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


def stamp_outcome(event: ToolCallResultEvent, outcome: str) -> ToolCallResultEvent:
    """A copy of ``event`` carrying ``outcome`` on both carriers ``OUTCOME_FIELD`` names.

    The outcome is **merged** into whatever ``metadata`` the event already
    carries rather than replacing it. Upstream builds its own result events with
    none, but a tool may return an AG-UI event of its own, which pydantic-ai
    forwards verbatim and this package stamps like any other result for the same
    call -- and a key its author put there is not this package's to discard. On
    ``OUTCOME_FIELD`` itself the stamp wins: the part's ``outcome`` is
    pydantic-ai's own record of how the call went, and it is the one value a
    client is told to trust under that key.

    A new ``metadata`` dict is built rather than the existing one updated in
    place, because ``model_copy`` is shallow: the copy would share the original's
    dict, and assigning into it would stamp an event the caller still holds.

    ``outcome`` is written as given. Which outcomes are worth stamping -- none on
    a success, since absent means success -- is the caller's decision, and it is
    made once, in ``OutcomeAGUIAdapter``'s stream. This is the part a wire-fixture
    recorder outside this repository imports, so that the fixture a client is
    tested against carries exactly the bytes this server writes.
    """
    metadata = {**(event.metadata or {}), OUTCOME_FIELD: outcome}
    return event.model_copy(update={OUTCOME_FIELD: outcome, "metadata": metadata})


__all__ = ["OUTCOME_FIELD", "stamp_outcome"]
