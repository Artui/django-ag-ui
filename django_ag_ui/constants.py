from __future__ import annotations

from enum import Enum


class ToolCategory(str, Enum):
    """Coarse grouping for a tool, surfaced to the agent and the UI.

    Categories are advisory metadata: they let a frontend group tools,
    let a system prompt reason about capability classes, and let a
    project apply category-wide policy. They do **not** by themselves
    gate execution — that is the ``destructive`` flag's job.
    """

    SHELL = "shell"
    INTROSPECT = "introspect"
    NAV = "nav"
    UI_READ = "ui_read"
    UI_WRITE = "ui_write"
    UI_GENERIC = "ui_generic"
    OTHER = "other"


# JSON-Schema extension key carrying our per-tool risk flag. AG-UI has no
# native destructive-tool concept, so we stamp this at the schema root and
# read it client-side to gate execution behind a confirmation modal.
X_DESTRUCTIVE_KEY = "x-destructive"

# JSON-Schema extension key carrying the tool's category, so a frontend can
# group/filter tools without a side channel.
X_CATEGORY_KEY = "x-category"

# JSON-Schema extension key carrying a human-readable confirmation prompt for a
# destructive tool (e.g. "Activate this project?"). When present, the frontend
# shows it in the confirmation step instead of a generic "Run <tool>?".
X_CONFIRM_KEY = "x-confirm"

# JSON-Schema extension key carrying a short human-readable label for a tool
# (e.g. "Query orders" for query_model). The frontend shows it on the tool-call
# card instead of the raw tool name.
X_SUMMARY_KEY = "x-summary"


__all__ = [
    "X_CATEGORY_KEY",
    "X_CONFIRM_KEY",
    "X_DESTRUCTIVE_KEY",
    "X_SUMMARY_KEY",
    "ToolCategory",
]
