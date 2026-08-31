"""``ContextDelivery`` — which channel carries client-supplied context."""

from __future__ import annotations

from typing import Literal

ContextDelivery = Literal["instructions", "tool"]
"""Which channel carries the fenced client-context block.

**There are two defensible answers and this package holds neither as the only
one**, because the right one depends on who writes the client. The block is
identical either way — same fence, same sentinel neutralisation, same budget —
and only the delivery differs.

``"instructions"`` (the default) delivers it as additional run instructions.
They are re-rendered on every model request, so the block **survives
compaction**: the attachment manifest is still there at step 20 when the model
decides to call ``read_attachment``. They land on ``ModelRequest.instructions``,
which the AG-UI adapter does not emit, so the text is neither persisted into the
thread nor echoed back to the browser.

The cost is the one pydantic-ai names in its own documentation: instructions
carry **operator authority**, so building them out of text a client sent lets a
prompt injection inherit it. Upstream left an ``AGUIAdapter.context`` accessor
out deliberately for that reason and points consumers at tool output instead.
The fence here is labelling, not sanitisation, and does not change that.

``"tool"`` follows upstream: the block becomes the return value of a
``get_client_context`` tool, so the text arrives as data the model fetched
rather than as instructions it was given. Three costs, all real. A tool result
can be **compacted away** and is not re-supplied, so an attachment handle can
stop being referenceable partway through a long run. The model has to *decide*
to call it — ambient facts like which page the user is on are only considered if
the model thinks to ask. And a tool result is an ordinary part of the exchange,
so the block is **streamed back to the browser and persisted into the thread**,
where the instructions channel is neither. That is auditability for some
projects and an unwanted copy of a page map for others; it is a property of the
channel, not a bug in it.

Pick ``"tool"`` where the page that fills ``RunAgentInput.context`` is not
fully under your control, and ``"instructions"`` where it is and the manifest
matters more than the authority boundary.
"""


__all__ = ["ContextDelivery"]
