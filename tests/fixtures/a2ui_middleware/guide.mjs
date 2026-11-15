import { writeFileSync, readFileSync } from "node:fs";
import { RENDER_A2UI_TOOL_GUIDELINES, renderA2UITool } from "@ag-ui/a2ui-middleware";
const v = JSON.parse(readFileSync("node_modules/@ag-ui/a2ui-middleware/package.json","utf8")).version;
writeFileSync(process.argv[2], JSON.stringify({
  generator: { "@ag-ui/a2ui-middleware": v, source: "src/tools.ts RENDER_A2UI_TOOL_GUIDELINES, renderA2UITool; src/index.ts injectToolAndFlag guidelinesDescription" },
  context_description: "A2UI render tool usage guide — how to call render_a2ui with valid arguments.",
  guidelines: { "1.0": RENDER_A2UI_TOOL_GUIDELINES("render_a2ui", "1.0"), "v0.9": RENDER_A2UI_TOOL_GUIDELINES("render_a2ui", "v0.9") },
  tool: { "1.0": renderA2UITool("1.0"), "v0.9": renderA2UITool("v0.9") },
}, null, 2) + "\n");
