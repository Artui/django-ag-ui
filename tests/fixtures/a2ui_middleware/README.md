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
