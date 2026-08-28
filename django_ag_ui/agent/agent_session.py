"""``AgentSession`` — one AG-UI run's orchestration, apart from the transport."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, cast

from ag_ui.core import BaseEvent, Message, RunErrorEvent
from django.http import HttpRequest
from django_pydantic_agent.agent.types.agent_deps import AgentDeps
from django_pydantic_agent.persistence.anonymous_operation_error import AnonymousOperationError
from django_pydantic_agent.persistence.null_conversation_store import NullConversationStore
from django_pydantic_agent.persistence.types.conversation import Conversation
from django_pydantic_agent.persistence.types.conversation_store import ConversationStore
from django_pydantic_agent.persistence.utils import owner_id_for, resolve_owner_id
from django_pydantic_agent.policy.audit.types.audit_event import AuditEvent
from django_pydantic_agent.policy.audit.types.audit_logger import AuditLogger
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.ui.ag_ui import AGUIAdapter

from django_ag_ui.agent.build_untrusted_context import build_untrusted_context
from django_ag_ui.agent.guarded_stream import guarded_stream
from django_ag_ui.agent.inject_compaction_events import inject_compaction_events
from django_ag_ui.agent.inject_invalidation_events import inject_invalidation_events
from django_ag_ui.agent.reasoning_filter import drop_reasoning_events
from django_ag_ui.agent.run_transcript import RunTranscript
from django_ag_ui.agent.stamp_approval_prompts import stamp_approval_prompts
from django_ag_ui.agent.strip_binary_content import strip_binary_content
from django_ag_ui.config.types.ag_ui_config import AGUIConfig
from django_ag_ui.persistence.utils import messages_to_jsonable


class AgentSession:
    """Per-run orchestration between the HTTP transport and the agent.

    Owns everything one AG-UI run needs after the transport has authenticated
    the request, parsed the ``RunAgentInput``, and built the agent — and before
    the response object exists: the ``AGUIAdapter``, the composed event stream
    (native → transformed → reasoning-filtered → encoded → disconnect-guarded),
    and persistence on all three of a run's exits — completed, failed and
    cancelled — the last two audited as well.

    Splitting it from [`DjangoAGUIView`][django_ag_ui.DjangoAGUIView]
    makes the streaming pipeline testable without a ``StreamingHttpResponse``
    (drive [`stream`][django_ag_ui.AgentSession.stream] directly) and keeps the
    SSE transport swappable — a future WebSocket transport reuses the session
    unchanged.
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
        model: Any = None,
        instructions: str | None = None,
        toolsets: list[Any] | None = None,
        capabilities: list[Any] | None = None,
    ) -> None:
        self._agent = agent
        self._run_input = run_input
        self._request = request
        # Per-run overrides, kept off the agent so one built agent serves every
        # run. Pydantic-AI's seam: ``model`` replaces the agent's for this run,
        # while ``toolsets`` / ``capabilities`` / ``instructions`` are
        # *additional* — and additional instructions are a replacement here,
        # because the agent this session is handed carries none of its own.
        self._model = model
        self._instructions = instructions
        self._toolsets = toolsets
        self._capabilities = capabilities
        # Everything request-scoped the agent needs reaches tools / toolsets /
        # capabilities through ``ctx.deps``, so nothing in the agent closes over
        # this request.
        self._deps = deps
        self._audit_logger = audit_logger
        self._config = config
        self._conversation_store = conversation_store
        # Server-authoritative history to seed the run with (a resumed / forked
        # run loads it from the step store). ``None`` for a normal run, where the
        # client owns the prior turns and sends them in ``run_input``.
        self._message_history = message_history
        # Memoised on first use rather than built here: dumping the prior
        # conversation is proportional to its whole length, and a run with
        # nothing to persist to never needs it at all.
        self._prior: list[Message] | None = None
        self._forward_reasoning = config.forward_reasoning
        self._adapter = AGUIAdapter(
            agent,
            run_input,
            # A plain string at the config boundary; the adapter types it as
            # the Literal["server", "client"].
            manage_system_prompt=cast("Any", config.manage_system_prompt),
            allow_uploaded_files=config.allow_uploaded_files,
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
        # The adapter composes ``[*message_history, *client_messages]``, so a
        # resumed run's server-loaded snapshot is prepended to the new turn.
        native = self._adapter.run_stream_native(
            message_history=self._message_history,
            deps=self._deps,
            model=self._model,
            instructions=self._run_instructions(),
            toolsets=self._toolsets,
            capabilities=self._capabilities,
        )
        events = self._adapter.transform_stream(native, on_complete=self._on_complete())
        if not self._forward_reasoning:
            events = drop_reasoning_events(events)
        # Unconditional: a no-op unless a ``CompactionObserver`` is in the
        # capability list, and a flag would mean a second way to express the
        # same opt-in.
        events = inject_compaction_events(events)
        # Unconditional for the same reason: a no-op unless something calls
        # ``publish_invalidation`` during the run, and a flag would mean a second
        # way to express the same opt-in. It wraps *outside* the run, so the sink
        # exists before the first tool can write.
        events = inject_invalidation_events(events)
        # Only when there is something to say: the wrapper has to track tool-call
        # ids to match an interrupt back to its tool, and an endpoint that gates
        # nothing should not pay for that on every run.
        if self._config.approval_prompts:
            events = stamp_approval_prompts(events, prompts=self._config.approval_prompts)
        # Recording costs the run's whole output — every text and tool-argument
        # delta held as its own object for the length of the stream — so it is
        # paid for only where something reads it back. The transcript exists to
        # persist a run that never completes, and with the default
        # ``NullConversationStore`` there is nowhere to persist it to.
        if self._persists():
            events = transcript.observe(events)
        # The third exit. ``on_complete`` covers a run that finishes and the
        # guard below covers a client that disconnects; the adapter offers no
        # error callback, so the terminal event is the only hook a *failing* run
        # can be persisted from.
        observed = self._persist_on_error(events, transcript)
        return guarded_stream(
            self._adapter.encode_stream(observed),
            native_events=native,
            on_cancel=self._on_cancel(transcript),
        )

    def _run_instructions(self) -> list[str] | str | None:
        """The operator instructions, plus this run's fenced client context.

        The delivery hook for what the client announced about the user's
        situation — ``RunAgentInput.context`` and the attachment refs riding the
        posted messages — which pydantic-ai's adapter deliberately leaves to the
        consumer.

        Operator instructions come **first** so the model reads the rules before
        the data; the block's closing line re-asserts that precedence where the
        data ends. Returning a sequence rather than one joined string is what
        keeps the client's text out of the operator's prompt string, off the
        persisted thread, and out of what streams back to the browser.
        """
        block = build_untrusted_context(self._run_input, config=self._config.run_context)
        if block is None:
            return self._instructions
        if self._instructions is None:
            return block
        return [self._instructions, block]

    async def _persist_on_error(
        self,
        events: AsyncIterator[BaseEvent],
        transcript: RunTranscript,
    ) -> AsyncIterator[BaseEvent]:
        """Pass ``events`` through, persisting the exchange if the run errors.

        Wraps the transcript observer rather than sitting inside it: an event
        reaches here only after the transcript has recorded it, so by the time
        ``RUN_ERROR`` arrives the transcript already holds the closing tool
        results the adapter emits for interrupted calls. Persisting any earlier
        would store a truncated exchange.

        The event's own text is redacted on its way out — see
        ``_client_facing_error``. The operator's copies are taken first, from
        the unredacted message.
        """
        finalize = self._on_error(transcript)
        async for event in events:
            if isinstance(event, RunErrorEvent):
                await finalize(event.message)
                event = self._client_facing_error(event)
            yield event

    def _client_facing_error(self, event: RunErrorEvent) -> RunErrorEvent:
        """``event`` with its exception text withheld, unless detail is opted in.

        Pydantic-AI builds ``RUN_ERROR`` as ``str(error)``, so an unhandled
        exception's own words reach the browser and are rendered in the
        transcript: an ORM error carrying SQL and a connection target, an
        ``OSError`` carrying a server path, a provider ``401`` echoing a masked
        key. That is the disclosure ``TOOL_FAILURE["INCLUDE_DETAIL"]`` exists to
        withhold one level down, and it is the same question, so it is the same
        answer — an exception message is written for an operator, not for a
        browser. The operator's copies keep the detail either way: the audit
        record is taken from the unredacted message above.

        Errors raised *outside* a tool — the store, the adapter, the model
        client — take this path whatever the failure policy is doing, which is
        why the redaction lives here rather than in the policy.
        """
        if self._config.tool_failure.include_detail:
            return event
        return event.model_copy(
            update={"message": "The run failed. The failure has been recorded."}
        )

    def _prior_messages(self) -> list[Message]:
        """Everything before this run's own output, in the client's own shape.

        Server-loaded history first (a resumed / forked run's snapshot, dumped to
        the wire shape), then the messages the client posted — the same order
        ``run_stream_native`` composes them in, so the stored thread reads the
        way the run did.

        **The client's messages are stored verbatim, never re-dumped** from the
        model's history: a round-trip through pydantic-ai's types regenerates
        every message id and drops the non-standard ``attachments`` field the web
        component rides on a user message, so a reloaded thread loses its
        attachment chips and the ids the model was told about match nothing
        stored. Two consequences: a client-posted system message reaches the row
        (inert — ``sanitize_messages`` still strips it before the model under the
        default ``MANAGE_SYSTEM_PROMPT="server"``), and stored user messages keep
        the client's id.

        The strip is asymmetric on purpose, and it is the invariant this method
        turns on: **the server never persists bytes it generated, and never
        discards bytes the client sent.** The snapshot arrives in pydantic-ai's
        types, where a prior run's ``read_attachment`` return still holds the
        file, so dumping it unstripped would write that file back into the row as
        base64. What the client posted is not this server's to edit.

        Memoised, because the answer cannot change during a run and up to three
        exits ask for it: a resumed run would otherwise dump, strip and retain
        an independent copy of the whole prior conversation for each. Callers
        splat the result rather than holding it, so the one list is shared
        safely.
        """
        if self._prior is None:
            self._prior = [
                *strip_binary_content(AGUIAdapter.dump_messages(self._message_history or [])),
                *self._run_input.messages,
            ]
        return self._prior

    def _on_complete(self) -> Callable[[Any], Awaitable[None]] | None:
        """The adapter's ``on_complete`` callback persisting the conversation.

        ``None`` when persistence is off (the default ``NullConversationStore``),
        so the stateless path adds no overhead. Otherwise the callback stores the
        prior exchange as the client shaped it plus **only** this run's new
        messages — ``result.new_messages()``, not ``all_messages()``, because the
        run's history already holds the client's turn and dumping the lot would
        re-derive it from the model's types (see ``_prior_messages``).

        The new messages are stripped of inlined file bytes here rather than
        anywhere the model reads: those bytes are the server's own doing (a
        ``read_attachment`` handing the model a PDF) and the model has to see
        them.
        """
        save = self._message_saver()
        if save is None:
            return None

        async def _on_complete(result: Any) -> None:
            new = strip_binary_content(AGUIAdapter.dump_messages(result.new_messages()))
            await save([*self._prior_messages(), *new])

        return _on_complete

    def _persists(self) -> bool:
        """Whether this run has anywhere to store what it produces.

        The default is ``NullConversationStore``, and the stateless path should
        pay for none of the machinery persistence needs — neither the buffered
        transcript nor the dumped prior conversation.
        """
        return not isinstance(self._conversation_store, NullConversationStore)

    def _owner_id(self) -> str | None:
        """The owner scope stamped onto the stored conversation.

        ``Conversation.owner_id`` is documented as the authorization scope, so
        handing every anonymous visitor the same ``None`` collapses them into
        one partition in any store that keys on the field as invited — visitor
        B's thread list would be visitor A's. An anonymous request is therefore
        scoped to the browser session instead, the same ``anon:<session_key>``
        bucket the reference stores derive for themselves.

        An existing session key is *used*, never created: this runs on the event
        loop, where creating one is a database write, and a store that refuses
        anonymous writes (the default) would have made that row for nothing. So
        a deployment with no session middleware still answers ``None`` — there
        is no per-visitor key to be had, and inventing one would be worse than
        saying so.
        """
        owner_id = owner_id_for(self._request)
        if owner_id is not None:
            return owner_id
        session = getattr(self._request, "session", None)
        if getattr(session, "session_key", None) is None:
            return None
        return resolve_owner_id(self._request, allow_anonymous=True)

    def _message_saver(self) -> Callable[[list[Message]], Awaitable[None]] | None:
        """A closure persisting AG-UI messages to the configured store.

        ``None`` when persistence is off — both the completed-run and the
        cancelled-run paths build their message list and hand it here, so the
        two persist with identical thread/owner scoping.
        """
        store: ConversationStore = self._conversation_store
        if not self._persists():
            return None
        thread_id: str = self._run_input.thread_id
        owner_id = self._owner_id()
        request = self._request

        async def _save(messages: list[Message]) -> None:
            conversation = Conversation(
                # The AG-UI wire shape is serialised here rather than inside the
                # substrate, which keeps transport-owned records.
                thread_id=thread_id,
                messages=messages_to_jsonable(messages),
                owner_id=owner_id,
            )
            try:
                await store.save(conversation, request=request)
            except AnonymousOperationError:
                # An anonymous run against a store that refuses anonymous writes
                # (the default): the run still streams, it just isn't saved,
                # rather than crashing an already-completed stream.
                return

        return _save

    def _run_finalizer(self, transcript: RunTranscript) -> Callable[[str], Awaitable[None]]:
        """Persist the partial exchange and audit the run, given a reason.

        Shared by the two non-completing exits. Persistence mirrors the
        completed-run shape — ``_prior_messages`` plus whatever the
        transcript observed — so a durable thread reflects the truncated
        exchange, matching the client, which keeps the partial assistant bubble.
        Going through ``_prior_messages`` is also what stops a **resumed** run
        that fails or is cancelled from persisting only the new turn and
        truncating the thread it was resuming.

        The audit record rides the existing ``record(AuditEvent)`` surface as a
        ``tool_name="agent.run"`` event rather than a new protocol method, so
        custom loggers keep working unchanged; ``duration_ms`` measures run start
        to the failure.

        The transcript half needs no binary strip: ``RunTranscript`` builds its
        messages out of AG-UI *events*, and inlined file bytes never travel the
        event stream — they exist only in dumped messages, which are stripped
        where they are dumped.

        The prior conversation is read **inside** ``_finalize`` rather than
        closed over: this runs once while the stream is composed and again on
        the generator's first step, so computing it here dumped and retained a
        second copy of the whole thread before a single token had streamed —
        and did it even with persistence off, where ``save`` is ``None`` and it
        can never be used.
        """
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
        """The ``RUN_ERROR`` counterpart: persist the partial exchange, then audit.

        A failed run is audited at the run level even though a failing *tool*
        is already recorded by the audit capability, because the two are not
        the same fact and not every run failure comes from a tool.
        """
        finalize = self._run_finalizer(transcript)

        async def _on_error(message: str) -> None:
            await finalize(f"run failed: {message}")

        return _on_error


__all__ = ["AgentSession"]
