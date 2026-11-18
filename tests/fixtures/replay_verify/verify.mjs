import { readFileSync, writeFileSync } from "node:fs";
import { HttpAgent } from "@ag-ui/client";

const { cases } = JSON.parse(readFileSync(process.argv[2], "utf8"));
const sse = (body) => async () =>
  new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });

async function play(agent, body) {
  agent.fetch = sse(body);
  await agent.runAgent();
}

const results = {};
for (const [name, c] of Object.entries(cases)) {
  const agent = new HttpAgent({ url: "http://replay.invalid/", threadId: "t1", initialMessages: c.messages ?? [] });
  try {
    for (const body of c.streams) await play(agent, body);
    results[name] = { ok: true, messages: agent.messages };
  } catch (error) {
    results[name] = { ok: false, error: String(error?.message ?? error) };
  }
}
const version = JSON.parse(readFileSync("node_modules/@ag-ui/client/package.json", "utf8")).version;
writeFileSync(process.argv[3], JSON.stringify({ client: version, results }));
