from __future__ import annotations

from typing import TYPE_CHECKING, cast

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai_harness.step_persistence import (
    ContinuableSnapshot,
    RunRecord,
    StepEvent,
    ToolEffectRecord,
)

from django_ag_ui.conf import get_setting
from django_ag_ui.contrib.store.models import (
    StoredRun,
    StoredSnapshot,
    StoredStepEvent,
    StoredToolEffect,
)
from django_ag_ui.persistence.anonymous_operation_error import AnonymousOperationError
from django_ag_ui.persistence.utils import resolve_owner_id

if TYPE_CHECKING:
    from pydantic_ai_harness.step_persistence import EventKind, ToolEffectStatus


class DefaultStepStore:
    """A durable, owner-scoped ``StepStore`` over the reference models.

    The database equivalent of the harness's own ``SqliteStepStore`` /
    ``FileStepStore``: it structurally satisfies ``pydantic-ai-harness``'s
    ``StepStore`` protocol (the ten async methods a ``StepPersistence``
    capability calls) while partitioning every row by the resolved owner — so
    one user can never read or resume another's runs, even by guessing a
    ``run_id`` (the harness types carry no owner; this store adds it).

    **Per-request.** Unlike a ``ConversationStore`` (a singleton whose methods
    take ``request``), the protocol's methods carry no request, so this store
    binds one at construction and is built **per request** — pass it through the
    ``AGUIServer(step_store=...)`` factory, which the view calls with the live
    request. Owner resolution runs inside each ``sync_to_async`` hop (it may
    create a session row for the anonymous bucket, so it must stay off the event
    loop), exactly as :class:`~django_ag_ui.persistence.model_conversation_store.ModelConversationStore`
    does.

    **Anonymous requests degrade, they don't crash.** When the request has no
    owner and ``ALLOW_ANONYMOUS`` is off, ``resolve_owner_id`` refuses — the
    capability's lifecycle hooks fire mid-run, so a raise would abort the run.
    Instead every write no-ops and every read returns empty: the run still
    streams, it just isn't recorded (an anonymous visitor has no durable
    identity to resume under anyway). Pair the store with
    ``require_authenticated=True`` (or ``ALLOW_ANONYMOUS``) for it to persist.

    Enable the backing tables by adding ``"django_ag_ui.contrib.store"`` to
    ``INSTALLED_APPS`` and running ``migrate`` (the same app the reference
    conversation store uses). Requires the ``django-ag-ui[harness]`` extra.
    """

    def __init__(self, request: HttpRequest, *, allow_anonymous: bool | None = None) -> None:
        """``allow_anonymous`` governs whether an anonymous request is recorded.

        ``None`` (the default) takes ``DJANGO_AG_UI["ALLOW_ANONYMOUS"]``,
        resolved once here — matching the conversation store's policy so two
        endpoints sharing a persistence strategy agree on it.
        """
        self._request = request
        self._allow_anonymous: bool = (
            allow_anonymous
            if allow_anonymous is not None
            else bool(get_setting("ALLOW_ANONYMOUS", False))
        )

    # -- Runs -----------------------------------------------------------------

    async def register_run(self, record: RunRecord) -> None:
        await sync_to_async(self._register_run)(record)

    async def get_run(self, *, run_id: str) -> RunRecord | None:
        return await sync_to_async(self._get_run)(run_id)

    async def list_runs(
        self,
        *,
        parent_run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> list[RunRecord]:
        return await sync_to_async(self._list_runs)(parent_run_id, conversation_id)

    # -- Events ---------------------------------------------------------------

    async def append_event(self, event: StepEvent) -> None:
        await sync_to_async(self._append_event)(event)

    async def list_events(self, *, run_id: str) -> list[StepEvent]:
        return await sync_to_async(self._list_events)(run_id)

    # -- Snapshots ------------------------------------------------------------

    async def save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        await sync_to_async(self._save_snapshot)(snapshot)

    async def latest_snapshot(self, *, run_id: str) -> ContinuableSnapshot | None:
        return await sync_to_async(self._latest_snapshot)(run_id)

    # -- Tool effects ---------------------------------------------------------

    async def record_tool_effect(self, record: ToolEffectRecord) -> None:
        await sync_to_async(self._record_tool_effect)(record)

    async def get_tool_effect(self, *, run_id: str, tool_call_id: str) -> ToolEffectRecord | None:
        return await sync_to_async(self._get_tool_effect)(run_id, tool_call_id)

    async def list_unresolved_tool_effects(self, *, run_id: str) -> list[ToolEffectRecord]:
        return await sync_to_async(self._list_unresolved_tool_effects)(run_id)

    # -- Sync row operations (owner resolved off the event loop) --------------

    def _owner(self) -> str | None:
        """The resolved owner id, or ``None`` when an anonymous request is refused."""
        try:
            return resolve_owner_id(self._request, allow_anonymous=self._allow_anonymous)
        except AnonymousOperationError:
            return None

    def _register_run(self, record: RunRecord) -> None:
        owner = self._owner()
        if owner is None:
            return
        StoredRun.objects.update_or_create(
            owner_id=owner,
            run_id=record.run_id,
            defaults={
                "conversation_id": record.conversation_id,
                "parent_run_id": record.parent_run_id,
                "agent_name": record.agent_name,
                "metadata": dict(record.metadata),
                "started_at": record.started_at,
            },
        )

    def _get_run(self, run_id: str) -> RunRecord | None:
        owner = self._owner()
        if owner is None:
            return None
        row = StoredRun.objects.filter(owner_id=owner, run_id=run_id).first()
        return None if row is None else self._run_from_row(row)

    def _list_runs(self, parent_run_id: str | None, conversation_id: str | None) -> list[RunRecord]:
        owner = self._owner()
        if owner is None:
            return []
        rows = StoredRun.objects.filter(owner_id=owner)
        if parent_run_id is not None:
            rows = rows.filter(parent_run_id=parent_run_id)
        if conversation_id is not None:
            rows = rows.filter(conversation_id=conversation_id)
        return [self._run_from_row(row) for row in rows.order_by("started_at")]

    def _append_event(self, event: StepEvent) -> None:
        owner = self._owner()
        if owner is None:
            return
        StoredStepEvent.objects.create(
            owner_id=owner,
            run_id=event.run_id,
            kind=event.kind,
            step_index=event.step_index,
            timestamp=event.timestamp,
            conversation_id=event.conversation_id,
            parent_run_id=event.parent_run_id,
            agent_name=event.agent_name,
            tool_call_id=event.tool_call_id,
            tool_name=event.tool_name,
            error=event.error,
            metadata=dict(event.metadata),
        )

    def _list_events(self, run_id: str) -> list[StepEvent]:
        owner = self._owner()
        if owner is None:
            return []
        rows = StoredStepEvent.objects.filter(owner_id=owner, run_id=run_id).order_by("id")
        return [self._event_from_row(row) for row in rows]

    def _save_snapshot(self, snapshot: ContinuableSnapshot) -> None:
        owner = self._owner()
        if owner is None:
            return
        StoredSnapshot.objects.create(
            owner_id=owner,
            run_id=snapshot.run_id,
            step_index=snapshot.step_index,
            messages=ModelMessagesTypeAdapter.dump_python(snapshot.messages, mode="json"),
            conversation_id=snapshot.conversation_id,
            parent_run_id=snapshot.parent_run_id,
            agent_name=snapshot.agent_name,
            timestamp=snapshot.timestamp,
        )

    def _latest_snapshot(self, run_id: str) -> ContinuableSnapshot | None:
        owner = self._owner()
        if owner is None:
            return None
        # Most recent by insertion order (largest pk), not by ``step_index`` —
        # matching the harness stores' ``snaps[-1]`` semantics.
        row = StoredSnapshot.objects.filter(owner_id=owner, run_id=run_id).order_by("-id").first()
        if row is None:
            return None
        return ContinuableSnapshot(
            run_id=row.run_id,
            step_index=row.step_index,
            messages=ModelMessagesTypeAdapter.validate_python(row.messages),
            conversation_id=row.conversation_id,
            parent_run_id=row.parent_run_id,
            agent_name=row.agent_name,
            timestamp=row.timestamp,
        )

    def _record_tool_effect(self, record: ToolEffectRecord) -> None:
        owner = self._owner()
        if owner is None:
            return
        StoredToolEffect.objects.update_or_create(
            owner_id=owner,
            run_id=record.run_id,
            tool_call_id=record.tool_call_id,
            defaults={
                "tool_name": record.tool_name,
                "status": record.status,
                "started_at": record.started_at,
                "ended_at": record.ended_at,
                "idempotency_key": record.idempotency_key,
                "effect_summary": record.effect_summary,
            },
        )

    def _get_tool_effect(self, run_id: str, tool_call_id: str) -> ToolEffectRecord | None:
        owner = self._owner()
        if owner is None:
            return None
        row = StoredToolEffect.objects.filter(
            owner_id=owner, run_id=run_id, tool_call_id=tool_call_id
        ).first()
        return None if row is None else self._tool_effect_from_row(row)

    def _list_unresolved_tool_effects(self, run_id: str) -> list[ToolEffectRecord]:
        owner = self._owner()
        if owner is None:
            return []
        rows = StoredToolEffect.objects.filter(
            owner_id=owner, run_id=run_id, status="started"
        ).order_by("id")
        return [self._tool_effect_from_row(row) for row in rows]

    # -- Row → record adapters ------------------------------------------------

    @staticmethod
    def _run_from_row(row: StoredRun) -> RunRecord:
        return RunRecord(
            run_id=row.run_id,
            conversation_id=row.conversation_id,
            parent_run_id=row.parent_run_id,
            agent_name=row.agent_name,
            metadata=dict(row.metadata),
            started_at=row.started_at,
        )

    @staticmethod
    def _event_from_row(row: StoredStepEvent) -> StepEvent:
        # ``kind`` was written from the harness ``EventKind`` literal set, so the
        # DB string is one of them — cast at the boundary rather than re-validate.
        return StepEvent(
            run_id=row.run_id,
            kind=cast("EventKind", row.kind),
            step_index=row.step_index,
            timestamp=row.timestamp,
            conversation_id=row.conversation_id,
            parent_run_id=row.parent_run_id,
            agent_name=row.agent_name,
            tool_call_id=row.tool_call_id,
            tool_name=row.tool_name,
            error=row.error,
            metadata=dict(row.metadata),
        )

    @staticmethod
    def _tool_effect_from_row(row: StoredToolEffect) -> ToolEffectRecord:
        return ToolEffectRecord(
            tool_call_id=row.tool_call_id,
            tool_name=row.tool_name,
            run_id=row.run_id,
            status=cast("ToolEffectStatus", row.status),
            started_at=row.started_at,
            ended_at=row.ended_at,
            idempotency_key=row.idempotency_key,
            effect_summary=row.effect_summary,
        )


__all__ = ["DefaultStepStore"]
