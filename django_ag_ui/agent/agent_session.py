from __future__ import annotations

import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from ag_ui.core import BaseEvent, Message, RunErrorEvent, RunStartedEvent
from django.http import HttpRequest
from django_spec_agents import IdentifierTooLongError
from django_spec_agents.agent.types.agent_deps import AgentDeps
from django_spec_agents.persistence.anonymous_operation_error import AnonymousOperationError
from django_spec_agents.persistence.null_conversation_store import NullConversationStore
from django_spec_agents.persistence.types.conversation import Conversation
from django_spec_agents.persistence.types.conversation_store import ConversationStore
from django_spec_agents.persistence.types.run_outcome import RunOutcome
from django_spec_agents.persistence.types.run_store import RunStore
from django_spec_agents.persistence.types.run_waiting import RunWaiting
from django_spec_agents.persistence.utils import owner_id_for, resolve_owner_id
from django_spec_agents.policy.audit.types.audit_event import AuditEvent
from django_spec_agents.policy.audit.types.audit_logger import AuditLogger
from pydantic_ai import Agent, CancellationToken, DeferredToolResults
from pydantic_ai.exceptions import RunCancelled, RunTimedOut
from pydantic_ai.messages import ModelMessage, ModelResponse

from django_ag_ui.agent.build_client_context_toolset import build_client_context_toolset
from django_ag_ui.agent.build_untrusted_context import build_untrusted_context
from django_ag_ui.agent.compacted_away_turn import compacted_away_turn
from django_ag_ui.agent.continued_history import continued_history
from django_ag_ui.agent.full_transcript import full_transcript
from django_ag_ui.agent.guarded_stream import guarded_stream
from django_ag_ui.agent.heartbeat_stream import heartbeat_stream
from django_ag_ui.agent.inject_compaction_events import inject_compaction_events
from django_ag_ui.agent.inject_invalidation_events import inject_invalidation_events
from django_ag_ui.agent.inject_subagent_events import inject_subagent_events
from django_ag_ui.agent.lend_answered_by import lend_answered_by
from django_ag_ui.agent.lend_stored_ids import lend_stored_ids
from django_ag_ui.agent.live_runs import LiveRuns
from django_ag_ui.agent.outcome_agui_adapter import OutcomeAGUIAdapter
from django_ag_ui.agent.park_run import ParkRun
from django_ag_ui.agent.reasoning_filter import drop_reasoning_events
from django_ag_ui.agent.run_owner import RunOwner
from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.agent.stamp_approval_prompts import stamp_approval_prompts
from django_ag_ui.agent.stamp_parent_run_id import stamp_parent_run_id
from django_ag_ui.agent.strip_binary_content import strip_binary_content
from django_ag_ui.agent.utils import ERROR_CODE, IDENTIFIER_TOO_LONG_CODE, without_answered_by
from django_ag_ui.agent.with_stored_activities import with_stored_activities
from django_ag_ui.agent.without_subagent_messages import without_subagent_messages
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.utils import messages_to_jsonable


class AgentSession:
    """Per-run orchestration between the HTTP transport and the agent.

    Owns everything one AG-UI run needs after the transport has authenticated
    the request, parsed the ``RunAgentInput``, and built the agent — and before
    the response object exists: the ``AGUIAdapter`` (an ``OutcomeAGUIAdapter``,
    the stock one plus a tool call's outcome), the composed event stream
    (native → transformed → reasoning-filtered → encoded → disconnect-guarded),
    and persistence on all three of a run's exits — completed, failed and
    cancelled — the last two audited as well.

    Splitting it from [`DjangoAGUIView`][django_ag_ui.DjangoAGUIView]
    makes the streaming pipeline testable without a ``StreamingHttpResponse``
    (drive [`stream`][django_ag_ui.AgentSession.stream] directly) and keeps the
    SSE transport swappable — a future WebSocket transport reuses the session
    unchanged.

    A resumed or forked run passes the source run's snapshot as
    ``message_history``, which makes the history the server's: the run takes
    only the client's new turn from ``run_input``. ``source_messages`` is then
    the source thread's stored conversation, if there is one, and the snapshot's
    messages are stored under the ids it holds for them rather than fresh ones.

    Outside a resume, ``stored_messages`` is the thread's stored conversation: the
    save keeps each activity in it that the client left out of the messages it
    posted, as far as the posted turns agree with the stored ones.

    With ``park``, a run that ends on deferred calls it names finishes ``WAITING``.
    """

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
        stored_messages: list[Message] | None = None,
        model: Any = None,
        instructions: str | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
        timeout: float | None = None,
        deferred_tool_results: DeferredToolResults | None = None,
        park: ParkRun | None = None,
        parent_run_id: str | None = None,
    ) -> None:
        run_input = without_subagent_messages(run_input)
        self._agent = agent
        self._parent_run_id = parent_run_id
        self._run_input = run_input
        self._request = request
        # Per-run overrides, kept off the agent so one built agent serves every run.
        self._model = model
        self._instructions = instructions
        self._toolsets = toolsets
        self._capabilities = capabilities if park is None else [*(capabilities or []), park]
        self._park = park
        self._waiting: tuple[RunWaiting, ...] = ()
        self._deps = deps
        self._audit_logger = audit_logger
        self._config = config
        self._conversation_store = conversation_store
        self._message_history = message_history
        self._source_messages = source_messages
        self._stored_messages = stored_messages or []
        self._deferred_tool_results = deferred_tool_results
        self._prior: list[Message] | None = None
        self._refusal = compacted_away_turn(message_history, source_messages, run_input.messages)
        self._timeout = timeout if timeout is not None else config.run_timeout_seconds
        self._token = CancellationToken()
        self._ended: RunOutcome | None = None
        self._forward_reasoning = config.forward_reasoning
        self._adapter = OutcomeAGUIAdapter(
            agent,
            run_input,
            manage_system_prompt=cast("Any", config.manage_system_prompt),
            manage_message_history="client" if message_history is None else "server",
            allow_uploaded_files=config.allow_uploaded_files,
            include_usage=config.include_usage,
        )

    def stream(
        self, owner: RunOwner | None = None, *, detached: bool = False
    ) -> AsyncIterator[str]:
        """The encoded AG-UI event stream for this run, as its owner's subscriber.

        Encoding, the heartbeat and the disconnect guard are the subscriber's. On a
        disconnect the guard tells the owner: a ``detached`` run carries on, any other
        is cancelled, which closes the provider's stream and persists the partial.
        """
        owner = owner if owner is not None else self.own()
        encoded = self._adapter.encode_stream(owner.subscribe())
        if self._config.heartbeat_seconds > 0:
            encoded = heartbeat_stream(encoded, interval=self._config.heartbeat_seconds)
        return guarded_stream(encoded, on_cancel=owner.detach if detached else owner.cancel)

    def own(
        self, run_store: RunStore | None = None, *, live_runs: LiveRuns | None = None
    ) -> RunOwner:
        """The run's owner: the pipeline from the provider's stream to persist-on-error.

        Composed by hand (rather than ``adapter.run_stream``) so the owner keeps a
        reference to the *native* event stream, whose context manager owns the
        provider's streaming request, and to the sub-agent stage racing it.
        """
        transcript = RunTranscript()
        if self._refusal is not None:
            return self._refused_owner(transcript, run_store)
        native = self._adapter.run_stream_native(
            message_history=self._message_history,
            deferred_tool_results=self._deferred_tool_results,
            deps=self._deps,
            model=self._model,
            instructions=self._run_instructions(),
            toolsets=self._run_toolsets(),
            capabilities=self._capabilities,
            cancellation_token=self._token,
            timeout=self._timeout,
            run_id=self._run_input.run_id or None,
        )
        events = self._adapter.transform_stream(
            native,
            on_complete=self._on_complete(transcript),
            on_cancel=self._on_timeout(transcript),
        )
        events = stamp_parent_run_id(events, self._parent_run_id)
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
        return RunOwner(
            self._persist_on_error(events, transcript),
            native_events=native,
            racing_stages=(subagents,),
            on_cancel=self._on_cancel(transcript),
            ended=lambda: self._ended,
            waiting=lambda: self._waiting,
            token=self._token,
            run_store=run_store,
            request=self._request,
            thread_id=self._run_input.thread_id,
            run_id=self._run_input.run_id,
            run_input=self._run_input,
            live_runs=live_runs,
        )

    def _refused_owner(self, transcript: RunTranscript, run_store: RunStore | None) -> RunOwner:
        """An owner whose stream is only the refusal: the agent never runs, the row is untouched."""
        refused = self._refused_events()
        return RunOwner(
            self._persist_on_error(refused, transcript),
            native_events=refused,
            on_cancel=self._on_cancel(transcript),
            ended=lambda: self._ended,
            token=self._token,
            run_store=run_store,
            request=self._request,
            thread_id=self._run_input.thread_id,
            run_id=self._run_input.run_id,
        )

    async def _refused_events(self) -> AsyncIterator[BaseEvent]:
        yield RunStartedEvent(thread_id=self._run_input.thread_id, run_id=self._run_input.run_id)
        yield RunErrorEvent(message=self._refusal or "", code=ERROR_CODE)

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
        """The session's toolsets, plus the client-context tool when this run has one."""
        added: list[Any] = []
        if self._config.run_context.delivery == "tool":
            block = self._client_context_block()
            if block is not None:
                added.append(build_client_context_toolset(block))
        return [*(self._toolsets or []), *added] if added else self._toolsets

    async def _persist_on_error(
        self,
        events: AsyncIterator[BaseEvent],
        transcript: RunTranscript,
    ) -> AsyncIterator[BaseEvent]:
        """Pass ``events`` through, persisting the exchange if the run errors."""
        finalize = self._on_error(transcript)
        async for event in events:
            if isinstance(event, RunErrorEvent):
                await finalize(event.message, persist=event.code != IDENTIFIER_TOO_LONG_CODE)
                event = self._client_facing_error(event)
            yield event

    def _client_facing_error(self, event: RunErrorEvent) -> RunErrorEvent:
        """``event`` with its exception text withheld, unless detail is opted in."""
        if self._config.tool_failure.include_detail or event.code == IDENTIFIER_TOO_LONG_CODE:
            return event
        if self._refusal is not None:
            return event
        return event.model_copy(
            update={"message": "The run failed. The failure has been recorded."}
        )

    def _prior_messages(self) -> list[Message]:
        """Everything before this run's own output, in the client's own shape."""
        if self._prior is None:
            if self._message_history is None:
                self._prior = with_stored_activities(
                    self._run_input.messages, self._stored_messages
                )
            else:
                history, new_turn = continued_history(
                    self._message_history, self._run_input.messages
                )
                kept = strip_binary_content(OutcomeAGUIAdapter.dump_messages(history))
                if self._source_messages:
                    kept = lend_answered_by(kept, self._source_messages)
                    kept = lend_stored_ids(
                        kept,
                        self._source_messages,
                        reserved={message.id for message in new_turn},
                    )
                    kept = full_transcript(
                        kept,
                        self._source_messages,
                        resubmitted={message.id for message in new_turn},
                    )
                self._prior = [*kept, *(without_answered_by(message) for message in new_turn)]
        return self._prior

    def _on_complete(self, transcript: RunTranscript) -> Callable[[Any], Awaitable[None]] | None:
        """The adapter's ``on_complete`` callback: note a park, then persist the conversation."""
        save = self._message_saver()
        if save is None and self._park is None:
            return None

        async def _on_complete(result: Any) -> None:
            if self._park is not None:
                self._waiting = self._park.waiting(result.output)
                if self._waiting:
                    self._ended = RunOutcome.WAITING
                    await self._snapshot_park(result.all_messages())
            if save is None:
                return
            dumped = OutcomeAGUIAdapter.dump_messages(result.new_messages())
            new = strip_binary_content(transcript.with_streamed_steps(dumped))
            await save([*self._prior_messages(), *new])

        return _on_complete

    async def _snapshot_park(self, messages: list[ModelMessage]) -> None:
        """Save the parked history as an interrupted snapshot, which ``StepPersistence`` skips."""
        try:
            from pydantic_ai_harness.step_persistence import ContinuableSnapshot, StepPersistence
        except ImportError:
            return
        persistence = next(
            (c for c in self._capabilities or [] if isinstance(c, StepPersistence)), None
        )
        if persistence is None or persistence.run_id is None:
            return
        await persistence.store.save_snapshot(
            ContinuableSnapshot(
                run_id=persistence.run_id,
                step_index=sum(isinstance(m, ModelResponse) for m in messages),
                messages=messages,
                conversation_id=self._run_input.thread_id,
                parent_run_id=persistence.parent_run_id,
                agent_name=persistence.agent_name,
                state="interrupted",
            )
        )

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

    def _run_finalizer(self, transcript: RunTranscript) -> Callable[..., Awaitable[None]]:
        """Persist the partial exchange and audit the run, given a reason."""
        save = self._message_saver()
        audit = self._audit_logger
        started = time.perf_counter()
        run_ref = json.dumps(
            {"run_id": self._run_input.run_id, "thread_id": self._run_input.thread_id},
            sort_keys=True,
        )
        ip_address = self._request.META.get("REMOTE_ADDR")

        async def _finalize(error: str, *, persist: bool = True) -> None:
            # A refused id is never saved; a cancel reaches here unchecked, so suppress the refusal.
            if save is not None and persist and self._refusal is None:
                with contextlib.suppress(IdentifierTooLongError):
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

    def _on_timeout(self, transcript: RunTranscript) -> Callable[[RunCancelled], Awaitable[None]]:
        """The adapter's ``on_cancel``: persist the partial exchange, then audit."""
        finalize = self._run_finalizer(transcript)

        async def _on_timeout(cancelled: RunCancelled) -> None:
            timed_out = isinstance(cancelled, RunTimedOut)
            self._ended = RunOutcome.TIMED_OUT if timed_out else RunOutcome.CANCELLED
            await finalize("cancelled: run timed out" if timed_out else "cancelled: run stopped")

        return _on_timeout

    def _on_error(self, transcript: RunTranscript) -> Callable[..., Awaitable[None]]:
        """The ``RUN_ERROR`` counterpart: persist the partial exchange, then audit."""
        finalize = self._run_finalizer(transcript)

        async def _on_error(message: str, *, persist: bool = True) -> None:
            await finalize(f"run failed: {message}", persist=persist)

        return _on_error


__all__ = ["AgentSession"]
