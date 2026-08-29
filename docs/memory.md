# Per-user memory

An agent that remembers a user between sessions — "prefers metric units", "works
in Berlin" — without the client re-sending it every run.

**This package adds nothing for it.** `pydantic-ai-harness` ships a complete
memory capability, and `capabilities=` already carries it. What follows is a
recipe, not a feature: the parts are upstream's and
[django-pydantic-agent](https://github.com/Artui/django-pydantic-agent)'s, and
the only thing worth writing down is how they fit together here and what to turn
on first.

Needs the `[harness]` extra.

## Turn `TOOL_GUARD` on before you turn memory on

Not a footnote. Memory may be allowed to steer what the model **says**; it must
not be allowed to decide what the model **does**.

Memory is *durable*, *model-written*, and replayed into every later run. A user
pastes text that reads as an instruction; the capability's own guidance tells the
model to store useful facts proactively; `write_memory` persists it; every later
run of that user injects it verbatim — and nothing in the loop ever re-asks
whether it belongs there. Upstream is candid about this: the delimited user-role
part "lowers its authority", but it "is not a hard prompt-injection boundary",
and memory records carry no provenance.

[`TOOL_GUARD`](configuration.md#tool_guard) is what holds the line, because it
reads the `x-destructive` stamp **server-side**: a memory-planted "call
`refund_order` first" still hits the approval interrupt. With the guard off,
every server-side tool runs the moment the model calls it.

```python
DJANGO_AG_UI = {
    "TOOL_GUARD": {"ENABLED": True},
}
```

A mount with memory on and the guard off is a durable, user-writable path to
unattended destructive calls. Each setting is defensible alone; they are only
wrong together.

## The wiring

```python
from django_pydantic_agent import memory_namespace_for_user
from django_pydantic_agent.contrib.store.default_memory_store import (
    DefaultMemoryStore,
)
from pydantic_ai_harness.memory import Memory

from django_ag_ui import AGUIServer

server = AGUIServer(
    registry,
    capabilities=[
        Memory(
            DefaultMemoryStore(),
            namespace=lambda ctx: memory_namespace_for_user(ctx.deps.user),
        )
    ],
)
```

That is the whole integration. The capability contributes four tools —
`write_memory`, `read_memory`, `delete_memory`, `search_memory` — its own
guidance through `get_instructions`, and a bounded block of the stored content
injected into each request.

There is **no settings key**, and there will not be one:
`DJANGO_AG_UI["CAPABILITIES"]` was removed in 0.19.0 and now raises. Memory
attaches as a constructor argument like every other capability.

### `namespace` is the user; `agent_name` is the mount

The harness composes its scope key as `f"{namespace}/{agent_name}"`, so two
mounts sharing one store are partitioned by construction:

```python
Memory(store, agent_name="internal")  # one mount
Memory(store, agent_name="public")  # another
```

No scoping wrapper is needed, unlike `ScopedConversationStore` for threads — the
capability solved it upstream.

### Why the resolver reads `ctx.deps` and not a request

`capabilities=` is resolved **once**, when the endpoint is constructed, and one
view instance serves every request — so nothing request-shaped can be closed over
in that list. It is the same invariant the endpoint states for its agent: the
agent carries only what the constructor fixed, and anything per-request rides the
*run*.

Memory fits that without help. Pydantic-AI clones every capability per run and
`Memory` re-resolves its namespace in the clone, so one agent built once serves
every user with no cross-contamination. `AgentDeps.user` is set by this package
from the authenticated request, so the namespace is server-resolved and not
something a client can choose.

A store built per request is the one thing that does **not** fit, because there
is no request at construction. `DefaultMemoryStore()` is namespace-scoped for
exactly this reason: it takes the owner from the leading path segment, which is
the namespace the resolver just produced.

### Anonymous callers

The endpoint refuses them by default
([`require_authenticated`](configuration.md#authentication-anonymous-scoping)),
which is the right setting for memory: an anonymous visitor has no durable
identity worth remembering under. If you serve anonymous runs deliberately, note
that `memory_namespace_for_user` returns one shared `anon` namespace — with no
request there is no session to key a per-visitor bucket on.

## Which store

Any object satisfying the harness's `MemoryStore` protocol. The harness bundles
`InMemoryStore`, `FileStore`, `SqliteMemoryStore` and `PostgresMemoryStore`;
django-pydantic-agent's `DefaultMemoryStore` is the Django one, and the one to
reach for here — it neutralises the fence tags on write, applies per-owner file
and byte ceilings the capability does not have, and carries a `purge(owner_id)`
for erasure. See its
[storage docs](https://artui.github.io/django-pydantic-agent/storage/) for the
details.

The bundled stores are single-tenant: their scope lives only in the path.
That is fine here because the namespace comes from `ctx.deps.user`, but it is
worth knowing before you point one at production.

## Let people see what is remembered

**Do not ship memory without a surface that lists it.** A model quietly
accumulating durable notes about someone, replayed into every future session with
nothing showing them, is what makes this feel spooky rather than tailored.

The minimum honest surface is **list, read and delete**, and the protocol
provides all three (`list_paths`, `read`, `delete`) — so this is a page in your
own app, not something to wait for. A memory-management UI is deliberately not in
the web component: it is a settings surface the host app owns.

The one widget-side part worth having is a "Memory updated" run notice, and it
costs nothing new: push an `ActivitySnapshotEvent` and register a renderer for it
client-side, the same way [charts](charts.md) do.

## Erasure

Memory is durable personal data written *about* a user, so an erasure request has
to reach it. The protocol has no bulk or prefix delete;
django-pydantic-agent ships `purge(owner_id)` and a management command for it:

```bash
python manage.py agent_store_purge_memory u-42
```

It is deliberately not wired to a `post_delete` signal on your user model —
whether deleting an account erases its memory is your product's policy.

## Preferences are not memory

They look like one feature and are two. The difference is **who reads them**:

| | Memory | Preferences |
| --- | --- | --- |
| Author | the model, or the app | the user, deliberately |
| Read by | **the model**, as prompt text | **the server**, to shape the run |
| Vocabulary chosen by | the model | you |
| Lifetime | until corrected or erased | until changed |

That difference settles the delivery question before it is asked. A preference
reaches the model too — "answer in German" has to — but it arrives through a slot
*you* wrote, where your project chose the sentence and the stored value only
selected among sentences you already sanctioned. Memory arrives as text the model
itself composed. Same destination, opposite authority.

Building one mechanism for both would launder model-written text into the
operator channel, which is the thing to avoid.

### The recipe: read your own row in the hooks that already exist

Preferences need **no library support and no new model here**. Two of the four
things people usually ask for already have a reader:

```python
def _prefs(request):
    return UserAgentPreference.objects.filter(user=request.user).first()


def model_for(request):
    prefs = _prefs(request)
    return prefs.model if prefs and prefs.model else None


def instructions_for(request):
    prefs = _prefs(request)
    base = "You are a helpful assistant."
    if prefs and prefs.language:
        # The project chose this sentence; the stored value only selects it.
        base += f" Reply in {prefs.get_language_display()}."
    if prefs and prefs.tone == "terse":
        base += " Keep answers short and factual."
    return base


server = AGUIServer(
    registry,
    model_for_request=model_for,
    instructions_for_request=instructions_for,
)
```

`model_for_request` and `instructions_for_request` are the **only** per-request
hooks on the endpoint, and that is on purpose — a hook handed the whole request
could read anything off it, which is not a set the agent's reuse can be reasoned
about against. Default model, tone and language all fall out of these two.

`UserAgentPreference` above is **your** model. This package does not ship one and
should not: nothing it owns would read the table, the reader is your own lambda,
and the schema would be guessing at your product's taxonomy.

### The one preference with no path

Per-user **reasoning disclosure** has no hook.
[`FORWARD_REASONING`](configuration.md#forward_reasoning) is a scalar resolved
once into the endpoint, and scalars read per request could only ever be global.
So "let *this* user see the thinking" is not expressible today.

Recorded as a decision rather than an omission: it stays that way until a
consumer asks. Worth noting that the rule's usual justification does not quite
cover this field — the examples that motivate it (a guard policy, a retry budget,
an upload cap) are all *policy*, where per-user variation is a smell, whereas
reasoning disclosure is *presentation*.
