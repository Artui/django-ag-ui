from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from ag_ui.core import ActivitySnapshotEvent

from django_ag_ui.constants import A2UI_ACTIVITY_TYPE


def a2ui_activity(
    operations: Sequence[dict[str, Any]], *, message_id: str | None = None
) -> ActivitySnapshotEvent:
    """An event painting an A2UI surface from ``operations``, whole.

    The shape ``@ag-ui/a2ui-middleware`` emits: one ``ACTIVITY_SNAPSHOT`` of
    type ``a2ui-surface`` whose content is ``{"a2ui_operations": [...]}``, with
    ``replace=True`` so ``createSurface``, ``updateComponents`` and
    ``updateDataModel`` arrive together and the client never renders half a
    surface. The operations are passed through as given; nothing here validates
    them -- the ``render_a2ui`` tool does that before it calls this.

    ``message_id`` is the identity of the painted surface in the client's
    transcript: send the same one again to replace it in place. It defaults to
    ``a2ui-surface-<uuid>``; the tool uses ``a2ui-surface-<tool call id>``, as
    the middleware does.
    """
    return ActivitySnapshotEvent(
        message_id=message_id if message_id is not None else f"a2ui-surface-{uuid.uuid4()}",
        activity_type=A2UI_ACTIVITY_TYPE,
        content={"a2ui_operations": list(operations)},
        replace=True,
    )


__all__ = ["a2ui_activity"]
