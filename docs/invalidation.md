# Telling the page what moved

The agent writes — places an order, edits a record, changes a setting — and the
page the user is looking at still shows the old list, the old row, the old count.

!!! danger "Never reload the page yourself, and do not write an example that does"

    The user was probably typing. An agent-triggered reload or a blind refetch
    into a live form destroys unsaved input, and the person who lost it has no
    idea why — from their side the page simply threw their work away on its own.

    This library **cannot** reload for you and deliberately offers no option that
    would. What arrives is a list of names; what your page does about them is
    yours to decide, and the right answer for a screen with a dirty form is
    usually a banner offering to refresh, not a refresh.

    ```js
    chat.addEventListener("ag-ui-invalidate", (e) => {
      if (formIsDirty()) {
        showBanner("This data changed. Refresh when you're ready.", e.detail.keys);
        return;
      }
      refetch(e.detail.keys);
    });
    ```

    A one-line "reload on invalidation" snippet is the thing someone will copy,
    which is why there isn't one on this page.

## You may already have enough

Before reaching for any of this: **`ag-ui-run-finished` already fires** on every
finished interaction and already says whether a server-side tool ran. If your
page refetches everything on that, it is correct today. It is only *blunt* —
it refetches the whole screen once, at the end.

Everything below is precision on that channel, not a replacement for it. Adopt it
when the coarse refetch is too slow, too broad, or too late; skip it otherwise.

## Naming what moved

```python
from django.db import transaction

from django_ag_ui import publish_invalidation


@agent.tool_plain
def place_order(sku: str) -> str:
    with transaction.atomic():
        order = Order.objects.create(sku=sku)
        transaction.on_commit(
            lambda: publish_invalidation("orders", f"orders/{order.pk}", reason="place_order")
        )
    return f"Ordered {sku}."
```

The keys reach the browser as they are announced, **during** the run — so a
long multi-step run refreshes the list as its third write lands rather than five
minutes later when everything finishes.

### The keys are yours

They are opaque strings. This package never interprets them, compares them to
anything, or requires a scheme. That is deliberate: every alternative — model
labels, spec names, URLs, MCP-style URIs — encodes one side's vocabulary into a
contract both sides have to read, and the two sides here are a Django app and a
frontend that may have no notion of Django models at all.

Use whatever your data layer already keys on. If that is TanStack Query, use its
query keys.

!!! warning "Name the collection too — matching is exact, never by prefix"

    `orders/42` does **not** invalidate `orders`. A prefix rule would be this
    package guessing at a scheme it does not own, and it fails both ways:
    `orders/1` would match `orders/11`.

    So a write to one row that should also refresh the list names both:

    ```python
    publish_invalidation("orders", f"orders/{order.pk}", reason="place_order")
    ```

    Your *host* may match hierarchically — in your vocabulary the scheme is known
    and hierarchy is the point. That is yours to interpret, not this package's to
    guess.

## Announce after the commit, not during

!!! danger "This is wrong by default, not in an edge case"

    `ServiceSpec` is `atomic=True` unless told otherwise, so announcing inline
    fires **inside** the transaction. The page then refetches data that has not
    committed — and may never commit — and caches the *old* row as fresh. That is
    worse than not having told it.

    `transaction.on_commit` is the answer, and it has a second half: register the
    callback **from the thread that owns the transaction**. Django connections
    are thread-local, so code running on the event loop sees no transaction at
    all, and a callback registered there runs immediately — announcing early
    while looking entirely correct. In practice that means registering from
    inside whatever `sync_to_async(..., thread_sensitive=True)` wrapper is
    already doing the write, which is where the ORM call lives anyway.

## The other route: returning it as tool metadata

`resource_invalidation` builds the same event for a tool that would rather return
it than queue it — the route [charts](charts.md) documents:

```python
from pydantic_ai.messages import ToolReturn

from django_ag_ui import resource_invalidation


@agent.tool_plain
def archive_project(project_id: int) -> ToolReturn:
    Project.objects.filter(pk=project_id).update(archived=True)
    return ToolReturn(
        return_value="Archived.",
        metadata=resource_invalidation("projects", f"projects/{project_id}"),
    )
```

Reach for this when nothing is being written transactionally. It cannot answer
the ordering question above — the tool has to return before its metadata is
forwarded — so anything inside `transaction.atomic()` wants `publish_invalidation`
instead.

## What the browser gets

Two things, and the second is why adoption is a one-line change.

An **`ag-ui-invalidate`** event as each announcement arrives, live during the run:

```js
chat.addEventListener("ag-ui-invalidate", (e) => {
  e.detail.keys;    // ["orders", "orders/42"]
  e.detail.reason;  // "place_order", or null
});
```

And an **`invalidated`** field on the existing `ag-ui-run-finished` detail,
de-duplicated and in order — so a page already listening there upgrades in place:

```js
chat.addEventListener("ag-ui-run-finished", (detail) => {
  if (detail.invalidated.length > 0) refetchOnly(detail.invalidated);
  else if (detail.tools.some((t) => t.side === "server")) refetchEverything();
});
```

That `else` is the whole compatibility story. Nothing negotiates and nothing
handshakes:

| Server | Client | Result |
| --- | --- | --- |
| old | old | coarse refetch, as today |
| new | old | the `CUSTOM` event is ignored; coarse refetch still fires |
| old | new | `invalidated` is empty; the `else` branch runs |
| new | new | precise, and live during the run |

## What this does not do

**It does not tell anybody else's page.** There is one `StreamingHttpResponse`
per run and no channel to another user's browser. Reaching them needs a
persistent channel, a broker and an authorization model — and *activity* is often
more sensitive than the content it concerns, so it is not a transport question
alone. Out of scope, and said here rather than left to be discovered.

**It does not derive anything from your tools.** Nothing in the spec layer
declares which resources a write touches, so nothing can infer the keys for you.
Naming them is the work.
