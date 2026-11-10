from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from ag_ui.core import BaseEvent, Message, RunErrorEvent
from django.http import HttpRequest
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.persistence.utils import owner_id_for, resolve_owner_id
from django_spec_agents.policy.audit.types.audit_event import AuditEvent
from django_spec_agents.policy.audit.types.audit_logger import AuditLogger
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage

from django_ag_ui.agent.build_client_context_toolset import build_client_context_toolset
from django_ag_ui.agent.build_untrusted_context import build_untrusted_context
from django_ag_ui.agent.continued_history import continued_history
from django_ag_ui.agent.guarded_stream import guarded_stream
from django_ag_ui.agent.heartbeat_stream import heartbeat_stream
from django_ag_ui.agent.inject_compaction_events import inject_compaction_events
from django_ag_ui.agent.inject_invalidation_events import inject_invalidation_events
from django_ag_ui.agent.inject_subagent_events import inject_subagent_events
from django_ag_ui.agent.lend_stored_ids import lend_stored_ids
from django_ag_ui.agent.outcome_agui_adapter import OutcomeAGUIAdapter
from django_ag_ui.agent.reasoning_filter import drop_reasoning_events
from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.agent.stamp_approval_prompts import stamp_approval_prompts
from django_ag_ui.agent.strip_binary_content import strip_binary_content
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.utils import messages_to_jsonable


class AgentSession:
    """Per-run orchestration between the HTTP transport and the agent."""

    def __init__(
        self,
        agent: Agent[AgentDeps, Any],
        run_input: Any,
        request: HttpRequest,
        *,
        deps: AgentDeps,
        audit_logger: AuditLogger,
        config: AGUIConfig,
        conversation_store: ConversationStore,
        message_history: list[ModelMessage] | None = None,
        source_messages: list[Message] | None = None,
        model: Any = None,
        instructions: str | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
    ) -> None:
        self._agent = agent
        self._run_input = run_input
        self._request = request
        # Per-run overrides, kept off the agent so one built agent serves every run.
        self._model = model
        self._instructions = instructions
        self._toolsets = toolsets
        self._capabilities = capabilities
        self._deps = deps
        self._audit_logger = audit_logger
        self._config = config
        self._conversation_store = conversation_store
        self._message_history = message_history
        self._source_messages = source_messages
        self._prior: list[Message] | None = None
        self._forward_reasoning = config.forward_reasoning
        self._adapter = OutcomeAGUIAdapter(
            agent,
            run_input,
            manage_system_prompt=cast("Any", config.manage_system_prompt),
            manage_message_history="client" if message_history is None else "server",
            allow_uploaded_files=config.allow_uploaded_files,
        )

    def stream(self) -> AsyncIterator[str]:
        """The encoded AG-UI event stream for this run, disconnect-guarded."""
        transcript = RunTranscript()
        native = self._adapter.run_stream_native(
            message_history=self._message_history,
            deps=self._deps,
            model=self._model,
            instructions=self._run_instructions(),
            toolsets=self._run_toolsets(),
            capabilities=self._capabilities,
        )
        events = self._adapter.transform_stream(native, on_complete=self._on_complete(transcript))
        if not self._forward_reasoning:
            events = drop_reasoning_events(events)
        events = inject_compaction_events(events)
        events = inject_invalidation_events(events)
        subagents = inject_subagent_events(events)
        events = subagents
        if self._config.approval_prompts:
            events = stamp_approval_prompts(events, prompts=self._config.approval_prompts)
        if self._persists():
            events = transcript.observe(events)
        observed = self._persist_on_error(events, transcript)
        encoded = self._adapter.encode_stream(observed)
        if self._config.heartbeat_seconds > 0:
            encoded = heartbeat_stream(encoded, interval=self._config.heartbeat_seconds)
        return guarded_stream(
            encoded,
            native_events=native,
            on_cancel=self._on_cancel(transcript),
            racing_stages=(subagents,),
        )

    def _client_context_block(self) -> str | None:
        """This run's fenced client-supplied context, whichever channel takes it."""
        return build_untrusted_context(self._run_input, config=self._config.run_context)

    def _run_instructions(self) -> list[Any] | str | None:
        """The operator instructions, plus this run's client context when it rides here."""
        if self._config.run_context.delivery != "instructions":
            return self._instructions
        block = self._client_context_block()
        if block is None:
            return self._instructions
        if self._instructions is None:
            return cast("Any", lambda _ctx: block)
        return [self._instructions, cast("Any", lambda _ctx: block)]

    def _run_toolsets(self) -> list[Any] | None:
        """The session's toolsets, plus the context tool when it rides there."""
        if self._config.run_context.delivery != "tool":
            return self._toolsets
        block = self._client_context_block()
        if block is None:
            return self._toolsets
        return [*(self._toolsets or []), build_client_context_toolset(block)]

    async def _persist_on_error(
        self,
        events: AsyncIterator[BaseEvent],
        transcript: RunTranscript,
    ) -> AsyncIterator[BaseEvent]:
        """Pass ``events`` through, persisting the exchange if the run errors."""
        finalize = self._on_error(transcript)
        async for event in events:
            if isinstance(event, RunErrorEvent):
                await finalize(event.message)
                event = self._client_facing_error(event)
            yield event

    def _client_facing_error(self, event: RunErrorEvent) -> RunErrorEvent:
        """``event`` with its exception text withheld, unless detail is opted in."""
        if self._config.tool_failure.include_detail:
            return event
        return event.model_copy(
            update={"message": "The run failed. The failure has been recorded."}
        )

    def _prior_messages(self) -> list[Message]:
        """Everything before this run's own output, in the client's own shape."""
        if self._prior is None:
            if self._message_history is None:
                self._prior = list(self._run_input.messages)
            else:
                history, new_turn = continued_history(
                    self._message_history, self._run_input.messages
                )
                kept = strip_binary_content(OutcomeAGUIAdapter.dump_messages(history))
                if self._source_messages:
                    kept = lend_stored_ids(
                        kept,
                        self._source_messages,
                        reserved={message.id for message in new_turn},
                    )
                self._prior = [*kept, *new_turn]
        return self._prior

    def _on_complete(self, transcript: RunTranscript) -> Callable[[Any], Awaitable[None]] | None:
        """The adapter's ``on_complete`` callback persisting the conversation."""
        save = self._message_saver()
        if save is None:
            return None

        async def _on_complete(result: Any) -> None:
            dumped = OutcomeAGUIAdapter.dump_messages(result.new_messages())
            new = strip_binary_content(transcript.with_streamed_ids(dumped))
            await save([*self._prior_messages(), *new])

        return _on_complete

    def _persists(self) -> bool:
        """Whether this run has anywhere to store what it produces."""
        return not isinstance(self._conversation_store, NullConversationStore)

    def _owner_id(self) -> str | None:
        """The owner scope stamped onto the stored conversation."""
        owner_id = owner_id_for(self._request)
        if owner_id is not None:
            return owner_id
        session = getattr(self._request, "session", None)
        if getattr(session, "session_key", None) is None:
            return None
        return resolve_owner_id(self._request, allow_anonymous=True)

    def _message_saver(self) -> Callable[[list[Message]], Awaitable[None]] | None:
        """A closure persisting AG-UI messages to the configured store."""
        store: ConversationStore = self._conversation_store
        if not self._persists():
            return None
        thread_id: str = self._run_input.thread_id
        owner_id = self._owner_id()
        request = self._request

        async def _save(messages: list[Message]) -> None:
            conversation = Conversation(
                # The AG-UI wire shape is serialised here, not in the substrate.
                thread_id=thread_id,
                messages=messages_to_jsonable(messages),
                owner_id=owner_id,
            )
            try:
                await store.save(conversation, request=request)
            except AnonymousOperationError:
                return

        return _save

    def _run_finalizer(self, transcript: RunTranscript) -> Callable[[str], Awaitable[None]]:
        """Persist the partial exchange and audit the run, given a reason."""
        save = self._message_saver()
        audit = self._audit_logger
        started = time.perf_counter()
        run_ref = json.dumps(
            {"run_id": self._run_input.run_id, "thread_id": self._run_input.thread_id},
            sort_keys=True,
        )
        ip_address = self._request.META.get("REMOTE_ADDR")

        async def _finalize(error: str) -> None:
            if save is not None:
                await save([*self._prior_messages(), *transcript.messages()])
            audit.record(
                AuditEvent(
                    tool_name="agent.run",
                    arguments_repr=run_ref,
                    duration_ms=(time.perf_counter() - started) * 1000.0,
                    success=False,
                    error=error,
                    ip_address=ip_address,
                ),
            )

        return _finalize

    def _on_cancel(self, transcript: RunTranscript) -> Callable[[], Awaitable[None]]:
        """The guard's ``on_cancel``: persist the partial exchange, then audit."""
        finalize = self._run_finalizer(transcript)

        async def _on_cancel() -> None:
            await finalize("cancelled: client disconnected mid-run")

        return _on_cancel

    def _on_error(self, transcript: RunTranscript) -> Callable[[str], Awaitable[None]]:
        """The ``RUN_ERROR`` counterpart: persist the partial exchange, then audit."""
        finalize = self._run_finalizer(transcript)

        async def _on_error(message: str) -> None:
            await finalize(f"run failed: {message}")

        return _on_error


__all__ = ["AgentSession"]
