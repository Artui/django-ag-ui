# Replay verifier

`verify.mjs` feeds SSE streams to the published `@ag-ui/client`'s `HttpAgent`
(through a stubbed `fetch`), so its parser, `verifyEvents` and its reducer all
run. It reads `{"cases": {name: {"streams": [sse, ...], "messages": [...]}}}`
and writes `{"client": version, "results": {name: {"ok", "messages" | "error"}}}`.
`tests/agent/test_replay_against_client.py` drives it with replays of the events
route, fresh and after a reconnect, from every seq.

- Client: `@ag-ui/client` 1.0.4 and `rxjs` 7.8.1 (pinned in `package-lock.json`).

Install from this directory:

```bash
npm ci
```

`node_modules/` is git-ignored. Without node or `node_modules` the tests skip,
so CI does not run them; run them locally after a change to the replay.
