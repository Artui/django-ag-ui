# Charts

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

```python
from django_ag_ui import ChartSeries, ChartSpec, chart_activity

spec = ChartSpec(
    kind="bar",
    title="Signups this week",
    labels=("Mon", "Tue", "Wed", "Thu", "Fri"),
    series=(ChartSeries("new", (12, 19, 9, 24, 17)),),
)
yield chart_activity(spec)
```

`chart_activity` returns an ordinary `ActivitySnapshotEvent`, so it goes on the
stream anywhere your code already yields events.

**There is no setting that turns this on.** Pushing a chart is an act rather
than a mode, and a flag would suggest the framework emits one on your behalf —
it cannot, because it has no idea what you want charted.

**The model does not see it.** That is the point, and the cost: the agent cannot
discuss a chart it never received. If you want it to reason about the numbers,
use the tool route below instead.

### Updating a chart in place

Pass the same `chart_id` again and the client replaces what is on screen rather
than stacking a second copy — one chart moving, not two measurements.

```python
yield chart_activity(spec, chart_id="throughput")
...
yield chart_activity(revised, chart_id="throughput")  # redraws in place
```

When only the numbers move, a patch is cheaper than re-sending the whole spec:

```python
from django_ag_ui import chart_points_delta

yield chart_points_delta("throughput", points=(14, 21, 11, 26, 20))
```

A delta is applied **positionally**, so it cannot tell that series 2 is now
something else. Send a fresh snapshot when the *shape* changes and reserve the
delta for when it has not. A delta naming a chart the client has not drawn is
dropped — send the snapshot first and keep its id.

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
| Extra model round | yes | no |
| Agent can discuss it | yes | no |
| Updates in place | no | **yes** |

## What the client will refuse

The browser drops a spec it cannot draw honestly, silently — so `ChartSpec`
checks the same rules at construction, where an error can name the offending
series:

- every series needs exactly one point per label, because a shorter one
  misaligns every value after the gap, and a chart that is subtly wrong still
  reads as authoritative;
- at least one label and one series.

An unrecognised `kind` is drawn as a bar rather than refused: the data is still
worth showing.

## On the wire

`activity_type` is `"chart"` on an ordinary `ACTIVITY_SNAPSHOT` — a convention
inside an extension point AG-UI already provides, not an extension to the
protocol. The envelope is standard and `activity_type` is an open string, so a
client that does not know this name ignores the event, which is the graceful
outcome. It is the same choice compaction makes, for the same reason: the wire
stays vanilla AG-UI and ours is not a privileged client.
