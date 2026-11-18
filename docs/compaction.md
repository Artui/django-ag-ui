# Compaction and agent skills

A long, tool-heavy run grows its message history until it crowds the model's
context window. [`pydantic-ai-harness`](https://github.com/pydantic/pydantic-ai-harness)'s
**`compaction`** capabilities trim it; its **`Skills`** capability loads folders of
`SKILL.md` instructions on demand.

Both are pure composition over the [`capabilities=`](configuration.md#capabilities)
seam plus the optional `[harness]` extra — **no django-ag-ui code involved**. The
capabilities themselves, and which compaction strategy to pick, are documented
where the seam lives: see
[django-spec-agents' integrations guide](https://artui.github.io/django-spec-agents/integrations/#harness-upstream-capabilities).

```bash
pip install "django-ag-ui[harness]"
```

```python
from pydantic_ai_harness.compaction import SlidingWindowCompaction

server = AGUIServer(capabilities=[SlidingWindowCompaction(max_messages=80, keep_messages=40)])
```

!!! note "Upgrading from harness 0.12"

    This strategy was called `SlidingWindow` before harness 0.13 renamed it to
    `SlidingWindowCompaction`. The old name survives as a deprecated alias that
    warns on import and is slated for removal, so the examples here use the new
    one. The `[harness]` extra floors at `>=0.69`, well past the rename.

## Choosing a strategy

Every strategy is a capability, so each goes in the same `capabilities=` list. Three
cover most mounts:

| Strategy | When it compacts | What it writes |
| --- | --- | --- |
| `SlidingWindowCompaction` | the history passes a message or token count | drops the oldest turns |
| `RelevanceCompaction` | the history passes a token budget | a judge model scores each step: it keeps what is needed verbatim and summarizes or truncates the rest |
| `CompactOnRequest` | the model calls its `compact_context` tool at a break in its task | runs the strategy it wraps, before the next model request |

Both ship in pydantic-ai-harness from 0.62, and the `[harness]` extra floors it at 0.69.
`RelevanceCompaction` calls its judge model once per step, so give it a small, fast
model:

```python
from django_ag_ui import CompactionObserver
from pydantic_ai_harness.compaction import RelevanceCompaction, SummarizingCompaction

server = AGUIServer(
    capabilities=[
        CompactionObserver(
            RelevanceCompaction(
                "anthropic:claude-haiku-5-5",
                summarizer=SummarizingCompaction(
                    model="anthropic:claude-haiku-5-5", max_messages=1
                ),
                max_fraction=0.8,
            )
        )
    ],
)
```

`CompactOnRequest` puts the timing in the model's hands. The strategy it wraps runs only
when asked, so its own trigger is bypassed. `min_tokens` refuses a request that would
save little, and `max_per_run` caps the requests in one run:

```python
from pydantic_ai_harness.compaction import CompactOnRequest, SummarizingCompaction

server = AGUIServer(
    capabilities=[
        CompactionObserver(
            CompactOnRequest(
                SummarizingCompaction(
                    model="anthropic:claude-haiku-5-5", max_messages=1, keep_messages=6
                ),
                min_tokens=30_000,
                max_per_run=2,
            )
        )
    ],
)
```

A strategy that summarizes (`SummarizingCompaction`, or `RelevanceCompaction` with a
`summarizer`) stores its summary in the history as a `CompactionPart`. On a resumed
thread, the stored conversation shows it as a `pydantic_ai_compaction` activity, and the
messages around it keep the ids they were first stored under. That holds even though
pydantic-ai merges the summary with the prompt kept before it.

That is the whole adoption. The rest of this page is about the one thing that
composition alone cannot give you: **telling the user it happened.**

## Why an indicator needs code at all

Compaction is deliberately invisible. It runs inside `before_model_request`,
mutates the message list, and **emits nothing** — upstream calls it "transparent
to the rest of the agent run". For the model that is exactly right. For a person
watching a long conversation it is not: earlier turns quietly stop informing the
answers, with no explanation for the change in behaviour.

Nothing in the AG-UI stream carries that signal, so `django-ag-ui` observes it and
puts it on the wire.

## Turning it on

Wrap the compaction capability in `CompactionObserver`:

```python
from django_ag_ui import CompactionObserver
from pydantic_ai_harness.compaction import SlidingWindowCompaction

server = AGUIServer(
    capabilities=[CompactionObserver(SlidingWindowCompaction(max_messages=80, keep_messages=40))],
)
```

Opt-in by construction: pass the strategy unwrapped and you get today's behaviour
with nothing emitted. Wrapping changes **only** what the client is told — the
compaction itself is untouched, because `CompactionObserver` subclasses
pydantic-ai's `WrapperCapability` and overrides one hook, delegating the rest of
the protocol (ordering, deferral, hook introspection) to the wrapped capability.

## What reaches the client

Each firing produces one standard AG-UI `ACTIVITY_SNAPSHOT` event:

```json
{
  "type": "ACTIVITY_SNAPSHOT",
  "messageId": "compaction-3f1c…",
  "activityType": "compaction",
  "content": { "removed": 8, "before": 10, "after": 2 }
}
```

An activity event rather than a `CUSTOM` one, so the wire stays vanilla AG-UI —
any AG-UI client can render it as an activity message, and ours is not
privileged. The event is interleaved **immediately before** the events of the
turn that ran with the shortened history, which is where a reader wants it.

Each firing gets a fresh `messageId`: a compaction is a distinct occurrence in
the transcript, not a mutation of a previous one.

Handle it with `@ag-ui/client`'s `onActivitySnapshotEvent`, matching on
`activityType === "compaction"` (exported as `COMPACTION_ACTIVITY_TYPE`).

## What it does not detect

Detection is a message-count comparison across the wrapped call, because that is
all the upstream contract exposes — a strategy mutates `request_context.messages`
and returns the context.

So a strategy that **rewrites** history without shortening it does not register.
`SummarizingCompaction` replacing twenty messages with one summary does (the
count drops); a hypothetical strategy that swapped message *content* in place
would not. That matches what the indicator claims — turns were dropped — rather
than over-promising a general "history was modified" signal.

## Concurrency

The observer records into a per-run [`ContextVar`](https://docs.python.org/3/library/contextvars.html),
not into itself. A consumer builds the capability once at configuration time and
the same instance serves every request, so instance state would interleave
concurrent runs into each other's transcripts. Context variables are per-task, so
each run reads and writes its own.

A capability used outside this transport — a management command, a test — simply
has no sink bound and records nothing.
