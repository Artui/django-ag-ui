# A2UI middleware fixture

`final_result_snapshot.json` is the `ACTIVITY_SNAPSHOT` that the published
`@ag-ui/a2ui-middleware` emits for a finished `render_a2ui`-style call. The
server's own snapshot is compared against it on the wire, in
`tests/agent/test_a2ui_render_through_the_view.py`. Do not edit it by hand.
Regenerate it instead.

- Producer: `@ag-ui/a2ui-middleware` 0.0.16, `@ag-ui/client` 1.0.4 (pinned in
  `package-lock.json`).
- Scenario: the middleware's `final-result-snapshots.test.ts` case "still
  paints a result when the activity for that call never painted", without the
  lifecycle activity, with tool call id `call-plain`.
- Catalog: `basic_catalog_1_0.json`, the middleware's own A2UI 1.0 basic catalog
  fixture, posted as the run's A2UI schema context entry.

Regenerate from this directory:

```bash
npm ci && node gen.mjs basic_catalog_1_0.json final_result_snapshot.json
```

`node_modules/` is git-ignored.

## The usage guide

`render_tool_guide.json` holds what the middleware gives a model around
`render_a2ui`: the usage guide text for each protocol
(`RENDER_A2UI_TOOL_GUIDELINES("render_a2ui", v)`, under `guidelines["1.0"]` and
`guidelines["v0.9"]`), `renderA2UITool(v)` under `tool`, and the context entry's
description. The package carries the two guide texts as constants, and
`tests/agent/test_a2ui_render_through_the_view.py` compares them with this file.
Do not edit it by hand. Regenerate it from this directory:

```bash
npm ci && node guide.mjs render_tool_guide.json
```

The basic catalog's source is upstream `ag-ui-protocol/ag-ui`,
`middlewares/a2ui-middleware/__tests__/fixtures/a2ui-1.0-basic-catalog.json` at
commit `0582d6b7929c853fa8a3b3854aa76aca4bcdfbf9`.
