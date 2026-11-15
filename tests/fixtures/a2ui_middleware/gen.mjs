import { readFileSync, writeFileSync } from "node:fs";
import { AbstractAgent, EventType } from "@ag-ui/client";
import { Observable, firstValueFrom, toArray } from "rxjs";
import { A2UIMiddleware, A2UI_SCHEMA_CONTEXT_DESCRIPTION } from "@ag-ui/a2ui-middleware";

const catalog = JSON.parse(readFileSync(process.argv[2], "utf8"));
const SURFACE = "validation-surface";
const ops = [
  { version: "1.0", createSurface: { surfaceId: SURFACE, catalogId: catalog.catalogId } },
  { version: "1.0", updateComponents: { surfaceId: SURFACE, components: [
    { id: "heading", component: "Text", text: { "@path": "/title" } },
    { id: "row", component: "Text", text: { "@path": "name" } },
    { id: "items", component: "List", children: { componentId: "row", path: "/items" } },
    { id: "root", component: "Column", children: ["heading", "items"] },
  ] } },
  { version: "1.0", updateDataModel: { surfaceId: SURFACE, path: "/", value: { title: "Fixture", items: [{ name: "Gamma" }, { name: "Delta" }] } } },
];
class Agent extends AbstractAgent {
  constructor(events) { super(); this.events = events; }
  run() { return new Observable((s) => { this.events.forEach((e) => s.next(e)); s.complete(); }); }
}
const callId = "call-plain";
const events = [
  { type: EventType.RUN_STARTED, runId: "run", threadId: "thread" },
  { type: EventType.TOOL_CALL_START, toolCallId: callId, toolCallName: "generate_a2ui" },
  { type: EventType.TOOL_CALL_ARGS, toolCallId: callId, delta: "{}" },
  { type: EventType.TOOL_CALL_END, toolCallId: callId },
  { type: EventType.TOOL_CALL_RESULT, messageId: `result-${callId}`, toolCallId: callId, content: JSON.stringify({ a2ui_operations: ops }) },
  { type: EventType.RUN_FINISHED, runId: "run", threadId: "thread" },
];
const mw = new A2UIMiddleware({ defaultCatalogId: catalog.catalogId });
const out = await firstValueFrom(mw.run({
  threadId: "thread", runId: "run", tools: [], state: {}, messages: [], forwardedProps: {},
  context: [{ description: A2UI_SCHEMA_CONTEXT_DESCRIPTION, value: JSON.stringify(catalog) }],
}, new Agent(events)).pipe(toArray()));
const snaps = out.filter((e) => e.type === EventType.ACTIVITY_SNAPSHOT);
const pkg = (n) => JSON.parse(readFileSync(`node_modules/${n}/package.json`, "utf8")).version;
writeFileSync(process.argv[3], JSON.stringify({
  generator: { "@ag-ui/a2ui-middleware": pkg("@ag-ui/a2ui-middleware"), "@ag-ui/client": pkg("@ag-ui/client"), scenario: "final-result-snapshots.test.ts: still paints a result when the activity for that call never painted, without the lifecycle activity" },
  context_description: A2UI_SCHEMA_CONTEXT_DESCRIPTION,
  tool_call_id: callId,
  operations: ops,
  snapshots: snaps,
}, null, 2) + "\n");
console.log(JSON.stringify(snaps, null, 1).slice(0, 600), snaps.length);
