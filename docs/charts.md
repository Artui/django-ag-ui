# Charts

!!! note "Needs a recent web component"

    The browser half of this ships in `@artooi/ag-ui-web-component` **0.26.0**
    and later. `enableCharts` does not exist before it, and a pushed chart
    activity is ignored by an older bundle.


The chat surface renders markdown through a deliberately narrow sanitiser, and
images are off by default — a model-controlled image URL is fetched with no user
interaction, which turns prompt-injected page data into a zero-click
exfiltration channel. So a chart cannot arrive as markup.

It arrives as **data**. The server (or the agent) sends numbers; the web
component builds the SVG itself. Nothing chart-shaped is ever parsed as HTML,
which is what lets a visual be safe on a surface where an `<img>` is not.

There are two ways to get one on screen, and they differ in **where the data
lives** rather than in how the chart looks.

## Pushing a chart from your own code

Use this when the data should not go to the model: a large result set, or one
you would rather not send to a provider at all. There is no round trip and no
tokens spent on the numbers.

`chart_activity` returns an ordinary `ActivitySnapshotEvent`. The way onto the
stream is a **tool that returns it as metadata** — Pydantic-AI forwards AG-UI
events attached to a tool return, verbatim, ahead of the tool result:

```python
from pydantic_ai.messages import ToolReturn

from django_ag_ui import ChartSeries, ChartSpec, chart_activity


@agent.tool_plain
def show_signups() -> ToolReturn:
    rows = Signup.objects.weekly()  # your data, server-side
    spec = ChartSpec(
        kind="bar",
        title="Signups this week",
        labels=tuple(row.day for row in rows),
        series=(ChartSeries("new", tuple(float(row.count) for row in rows)),),
    )
    return ToolReturn(
        return_value="Signups chart shown.",  # what the model reads
        metadata=chart_activity(spec),  # what the browser draws
    )
```

The model sees only `return_value`. The numbers ride past it to the browser, so
a large or sensitive result set is charted without being sent to a provider.

Attach a list of events to `metadata` to send several at once.

The browser has to opt in as well — nothing renders a chart unless a host asks:

```js
document.querySelector("ag-ui-chat").enableCharts(["activity"]);
// or ["tool", "activity"] to allow both routes
```

**There is no server setting that turns this on.** Pushing a chart is an act
rather than a mode, and a flag would suggest the framework emits one on your
behalf — it cannot, because it has no idea what you want charted.

**The model does not see it.** That is the point, and the cost: the agent cannot
discuss a chart it never received. If you want it to reason about the numbers,
use the tool route below instead.

### Updating a chart in place

Pass the same `chart_id` again and the client replaces what is on screen rather
than stacking a second copy — one chart moving, not two measurements.

```python
@agent.tool_plain
def watch_throughput() -> ToolReturn:
    return ToolReturn(
        return_value="Throughput chart shown.",
        metadata=[
            chart_activity(first, chart_id="throughput"),
            chart_activity(revised, chart_id="throughput"),  # redraws in place
        ],
    )
```

`metadata` takes a list, so several events go out from one tool call. When only
the numbers move, a patch is cheaper than re-sending the whole spec:

```python
from django_ag_ui import chart_points_delta

return ToolReturn(
    return_value="Throughput updated.",
    metadata=chart_points_delta("throughput", points=(14, 21, 11, 26, 20)),
)
```

A delta is applied **positionally**, so it cannot tell that series 2 is now
something else, and it cannot tell that you sent the wrong number of points —
a wrong-length patch applies cleanly and then leaves the previous chart on
screen, with the stale numbers still showing. Send a fresh snapshot when the
*shape* changes and reserve the delta for when it has not. A delta naming a
chart the client has not drawn is dropped — send the snapshot first and keep
its id.

## Letting the agent ask for a chart

Use this when the agent should be able to talk about what it drew. The numbers
are in its context, so it can summarise them in the same turn.

The tool is client-side and opt-in — the browser registers it:

```js
document.querySelector("ag-ui-chat").enableCharts(["tool"]);
```

The agent then calls `render_chart` with the same shape as `ChartSpec`, and the
page draws it. It costs one model round, and the data passes through the
provider.

## Which to reach for

| | Agent calls the tool | You push an activity |
| --- | --- | --- |
| Data reaches the model | yes | **no** |
| Agent can discuss it | yes | no |
| Updates in place | no | **yes** |
| Needs a tool call | yes | yes, but one the agent was calling anyway |
| **Survives a reload** | **yes** | only with a client-side store |

### A pushed chart does not survive a reload on the server

Worth knowing before you choose, because it is the one difference you cannot
work around from the browser.

When a run **succeeds**, what gets stored as the thread is the *model's* message
history. A pushed chart never enters that history — which is the whole reason to
push it — so it is not in the stored thread, and a reload has nothing to redraw.
The chart is there for the rest of the session and gone on refresh.

A chart the **agent** asked for survives, because the spec travels as the tool
call's arguments, and tool calls are part of the model's history. The browser
redraws it from those arguments without re-running anything.

If a chart has to outlive a refresh and its data must not reach the model, the
options today are to store the data yourself and re-push it when the thread is
opened, or to keep the conversation in a client-side store, which does persist
activities. Teaching the server to persist them is on the roadmap.

Both routes involve a tool call — the difference is what crosses it. The agent
route sends the numbers *through* the model; the push route attaches them to a
tool the agent called for its own reasons, so the model reads one sentence while
the browser gets the data.

## What the client will refuse

The browser drops a spec it cannot draw honestly, silently — so `ChartSpec`
checks the same rules at construction, where an error can name the offending
series:

- every series needs exactly one point per label, because a shorter one
  misaligns every value after the gap, and a chart that is subtly wrong still
  reads as authoritative;
- at least one label and one series;
- labels — both the axis labels and each series' name — are strings, and the
  title is a string or `None`;
- every point is a **finite `int` or `float`** no larger than `1e15`. Two finite
  extremes still give an infinite *range*, and the client divides by that range
  to scale, so an unbounded value yields nothing drawable;
- **at most 20,000 points** across all series. Drawing more blocks the browser's
  main thread, and does so again on every reload of a stored conversation;
- **at most 2,000 labels**, whatever the series count. A separate bound because
  it answers a different question: the one above bounds the *data*, this one
  bounds the *DOM*. Every label emits an axis text node, so a single-series spec
  well inside the point budget can still ask the browser for tens of thousands
  of nodes.

That last one catches the mistake a Django app makes first. A `Sum` over a
`DecimalField` returns `Decimal`, which serialises as a JSON *string* — the
client reads only numbers and drops the whole chart, with nothing reported on
either side. `ChartSpec` refuses it rather than coercing, because rounding
somebody's money to a float on their behalf is the wrong favour: call `float()`
where you can see the precision you are giving up.

An unrecognised `kind` is drawn as a bar rather than refused: the data is still
worth showing.

## On the wire

`activity_type` is `"chart"` on an ordinary `ACTIVITY_SNAPSHOT` — a convention
inside an extension point AG-UI already provides, not an extension to the
protocol. The envelope is standard and `activity_type` is an open string, so a
client that does not know this name ignores the event, which is the graceful
outcome. It is the same choice compaction makes, for the same reason: the wire
stays vanilla AG-UI and ours is not a privileged client.
