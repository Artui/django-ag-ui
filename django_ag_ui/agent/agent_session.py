"""``AgentSession`` — one AG-UI run's orchestration, apart from the transport."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from ag_ui.core import Message
from django.http import HttpRequest
from pydantic_ai import Agent
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.guarded_stream import guarded_stream
from django_ag_ui.agent.reasoning_filter import drop_reasoning_events
from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.conf import get_settings
from django_ag_ui.persistence.anonymous_operation_error import AnonymousOperationError
from django_ag_ui.persistence.null_conversation_store import NullConversationStore
from django_ag_ui.persistence.resolve_conversation_store import resolve_conversation_store
from django_ag_ui.persistence.types.conversation import Conversation
from django_ag_ui.persistence.types.conversation_store import ConversationStore
from django_ag_ui.persistence.utils import owner_id_for
from django_ag_ui.policy.audit.types.audit_event import AuditEvent
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger


class AgentSession:
    """Per-run orchestration between the HTTP transport and the agent.

    Owns everything one AG-UI run needs after the transport has authenticated
    the request, parsed the ``RunAgentInput``, and built the agent — and before
    the response object exists: the ``AGUIAdapter``, the composed event stream
    (native → transformed → reasoning-filtered → encoded → disconnect-guarded),
    completed-run persistence, and the cancelled-run persist + audit path.

    Splitting it from :class:`~django_ag_ui.agent.agui_view.DjangoAGUIView`
    makes the streaming pipeline testable without a ``StreamingHttpResponse``
    (drive :meth:`stream` directly) and keeps the SSE transport swappable —
    a future WebSocket transport reuses the session unchanged.
    """

    def __init__(
        self,
        agent: Agent[None, Any],
        run_input: Any,
        request: HttpRequest,
        *,
        audit_logger: AuditLogger,
    ) -> None:
        self._agent = agent
        self._run_input = run_input
        self._request = request
        self._audit_logger = audit_logger
        settings = get_settings()
        self._forward_reasoning = settings.forward_reasoning
        self._adapter = AGUIAdapter(
            agent,
            run_input,
            # A plain string at the settings boundary; the adapter types it as
            # the Literal["server", "client"].
            manage_system_prompt=cast("Any", settings.manage_system_prompt),
            allow_uploaded_files=settings.allow_uploaded_files,
        )

    def stream(self) -> AsyncIterator[str]:
        """The encoded AG-UI event stream for this run, disconnect-guarded.

        Composed by hand (rather than ``adapter.run_stream``) so the session
        keeps a reference to the *native* event stream — the innermost
        generator, whose context manager owns the provider's streaming
        request. On client disconnect the guard closes it explicitly, then
        persists the partial exchange and audits the cancellation.
        """
        transcript = RunTranscript()
        native = self._adapter.run_stream_native()
        events = self._adapter.transform_stream(native, on_complete=self._on_complete())
        # A reasoning model's chain-of-thought rides through as AG-UI reasoning
        # events (adapter pass-through). Forward it by default; strip it when
        # the consumer opts out, so the model can reason privately.
        if not self._forward_reasoning:
            events = drop_reasoning_events(events)
        return guarded_stream(
            self._adapter.encode_stream(transcript.observe(events)),
            native_events=native,
            on_cancel=self._on_cancel(transcript),
        )

    def _on_complete(self) -> Callable[[Any], Awaitable[None]] | None:
        """The adapter's ``on_complete`` callback persisting the conversation.

        ``None`` when persistence is off (the default ``NullConversationStore``),
        so the stateless path adds no overhead. Otherwise the callback mirrors
        the run's full message history into the configured store when the run
        finishes streaming.
        """
        save = self._message_saver()
        if save is None:
            return None

        async def _on_complete(result: Any) -> None:
            await save(AGUIAdapter.dump_messages(result.all_messages()))

        return _on_complete

    def _message_saver(self) -> Callable[[list[Message]], Awaitable[None]] | None:
        """A closure persisting AG-UI messages to the configured store.

        ``None`` when persistence is off — both the completed-run and the
        cancelled-run paths build their message list and hand it here, so the
        two persist with identical thread/owner scoping.
        """
        store: ConversationStore = resolve_conversation_store(get_settings().conversation_store)
        if isinstance(store, NullConversationStore):
            return None
        thread_id: str = self._run_input.thread_id
        owner_id = owner_id_for(self._request)
        request = self._request

        async def _save(messages: list[Message]) -> None:
            conversation = Conversation(
                thread_id=thread_id,
                messages=messages,
                owner_id=owner_id,
            )
            try:
                await store.save(conversation, request=request)
            except AnonymousOperationError:
                # An anonymous run on an open agent endpoint with a persisting
                # store that refuses anonymous writes (the default, no
                # ``ALLOW_ANONYMOUS``): the run still streams, it just isn't
                # saved — better than crashing the completed stream.
                return

        return _save

    def _on_cancel(self, transcript: RunTranscript) -> Callable[[], Awaitable[None]]:
        """The guard's ``on_cancel``: persist the partial exchange, then audit.

        Persistence mirrors the completed-run shape — the client-sent history
        plus whatever the transcript observed before the disconnect — so a
        durable thread reflects the truncated exchange (matching the client,
        which keeps the partial assistant bubble). The audit record rides the
        existing ``record(AuditEvent)`` surface as a ``tool_name="agent.run"``
        event rather than a new protocol method, so custom loggers keep
        working unchanged; ``duration_ms`` measures run start → cancellation.
        """
        save = self._message_saver()
        audit = self._audit_logger
        started = time.perf_counter()
        input_messages: list[Message] = list(self._run_input.messages)
        run_ref = json.dumps(
            {"run_id": self._run_input.run_id, "thread_id": self._run_input.thread_id},
            sort_keys=True,
        )
        ip_address = self._request.META.get("REMOTE_ADDR")

        async def _on_cancel() -> None:
            if save is not None:
                await save([*input_messages, *transcript.messages()])
            audit.record(
                AuditEvent(
                    tool_name="agent.run",
                    arguments_repr=run_ref,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=False,
                    error="cancelled: client disconnected mid-run",
                    ip_address=ip_address,
                ),
            )

        return _on_cancel


__all__ = ["AgentSession"]
