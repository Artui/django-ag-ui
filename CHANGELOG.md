# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- **A per-user memory page.** `pydantic-ai-harness` ships a complete memory
  capability and `capabilities=` already carries it, so this is a recipe rather
  than a feature — no new setting, no new code. It leads with the precondition
  instead of burying it: **`TOOL_GUARD` should be on before memory is.** Memory
  is durable, model-written and replayed into every later run, so a note that
  reads as an instruction keeps working indefinitely; the guard is what stops one
  reaching a destructive tool unattended. Each setting is defensible alone and
  they are only wrong together.

  It also records why the namespace resolver reads `ctx.deps.user` and not a
  request — `capabilities=` is resolved once at construction, so nothing
  request-shaped can be closed over in it — and why a memory-management UI stays
  the host app's, while insisting that memory not be switched on without one.

- **Preferences, on the same page, as the thing memory is not.** They differ on
  *who reads them*: memory is read by the model as prompt text it wrote itself,
  a preference is read by the server to shape the run through a slot the operator
  wrote. Shipped as a recipe over `model_for_request` / `instructions_for_request`
  — default model, tone and language all fall out of the two hooks that already
  exist. No model ships here and none should: nothing this package owns would
  read the table, and the schema would be guessing at a product's taxonomy.

  The one preference with no path is per-user reasoning disclosure, and the
  refusal is recorded as a decision rather than left as an omission.

### Fixed

- **Two extras comments told readers to attach through settings keys that
  raise.** `[spec-tools]` pointed at `DJANGO_AG_UI["SERVICE_SPECS"]` and
  `[harness]` at `DJANGO_AG_UI["CAPABILITIES"]`; both keys were removed in
  0.19.0 and `check_removed_settings` has rejected them ever since. Packaging
  metadata is read where nothing runs -- no gate parses a comment in
  `pyproject.toml` -- so the claim outlived the API by five minor versions.

- Three docstrings and one docs line named drf-services' `AgentContract`, which
  0.48.0 renamed to `OfflineContract` with no alias left behind. Prose only;
  nothing imported it, which is exactly why nothing caught it.

## [0.51.0] — 2026-08-29

### Added

- **`suggestions_activity()` — server-pushed follow-up chips.** Registered skill
  chips are static and host-configured, so they can offer "summarize this" but
  never *"want me to update the shipping address too?"* after a tool has run.

  ```python
  from django_ag_ui import suggestions_activity

  await sink(
      suggestions_activity(
          [
              "Update the shipping address too",
              "Show me the order history",
          ]
      )
  )
  ```

  An ordinary `ACTIVITY_SNAPSHOT` under the `suggestions` type — a convention
  inside an extension point the protocol already provides, exactly as `chart` is,
  so a client that has never heard of the name ignores it. Identity works as
  `chart_id` does: omit `suggestions_id` and each push draws its own row under
  the answer it follows; pass the same one to replace a row already on screen.
  Chips are content, so they persist and a reload puts them back.

  Bounded at **4 prompts** (Slack's `setSuggestedPrompts` cap, and the point past
  which chips become a menu rather than a nudge) and 120 characters each, and
  **it raises rather than trimming**. The client draws no more than its own limit
  and has no channel to report what it dropped, so trimming here would ship
  suggestions that silently never appear — which is the hole `chart_limits`
  exists to close, found there by shipping it.


- **`RUN_CONTEXT["DELIVERY"]` — the channel that carries client-supplied context
  is now a choice, because there are two defensible answers and this package
  held one of them silently.**

  ```python
  DJANGO_AG_UI = {"RUN_CONTEXT": {"DELIVERY": "tool"}}  # or "instructions" (default)
  ```

  `"instructions"` is what every release before this key did and stays the
  default: the fenced block rides additional run instructions, so it **survives
  compaction** — the attachment manifest is still there at step 20 when the
  model decides to read a file — and it is neither persisted into the thread nor
  echoed back to the browser.

  `"tool"` follows pydantic-ai's documented position. Upstream left an
  `AGUIAdapter.context` accessor out **deliberately**, because *instructions
  carry operator authority, so building them out of text a client sent lets a
  prompt injection inherit that authority*, and points consumers at tool output
  instead. On this channel the block is the return value of a
  `get_client_context` tool.

  Three costs come with `"tool"`, all real and all written down rather than
  discovered: a tool result can be **compacted away** and is not re-supplied; the
  model has to **decide to call it**, so ambient facts are only considered if it
  thinks to ask; and a tool result is an ordinary part of the exchange, so the
  block is **streamed back to the browser and persisted into the thread**.

  The block itself is rendered once — same fence, same sentinel neutralisation,
  same ceiling — and only the delivery differs, so the two channels cannot drift
  in what they consider safe to pass on. An unrecognised `DELIVERY` raises at
  startup rather than defaulting: the channels differ in whether client text
  inherits operator authority, and a typo resolving to the more permissive one is
  the outcome worth refusing.


- **`publish_invalidation` and `resource_invalidation` — tell the page what the
  agent just moved.** The agent writes and the page the user is looking at still
  shows the old list. `ag-ui-run-finished` already says *something* moved and is
  already adopted, so this is **precision on a channel that ships**, not a new
  one: name the resources and a host refetches only those, live during the run
  rather than once at the end.

  ```python
  with transaction.atomic():
      order = Order.objects.create(sku=sku)
      transaction.on_commit(
          lambda: publish_invalidation("orders", f"orders/{order.pk}", reason="place_order")
      )
  ```

  Keys are **opaque host-defined strings**. This package never interprets them,
  because every alternative — model labels, spec names, URLs, MCP-style URIs —
  encodes one side's vocabulary into a contract both sides must read, and the two
  sides are a Django app and a frontend that may know nothing about Django
  models. Matching is exact and never by prefix, so a write that should also
  refresh the collection **names the collection**: a prefix rule would guess at a
  scheme this package does not own, and `orders/1` would match `orders/11`.

  A `CUSTOM` event, which is the opposite of the carrier `chart_activity` picks,
  and the difference is **lifetime**. An `ACTIVITY_SNAPSHOT` is materialised into
  a message, persisted with the transcript and replayed on every restore — right
  for a chart, and a refetch storm for an invalidation.

  ⇒ `ACTIVITY_SNAPSHOT` is for content; `CUSTOM` is for an imperative.

  **Two routes, because the transaction boundary is wrong by default.**
  `resource_invalidation` returns the event for a tool to hand back as
  `ToolReturn(metadata=...)`, the same route charts document. But a tool has to
  return before its metadata is forwarded, so it cannot announce *after* a
  commit — and `ServiceSpec` is `atomic=True` unless told otherwise, so the
  naive version tells the page to refetch data that has not committed and may
  never commit. `publish_invalidation` queues onto the run's own stream instead,
  which is what makes `transaction.on_commit` usable. It returns `False` when no
  run is listening, so a call from a worker or a management command is a no-op
  rather than an error.

  The queue is a `ContextVar` sink drained by a stream injector, the same shape
  compaction uses and for the same reason: one process serves many runs, and an
  invalidation crossing between them would tell the wrong page to refetch.

  The [guide](https://artui.github.io/django-ag-ui/invalidation/) leads with the
  hazard that matters most — **an agent-triggered reload destroys unsaved input**
  — and deliberately contains no one-line "reload on invalidation" example,
  because that is the line someone would copy.

### Changed

- **Sibling floors raised: `django-pydantic-agent>=0.19`,
  `djangorestframework-pydantic-ai>=0.20`, `djangorestframework-mcp-server>=0.35`.**

  The first two are **code-path floors, not resolution floors**. Handing a
  `SpecRegistry` to `service_specs=` is a four-hop chain — this package, then
  dpa's `build_spec_capability`, then PAI's `SpecCapability`, then its
  `SpecToolset` — and the middle two both flattened the registry to
  `name -> spec` before passing it on. All three hops were fixed separately, and
  **any one of them alone is a no-op**: below these versions the entry is
  flattened again further down and everything a `RegisteredSpec` carries beyond
  the spec is dropped, silently. So the fix in this release only does anything
  with those versions present.

  The drf-mcp floor is defensive: 0.30 through 0.34 import audience symbols that
  `drf-services` 0.48.0 removed, and they declare no upper bound, so pinning one
  of them alongside a current drf-services resolves to a combination that fails
  at import. 0.35 is the first release that works.

### Fixed

- **Client context was fused into the cached instruction prefix, latently
  costing money the moment anyone enables prompt caching.** Pydantic-AI
  classifies a literal-string instruction as `dynamic=False` and a callable's
  result as `dynamic=True`, sorts static before dynamic, and — with
  `anthropic_cache_instructions` set — puts the cache breakpoint after the last
  **static** instruction. The block was passed as a string, so it joined the
  operator's static block and every request paid a fresh cache write and never
  got a read.

  It is now passed as a callable, which keeps rules-before-data (static still
  sorts first) while landing the volatile text outside the breakpoint.

  **Latent, not live** — nothing in any of these packages configures caching. But
  `AgentConfig.model_settings` passes straight through, so it was one key away
  with no code change, and `CachePoint` markers are dropped by the AG-UI adapter,
  so a consumer could not have corrected it from the message side.


- **A `SpecRegistry` handed to `service_specs=` was flattened before the
  capability was built, dropping everything the entry carries.**
  `_resolve_spec_source` returned only the `name -> spec` mapping, so the spec
  capability was built from a registry's *output* rather than the registry — and
  a `RegisteredSpec` holds more than its spec: its tags, and drf-services 0.45's
  `AgentContract`, which is where a project declares what a caller with **no
  HTTP request** has to be told (the URL kwargs, query params and field-audience
  overrides the URLconf and query string give an HTTP caller for free).

  The source is now kept beside the mapping and is what the capability is built
  from. The mapping still serves the two consumers that want names — the tool
  catalog and the view's tool-name reservation, which is why it was normalised
  here in the first place.

  **The loss was silent**, which is why it survived a wave of review: every tool
  is still registered and the endpoint still works. It shows only as an argument
  the model was never offered, or a field it was shown and should not have been.

  The other half is `django-pydantic-agent`, which flattens again one step
  later; the declarations reach the toolset once **both** land, so the dpa floor
  raise rides with the next release sweep. Nothing regresses in the meantime —
  a registry reaching the older builder is flattened exactly as before.

## [0.50.0] — 2026-08-28

### Security

- **Raised the `django-pydantic-agent` floor to `>=0.18`** — below it
  `TOOL_GUARD={"ENABLED": True}` did not gate a drf-services spec attached
  **in process** through `service_specs=`. The guard read one vocabulary, the
  `DESTRUCTIVE_METADATA_KEY` the drf-mcp bridge stamps; a `SpecToolset` writes
  `metadata["annotations"]["readOnlyHint"]` instead, and nothing translated
  between them. So the *same* `ServiceSpec` was gated when it arrived over the
  bridge and ungated when it was attached directly — a transport swap silently
  removed the gate, and a spec mutation ran unapproved with the setting on and
  the docs promising otherwise.

  The fix shipped in django-pydantic-agent 0.18.0; this release is what makes an
  install of *this* package get it. A patched library is not a patched stack. If
  you cannot upgrade yet, name the mutating specs in
  `TOOL_GUARD["REQUIRE_APPROVAL"]` — that path always worked.

  0.18 also taught the guard the `x-destructive` schema stamp
  `build_input_schema` writes, so a tool attached through `toolsets=` with a
  schema derived by that helper is now gated too. Both additions **widen** what
  the gate catches; nothing that was gated before stops being gated.

### Documentation

- **`tool-approval.md` and `configuration.md` understated the gate.** Both
  enumerated what counts as destructive and named only the registry flag and the
  drf-mcp annotation. They now list all four vocabularies, and say that silence
  is not a claim — a tool declaring nothing is left alone, which is what
  `REQUIRE_APPROVAL` is for.

### Added

- **`thread_activity_source=`** on `AGUIServer` / `ThreadsView` — a pushed chart
  can be put back on reload. A successful run stores the *model's* message
  history, and a pushed activity deliberately never enters it, so a server-side
  store served a restored thread with nothing to redraw the chart from: it was
  there for the session and gone on refresh. A client-side store persists
  activities, which is why both test suites agreed the feature worked.

  ```python
  class StoredCharts:
      async def activities_for(self, thread_id, *, messages, request):
          rows = Chart.objects.filter(thread_id=thread_id, owner=request.user)
          return [
              ThreadActivity(
                  chart_activity(row.spec(), chart_id=row.chart_id),
                  after_message_id=row.after_message_id,
              )
              async for row in rows
          ]


  AGUIServer(registry, conversation_store=store, thread_activity_source=StoredCharts())
  ```

  The source is asked on every thread read and its events are merged into the
  messages served, materialised the way `@ag-ui/client` materialises a live
  `ACTIVITY_SNAPSHOT` — so a restored chart is the same `role: "activity"`
  message the browser already had on screen, and **no web-component change is
  needed**; 0.26.0 and later redraw it as they stand.

  **Merging on read rather than persisting activities beside the history** is
  the deliberate half. Persisting them needs an ordering rule, a dedup rule and
  an answer for what a resumed run does with a snapshot that already contains
  them — decisions the framework cannot make, because it never saw the data.
  The stored thread stays exactly the model's history, so resume, fork and
  snapshot keep meaning what they meant. The project owns the record and answers
  the placement question through `ThreadActivity.after_message_id`; an unknown
  anchor lands the chart at the end rather than dropping it, and two entries
  under one id collapse to first-position/last-content, which is what the
  browser does with the pair anyway.

- **`ThreadActivity`** and **`ThreadActivitySource`** — the record and the
  Protocol above, exported from the package root.

## [0.49.0] — 2026-08-26

### Added

- **`ScopedStepStore`** — partitions the step ledger between endpoints, the way
  `ScopedConversationStore` already partitions thread history:

  ```python
  internal = AGUIServer(registry, step_store=ScopedStepStore(DefaultStepStore, scope="internal"))
  public = AGUIServer(registry, step_store=ScopedStepStore(DefaultStepStore, scope="public"))
  ```

  A ledger keys by `(owner_id, run_id)` and nothing else, so two mounts handed
  the same `step_store` shared one user's runs: a run recorded at
  `/internal/agent` was listed by `/public/agent/runs/`, and since
  `resume/<run_id>/` addresses a run **by id**, the same user could continue that
  transcript under the public agent's model, tools and guard policy. Owner
  scoping cannot catch it — it is the same user on both mounts, and the
  documented two-endpoint recipe told projects to scope conversations while
  saying nothing about runs.

  It wraps the **factory**, not a store, because that is what `step_store=`
  takes. The partition is a run-id prefix, so it needs no migration and no break
  to a protocol that is upstream's; a run in another scope is simply *not found*
  rather than refused, so a probe cannot confirm the id exists. Run ids on the
  wire are unchanged. Opt-in and explicit, for the same reason as the
  conversation wrapper: wrapping a mount that has been running hides its earlier
  runs from `runs/` rather than migrating them.

- **`transcribe_throttle=`** on `AGUIServer` — the rate-limit seam that only the
  agent endpoint had, on the other route that spends provider money. The shipped
  transcription backend is a paid API call per clip, and authentication says
  *who* may call it, not how often, so an authenticated caller looping small
  valid clips was a bill with no limiter available to it.

  ```python
  AGUIServer(
      registry,
      transcription_backend=OpenAITranscriptionBackend(),
      transcribe_throttle=FixedWindowThrottle(max_runs=60, per_seconds=60, namespace="transcribe"),
  )
  ```

  A **separate** argument rather than a second use of `throttle=`: one limiter
  instance is one counter, so sharing would let voice clips consume the run
  budget. `TranscribeView(throttle=…)` takes the same `Throttle`, runs it at the
  same point (after authentication, before the body is parsed) and answers the
  same `429`.

- **`MAX_LABELS`** in `chart_limits`, exported alongside `MAX_MAGNITUDE` and
  `MAX_POINTS`.

- **`OpenAITranscriptionBackend.api_key`** — a class attribute passed to
  `AsyncOpenAI`, so a subclass pointing `base_url` at another vendor can send
  that vendor's key. Unset it behaves exactly as before (the SDK reads
  `OPENAI_API_KEY`).

- **`RUN_LIST_LIMIT`** (default `50`, `0` disables) — the run index's ceiling,
  also a `build_ag_ui_config(run_list_limit=…)` keyword, so two endpoints can
  differ.

### Fixed

- **Every directly-mountable endpoint now warns when nothing says how it
  authenticates.** The warning fired on the agent endpoint alone, so a project
  mounting the attachment, thread or transcription views directly -- which the docs
  describe -- got silence for the one configuration the warning exists for:
  cookie-authenticated callers on a CSRF-exempt endpoint, where any third-party page
  can drive the agent as whoever is logged in.

- **The tool-catalog and skills endpoints authorize before checking the method.** An
  unauthenticated caller got 405 where an unmounted backend answers 404, which
  fingerprints the optional backends a deployment has enabled. The agent endpoint was
  corrected for this; its two read-only siblings kept the old order.

- **Resuming a checkpoint from one thread could destroy another thread's stored
  conversation, and may already have.** A saved conversation is written as a
  whole row, `runs/` indexes every run an owner has across *all* their threads,
  and a client resumes the run it picked into whatever thread is currently open.
  A user reading thread B who picked a run belonging to thread A therefore ended
  up with A's conversation stored under B and B's own turns gone — silently,
  irreversibly, and invisibly until they reopened B. **If you run a
  `conversation_store` together with a `step_store`, assume this can have
  happened and check your backups before upgrading changes anything.** The
  endpoint now answers `409` and streams nothing when seeding the posted
  `threadId` from that run would replace a conversation the run does not belong
  to. Continuing a run in its own thread is unaffected; so is branching one into
  a thread that holds nothing yet, and so is any endpoint with no conversation
  store. See [Resume and fork](https://artui.github.io/django-ag-ui/step-persistence/).

- **`drf_mcp_server=` exposed no tools at all.** The per-request bridge was
  handed the set of *already claimed* names, which by construction contains
  every tool the drf-mcp server registers — so the toolset excluded the whole
  registry it exists to expose and returned nothing, on every request, while
  `GET tools/` went on advertising those same tools. Any endpoint configured
  with a drf-mcp server has been running with none of its bridged tools
  reachable by the model, with no error anywhere. The bridge now excludes only
  the `@tool` registry's names, which is the collision it actually owes.

- **A failing run streamed the raw exception text to the browser.** `RUN_ERROR`
  carried `str(exception)` verbatim — an ORM error's SQL and connection target,
  an `OSError`'s server path, a provider `401` echoing a masked key — the
  disclosure `TOOL_FAILURE["INCLUDE_DETAIL"]` exists to withhold one level down,
  and one that no failure policy covered: errors raised by the store, the
  adapter or the model client take this path whatever the policy is doing. The
  same setting now governs both. Operator copies are unchanged: the full
  exception still reaches the audit record and the Python logger.

- **The agent endpoint answered `405` before authenticating.** An
  unauthenticated caller learned the route existed, while every sibling view
  answers `401` — and since optional routes are mounted only when their backend
  is configured, the difference let an unauthenticated caller enumerate which
  backends a deployment had enabled. It authenticates first now, as the sibling
  views do.

- **Anonymous runs handed every conversation store the same `owner_id`.**
  `Conversation.owner_id` is documented as the authorization scope, so `None`
  for every anonymous visitor collapses them into one partition in a store that
  keys on the field as invited — one visitor's thread list answering another's.
  An anonymous run is now scoped to its browser session, the same bucket the
  reference stores derive for themselves. An existing session key is used and
  never created, so a deployment without session middleware still answers
  `None`: there is no per-visitor key to be had.

- **A client disconnect could park a worker in teardown, or lose the run's
  record entirely.** The cancelled-run finalisation — a conversation save plus
  an audit write — ran inline in the disconnected request's own task with no
  time bound, so repeated cheap aborts against a slow store turned into workers
  stuck in teardown. It is shielded and time-bounded now: past the bound the
  write is left to land on its own and the wait ends. The guard around it also
  caught `Exception` only, which does not cover `CancelledError`, so a store
  torn down mid-write lost both the partial conversation and the cancellation
  audit record with nothing logged — leaving the run reading as neither
  completed nor cancelled. A failing stream teardown no longer costs the
  finalisation either.


- **A wide chart was accepted server-side and silently dropped in the browser.**
  `chart_limits` exists to keep the producer's bounds in step with the
  consumer's, and it mirrored two of the client's three: the web component also
  refuses more than **2,000 labels**, a bound independent of the point budget
  because it bounds the *DOM* rather than the data. A single-series spec of 2,500
  points is far inside `MAX_POINTS`, so it passed `ChartSpec.validate()`,
  serialised, streamed, and was discarded on arrival with **nothing reported on
  either side** — the exact failure the module was written to prevent. Refused at
  construction now, and the pinning test asserts the *count* of bounds as well as
  their values, so a fourth one appearing in the component fails here.

- **`AGUIServer(service_specs=<pre-built toolset>)` checked its tool names
  against the `@tool` registry only, not against `drf_mcp_server=`.** The mapping
  path already excluded both, so the two shapes of `service_specs=` disagreed
  about what counted as a collision — and the drf-mcp half surfaced as a
  pydantic-ai `UserError` **mid-run**, which is exactly the failure this guard
  exists to move to construction time. Both sources are checked now, and the
  error names which one claimed the name.

### Changed

- **A custom step store must provide `get_run`.** Refusing a cross-thread resume
  reads the source run's `conversation_id`, so a duck-typed store that omits `get_run`
  now raises on a resume where it previously did not. `get_run` is part of the
  `StepStore` Protocol, so this is a contractual requirement made load-bearing rather
  than a new one.

- **The default system prompt no longer promises a confirmation step the
  deployment may not have.** It told the model the interface shows an explicit
  confirmation before a destructive action runs, and in the same breath told it
  not to ask in text — but the only server-side gate is opt-in and off by
  default, so on stock settings the prompt removed the last check rather than
  describing one. It now says the application decides which actions need a
  confirmation and may interrupt the call to collect one, and asks the model to
  check for itself before a destructive or irreversible action the user did not
  clearly ask for. Set `DJANGO_AG_UI["SYSTEM_PROMPT"]` to keep your own wording.


- **`transcribe/` refuses an oversized clip while the body is still arriving**,
  instead of measuring it afterwards. The view parsed `request.FILES` and *then*
  checked `TRANSCRIPTION_MAX_BYTES`, so Django had already written the whole part
  out — to `FILE_UPLOAD_TEMP_DIR` once it outgrew memory — and the cap bounded
  what reached the backend rather than what reached the disk. No Django-level
  ceiling covered it either: `DATA_UPLOAD_MAX_MEMORY_SIZE` excludes file uploads
  and `FILE_UPLOAD_MAX_MEMORY_SIZE` only chooses memory over a temp file. It now
  installs the same `CappedUploadHandler` the attachment upload route has used
  all along.

  **Upgrading.** Same setting, same `413`, same body — but an oversized upload is
  now cut off mid-stream (`StopUpload(connection_reset=True)`), so a client that
  was still sending may surface a connection error instead of reading the `413`.
  That is the tradeoff the attachment route already made. Nothing changes for
  in-cap clips, and `TRANSCRIPTION_MAX_BYTES = 0` still disables the cap
  entirely.

- **`runs/` is capped at `RUN_LIST_LIMIT` (default `50`), newest first.** It
  listed *every* recorded run and issued one `latest_snapshot` call per run,
  holding each run's whole message list resident while the rows were built — so a
  single `GET` on an account with a long history was `1 + N` queries and `N`
  transcripts in memory, with nothing clamping `N`. The cap is applied **before**
  those snapshot loads, so the runs it drops cost nothing; the store's own
  `list_runs` is still unbounded, because the step-store protocol has no limit to
  pass.

  **Upgrading.** A deployment whose users have more than 50 recorded runs will
  see `runs/` return the newest 50 rather than all of them. Raise it with
  `DJANGO_AG_UI["RUN_LIST_LIMIT"]`, per endpoint with
  `build_ag_ui_config(run_list_limit=…)`, or set `0` to restore the old
  unbounded behaviour. There is deliberately no `?limit=` on this route, unlike
  `threads/`: the protocol offers no offset, so a smaller page would only be a
  client asking for less of a list it cannot page through.

- **Every `DJANGO_AG_UI` key the package does not read is refused at startup**,
  not just the ten removed in 0.19.0. `ALLOW_ANONYMOUS` was the case worth
  naming: it was never a setting of this package, it reads like a switch, it is a
  store constructor argument, and a project that set it got the `False` default
  and no indication otherwise — the only explanation lived in a warning box in
  the docs. Naming keys one at a time only ever covered the mistakes already
  made; rejecting the complement is what makes the list exhaustive, and it
  catches a typo (`TRANSCRIPTION_MAX_BYTE`) with the same silent failure one
  letter away.

  **Upgrading.** A settings dict carrying an unrecognised key now raises
  `ImproperlyConfigured` when the URL conf is imported, naming every offender at
  once with what to do about each. Move project-specific values out of
  `DJANGO_AG_UI`; the accepted keys are the settings table in the configuration
  guide, and a test reads `build_ag_ui_config`'s own source so the guard cannot
  drift from it.

### Security

- **The provider API key no longer prints into tracebacks.** `AGUIConfig` is a
  dataclass with `api_key: str | None` and the generated `repr`, and the record
  is bound to a plainly-named local on every path that builds an agent — so a bad
  model string, a provider import error or a provider `401` put
  `config=AGUIConfig(model=…, api_key='sk-…', …)` in the frame locals of a
  technical-500 page or an error-reporting event. Name-based scrubbing misses it
  there, because the secret is nested inside another object's `repr` rather than
  sitting in a field called `api_key`. The field is now `repr=False`; reading the
  attribute is unchanged.

- **The documented recipe for a non-OpenAI transcription endpoint sent
  `OPENAI_API_KEY` to that third-party host.** `OpenAITranscriptionBackend`
  exposed `base_url` as an overridable class attribute but no key seam, so
  following its own example — `class GroqTranscription(...): base_url = "…"` —
  sent the OpenAI credential as the bearer token to `api.groq.com` on every
  clip, and showed only a `401` for it. The only escapes were overriding
  `transcribe()` or clobbering the environment variable process-wide, which
  breaks the agent's own model. `base_url` and `api_key` now travel together, in
  the docstring and in the recipe.

### Performance

- **A run with persistence off no longer buffers what nothing will read.** The
  transcript that lets a cancelled or failed run be persisted was recorded
  unconditionally — every text and tool-argument delta held as its own object
  for the length of the stream — including under the default
  `NullConversationStore`, where it is discarded at the end. It is now recorded
  only where a store will read it back.

- **The prior conversation is dumped once per run, and not at all when nothing
  persists.** Both non-completing exits eagerly dumped and stripped the whole
  prior conversation while the stream was being composed, and each closure held
  its own copy for the run's lifetime — so a resumed or forked run carried two
  to three independent copies of its history on top of what the run itself
  needed, computed even with persistence off, where they could never be used.

### Documentation

- The configuration guide gains an **Unknown keys** section, a `RUN_LIST_LIMIT`
  section and a `transcribe_throttle=` section; the multi-endpoint recipe now
  scopes the step store alongside the conversation store, which it had been
  silent about.
- The step-persistence guide gains a **Scoping two endpoints** section and states
  the run index's ceiling.
- "What the client will refuse" in the chart guide lists the label bound.
- The API reference gains `Throttle`, `FixedWindowThrottle` and `ScopedStepStore`.


## [0.48.0] — 2026-08-25

### Added

- **`MAX_MAGNITUDE` and `MAX_POINTS`**, the bounds a chart payload has to satisfy
  on both sides of the wire, exported so a caller building specs by hand can
  check against the same numbers rather than discovering them by having charts
  disappear.

### Fixed

- **A chart the client silently discarded now fails where it is built.** The two
  sides of this contract had drifted: the web component bounds point magnitude
  at `1e15` and total points at `20000`, and this package enforced neither — so
  a spec past either limit was serialised, streamed, and dropped on arrival with
  **nothing reported on either side**. The limits now live in one module,
  `chart_limits`, with the component's own values pinned by a test, because a
  mismatch is invisible in both suites: each passes its own, and only a payload
  crossing the gap between them fails.

- **A lazy translation as a series name killed the response mid-stream.**
  `ChartSeries("…")` accepted anything, and a `gettext_lazy` label — an entirely
  ordinary thing to want — is not serialisable, so it raised at *encode* time,
  after the response headers had gone out. It is refused at construction now,
  alongside the axis labels it sits beside. A non-string `title` is refused too:
  the client reads one as no title at all, so it vanished quietly.

- **`chart_points_delta` accepted patches that break a chart in silence.** An
  empty points array applies cleanly and leaves the client holding a spec it
  will not draw — so the *previous* numbers stay on screen reading as current,
  and the chart disappears entirely on the next reload. Empty arrays and
  out-of-range magnitudes are now refused, and the docstring says plainly which
  remaining mistake (a wrong-length array) still fails invisibly and why a
  snapshot is the right tool when the shape changes.

### Documentation

- **A pushed chart does not survive a reload, and now the docs say so.** When a
  run succeeds, the stored thread is the *model's* message history — and a
  pushed chart never enters that history, which is the whole reason to push it.
  So it is absent from the stored thread and a reload has nothing to redraw. A
  chart the agent asked for does survive, because its spec travels as the tool
  call's arguments. Found by driving the real stack; both suites pass either
  way, because the client-side store does persist activities.
- **The chart docs still showed a mechanism that does not exist.** The
  "Updating a chart in place" section — and the only example
  `chart_points_delta` had — told you to `yield chart_activity(...)` into a
  stream with no injection point. Rewritten around the tool-return route that
  actually works.
- **"What the client will refuse" listed three rules and omitted the two that
  drop a chart without a word.** Magnitude and total-point bounds are now
  documented with the reason each exists.

## [0.47.0] — 2026-08-25

### Added

- **Charts, pushed from your own code.** `chart_activity(spec)` returns an
  ordinary `ActivitySnapshotEvent` that the web component draws as a chart —
  `ChartSpec` / `ChartSeries` carry the data, and the browser builds the SVG.
  Nothing chart-shaped is parsed as HTML, which is what lets a visual be safe on
  a surface that keeps images off by default, a model-controlled URL being a
  zero-click exfiltration channel.

  **The data never reaches the model.** No round trip, no tokens spent on the
  numbers, and a large or sensitive result set is drawn for the user without
  going to a provider. The cost is the other side of the same coin: the agent
  cannot discuss a chart it never received. When it should be able to, let it
  call the client-side `render_chart` tool instead.

  No setting turns this on. Pushing a chart is an act rather than a mode, and a
  flag would imply the framework emits one on your behalf, which it cannot. The
  event reaches the stream as a tool return's `metadata`, which Pydantic-AI
  forwards verbatim — so the model reads one sentence while the browser gets the
  data.

  `ChartSpec` refuses what the client would silently drop, including the mistake
  a Django app makes first: a `Sum` over a `DecimalField` returns `Decimal`,
  which serialises as a JSON *string*, and the client reads only numbers. It is
  refused rather than coerced — rounding somebody's money to a float on their
  behalf is the wrong favour.

- **Charts that update in place.** Reuse a `chart_id` and the client redraws
  that chart rather than stacking a copy — one chart moving, not two
  measurements. When only the numbers change, `chart_points_delta` sends a JSON
  Patch touching one series instead of the whole spec. A delta is applied
  positionally, so it is for when the shape has not changed; send a snapshot
  when it has.

  `ACTIVITY_SNAPSHOT` and `ACTIVITY_DELTA` are used as the protocol defines
  them, with `activity_type` set to `"chart"` — a convention inside an extension
  point AG-UI already provides, the same choice compaction makes. A client that
  does not know the name ignores the event.

### Fixed

- **The floor gate's second resolution was still reading the runner's cache.**
  The previous release refreshed the floor *lock* but left the bare-install check
  — the one that installs the package alone, with no extras, which is the shape
  that catches a floor a fuller install papers over — resolving against whatever
  package list the shared uv cache happened to hold. Both now use `--refresh`.

## [0.46.0] — 2026-08-25

### Changed

- **Requires `django-pydantic-agent>=0.17`**, up from `>=0.16`, and this
  transport is the reason rather than a bystander. An attached file's tool result
  has two halves with different lifetimes: the sentence is streamed and kept in
  the client transcript, while the bytes never travel the event stream at all.
  This transport seeds later turns from that transcript — `message_history` stays
  `None` unless a run is resumed — so below the new floor it replayed *"its
  contents are attached"* with nothing attached, and the model answered
  confidently about a document it had never read, with no error surfaced
  anywhere. A transport persisting full `ModelMessages` keeps the bytes and never
  saw this; ours is the one where the sentence was wrong. 0.17 also stops a text
  attachment over the size cap being returned whole.

### Fixed

- **The floor-resolution CI gate could resolve against a stale package index.**
  Its purpose is to answer "what would a consumer installing from scratch get",
  but it read the runner's shared uv cache, so the answer came from whatever
  listing that cache held rather than from the index. It failed a floor raise as
  unsatisfiable while the index had been serving the release for some time. Now
  resolved with `--refresh`, so the gate measures what it claims to.

### Added

- **`attachment_inline` on `AGUIConfig`**, so how much of an attachment the model
  is handed can be set alongside what may be uploaded. The toolset was built
  without it, so the substrate's defaults always won and no consumer could reach
  them. That left a **6 MiB band** on the defaults — files between the 4 MiB
  read-back limit and the 10 MiB upload cap uploaded successfully, rendered a
  chip, and came back as a description however often the model asked, with
  nothing on screen to distinguish that from success. Every raise of
  `ATTACHMENT_MAX_BYTES`, the documented knob for upload limits, widened it.

  The same gap made inlining impossible to switch off, so a consumer preferring
  its own extraction, or bounding the bytes re-sent on every model request, had
  no lever. Passed as an object rather than read from settings: it is a
  dataclass, and collaborators arrive as objects here rather than dotted paths.

  Defaults to `None`, which takes the substrate's own defaults — unchanged
  behaviour for anyone not setting it, and no second copy of those values to
  drift out of sync.

## [0.45.0] — 2026-08-25

### Changed

- **The `anthropic` extra now requires `pydantic-ai-slim>=2.33`**, up from
  `>=2`. The `anthropic` SDK 1.0.0 reached PyPI on 2026-08-20 rebuilt on
  `httpx2`, with legacy `httpx` support removed, and every pydantic-ai before
  2.33 hands `AnthropicProvider` an `httpx.AsyncClient` that the 1.x SDK
  rejects — permitted without being supported, so a fresh install at the old
  floor resolved a combination that failed at runtime. The `openai` and `google`
  extras and the core `pydantic-ai-slim` floor are unaffected.
- **Requires `django-pydantic-agent>=0.16`**, up from `>=0.15`, so this
  package's attachment surface carries that release's two fixes rather than
  leaving them to chance. Below the new floor, an attachment whose bytes had
  left storage raised where the store contract says it returns `None` — the
  attachment tool has a sentence ready for an unavailable file and got an opaque
  failure instead, so the agent described a file it could not read and was told
  not to retry — and the content-hash dedup adopted a missing blob without
  checking, which meant re-uploading the same file could never repair it.

### Fixed

- **The documentation build no longer aborts on the substrate's own
  cross-references.** This package re-exports `django_pydantic_agent` symbols and
  renders their docstrings, and those docstrings carry autorefs links to targets
  that exist only in the substrate's own site — so a strict build here failed on
  seven of them. Its published inventory is now imported, pointing those links at
  the upstream page rather than nowhere. Latent since the substrate added the
  links; it surfaced when the floor above moved the resolved version past them.

## [0.44.0] — 2026-08-14

### Added

- **The run index answers with a `preview`, so a person can tell two runs
  apart.** `GET runs/` rows carried `run_id`, `thread_id`, `parent_run_id`,
  `started_at` and `continuable` — nothing a human recognises. Three continuable
  runs therefore rendered as `1m ago` / `just now` / `just now`, the last two
  continuing different conversations, and picking between them was picking blind.
  The new field is the run's first user message, collapsed to one line and
  truncated to 100 characters.

  It costs no extra query: the view already loads each row's latest snapshot to
  answer `continuable`, and a snapshot holds the messages. It is `null` exactly
  where that snapshot is absent — which is where `continuable` is `false` and
  there was nothing to offer anyway — and where the run opened on something with
  no words in it (a run seeded from history alone, an image with no caption).

- **Rows arrive newest first.** Both this package's docs and the web component's
  said so; neither sorted, and a run's most likely target sat at the bottom of the
  list. The reversal belongs here rather than in the store: a `StepStore` answers
  oldest-first because the harness protocol documents ascending `started_at` and
  invites callers to take the newest with `[-1]`, so a store that reversed it
  would break upstream's own idiom. Presentation order is the view's.

### Changed

- **`[harness]` no longer installs a code sandbox** (breaking, for one
  combination). It required `pydantic-ai-harness[code-mode]`, so a project wiring
  *step persistence* also got `pydantic-monty` and its client and runtime — 8 MB
  of WASM sandbox, measured from cold installs — to show a list of checkpoints.
  The extra now installs `pydantic-ai-harness` and nothing else, which is what its
  own docs always said it did, and **CodeMode moves to a new `[code-mode]`
  extra**:

  ```bash
  pip install "django-ag-ui[code-mode]"    # was [harness]
  ```

  Two features shared one dependency and the extra was named after the
  dependency rather than after either feature, so it grew when the package did.
  Compaction, subagents and step persistence keep riding `[harness]`; only
  CodeMode needs a sandbox, and now only CodeMode installs one. A project on
  `[harness]` that does use CodeMode fails loudly and immediately on the import,
  with upstream's own message naming the extra to install.

### Fixed

- **A collaborator passed as a dotted path is refused at startup.**
  `AGUIServer(registry, attachment_store="myapp.stores.MyStore")` constructed
  without complaint **and mounted both upload endpoints** — the mount test asks
  only whether the store is a non-null one, and a string passes that — then failed
  on the first upload as an attribute error on a `str`, in an endpoint the caller
  believed was configured. There is no `import_string` in this package, so a
  string can only be a mistake, and it is now rejected when the URL conf is
  imported, naming every offending argument and its value at once. A list is
  checked element-wise too: a string in `toolsets=[…]` is the same mistake with a
  longer fuse, surviving construction *and* the mount before failing where nothing
  points back at the URL conf. `model=` and `instructions=` take strings by design
  and are untouched.

### Documentation

- **Five places still described a constructor argument as "a dotted path"**, a
  month after the collaborators became objects and `import_string` left the
  package. `attachment_store=` and
  `transcription_backend=` each carried the stale sentence *immediately followed*
  by the corrected one, which is what gave the cause away — the conversion pass
  rewrote the arguments and left the prose. Also `drf_mcp_server=`, the note
  explaining why the settings keys went away (which now says no argument takes a
  path and that a string is refused), the subagents page, and the README's audit
  line, which the original finding had not caught.

- **The resume warning no longer oversells a hazard the harness guards.** It read
  as though reusing the source `run_id` would corrupt the tool-effect ledger
  through its `(run_id, tool_call_id)` key. The harness refuses the reuse by name
  — *"run_id … is already in the store. Explicit `run_id` is single-shot"* — and
  the source run's records are untouched, so there is nothing to defend against
  client-side beyond passing a new id.

  Worth more than the correction: **that refusal is a `RUN_ERROR` event, not an
  HTTP status**, and cannot be otherwise, because `RUN_STARTED` has already
  committed the response at `200`. That is true of every error a streaming
  endpoint raises after its first byte, so the page now states the client-side
  rule it implies — read the event stream, not `response.ok`, or these runs score
  as successes.

## [0.43.0] — 2026-08-13

### Added

- **An approval card can ask a question a person can read.** The question an
  AG-UI interrupt carries is generated from the call itself —
  `Approve delete_project({"project_id": 7})?` — which is accurate and not
  something to show. Two sources now replace it, in order: a registry tool's own
  `@tool(confirm=...)`, which is already the wording the web component uses when
  *it* gates the call in the browser, and a new `APPROVAL_PROMPTS` map (settings,
  or `build_ag_ui_config(approval_prompts=...)` per endpoint) for tools whose
  schema carries none — a spec tool reaching the agent in-process, or a bridged
  MCP tool. The phrase rides the interrupt's `metadata` as `x-confirm`, the same
  key the client already reads off a tool's schema, so one concept covers both
  gates. A tool with neither source keeps the generated question, and an interrupt
  whose tool supplied its own `x-confirm` is left alone.
- **`ToolGuard` and `ToolGuardConfig` are re-exported.** `build_ag_ui_config`
  takes a `ToolGuardConfig` and `AGUIConfig` is typed with one, while the package
  exported neither — so configuring the gate in code meant importing from
  `django_pydantic_agent` beside a call into `django_ag_ui`, with nothing
  explaining the split. `ToolFailureConfig` was already re-exported, which made
  the omission look accidental rather than principled.

### Fixed

- **A stored turn that called no tool no longer serialises `"toolCalls": null`.**
  The two SDKs of this protocol disagree about that field: `ag_ui.core` types it
  `list[ToolCall] | None`, while the TypeScript schema types it
  optional-and-not-nullable and **rejects** the null (`Expected array, received
  null`). An absent field is valid in both, so this end stops emitting it —
  conformance, not tidiness. It had already cost a released version of the web
  component, whose history replay threw on the null and dropped every later turn
  from a restored transcript; that client-side guard shipped in 0.23.1, and this
  is the server half. Nothing is lost on the way back: every nullable field on
  these models is also optional.

### Documentation

- **What a resumed request has to carry**, which is two things and the second is
  easy to miss: the `resume[]` array answers the interrupt, and the assistant turn
  holding the pending tool call has to be in `messages[]` beside it, with no tool
  message (nothing has run). Send the answer alone and the run starts the turn
  over. Also that a denial reaches the model as a tool return whose `outcome` is
  `"denied"` — read the outcome, not the message text.
- **How a server-side write tells the host page.** An approved call changes data
  the page may be rendering, and the page has no reason to refetch. The two
  channels are now named where the gate is documented: the web component's
  `ag-ui-run-finished` event (needs nothing of the agent) and shared state (richer,
  but the agent has to emit a snapshot).

### Fixed

- **Three attribute descriptions now reach the page at all.** `OpenAITranscriptionBackend`'s
  `model`, `base_url` and `timeout` documented themselves with Sphinx `#:` comments,
  which mkdocstrings reads as ordinary Python comments — so the text was never
  rendered anywhere. They are attribute docstrings now, and the reference carries
  them.
- **The reST literal-block marker no longer reaches the page.** Sphinx reads a
  trailing `::` as "an indented literal block follows" and prints one colon;
  Markdown has no such rule, so the second colon rendered verbatim. The indented
  block was already coming out as a code block either way, so this drops the
  stray character and nothing else.

  The API page still shows the marker inside symbols re-exported from
  `django-pydantic-agent`; that is fixed in its repo and clears when it releases.

### Fixed

- **Docstring cross-references now render as links instead of raw markup.** The
  docstrings carried Sphinx roles — ``:class:`~django_ag_ui.AGUIServer` `` — but
  the docs build is mkdocstrings, which renders docstring bodies as Markdown and
  has no such syntax, so all 71 reached the published page verbatim, `:class:`
  prefix and Sphinx's abbreviating `~` included. They are now mkdocstrings
  autorefs links, matching the syntax the hand-written `docs/*.md` pages already
  used. References to symbols the reference does not render — private helpers,
  `CappedUploadHandler`, undocumented members, and third-party symbols — became
  plain code spans.

  The API page still shows raw roles inside the symbols re-exported from
  `django-pydantic-agent`; those come from that package's own docstrings and
  clear when its fix is released and picked up here.

## [0.42.0] — 2026-08-12

### Fixed

- **A file the model read is no longer stored as base64 in the conversation
  row.** `read_attachment` hands a PDF or an image to the model as a `ToolReturn`
  carrying the bytes, which is right for the *run* and wrong for the *record*:
  the return serialises onto the wire as a synthetic `user` message whose whole
  content is a base64 `document` part, so it landed in the stored thread. A
  2300-byte PDF measured 3994 bytes in the row; a 2.6 MB one is roughly 3.5 MB,
  refetched by the browser on every thread load and posted back on every turn
  after a reload. Those bytes now come off the run's own new messages on the way
  to the store, and off a resumed run's server-loaded snapshot when that is
  dumped into the row — while still reaching the model during the run, the only
  place they were needed. Text parts survive, the tool message naming what was
  read survives, and a message left holding nothing is dropped rather than
  stored as an empty turn. Message ids and the non-standard `attachments` refs
  survive too: the strip copies with `model_copy`, never by re-validating
  through the adapter, which would regenerate ids and discard the extras the
  previous entry exists to keep.

  **The rule is one-sided on purpose: the server never persists bytes it
  generated, and never discards bytes the client sent.** The posted history is
  not touched, so an inline image a front end sends reaches both the model and
  the row exactly as sent. Stripping it as well was the tidier symmetry and the
  wrong one — `ALLOW_UPLOADED_FILES` governs provider file-id references
  (`UploadedFile`), not inline content, so a `data`-sourced image part reaches
  the model on either setting and taking it off the way in would have silently
  blinded the model to any pasted image.

  **A row written by 0.41.0 keeps its payload until something rewrites it, and
  that something is not the run loop.** With django-pydantic-agent 0.15.0 or
  newer and its `django_pydantic_agent.contrib.store` app installed, `manage.py
  agent_store_strip_inline_bytes` (`--dry-run` first) cleans stored rows in
  place, keeping every message id and the `attachments` array. A data migration
  is the right layer for stored data; rewriting what a client posted, on the way
  through a run, is not.

  **A follow-up question after a page reload now re-reads the file server-side**
  instead of finding it in history. Within a single session that was already the
  behaviour — the bytes never travelled the event stream (measured: 0 of 1828
  bytes across 11 events), so the client had nothing to post back and the model
  simply called `read_attachment` again. The stored copy only ever paid off
  across a reload, and it charged every load and every subsequent turn for it.

- **`RunAgentInput.context` was never read, so nothing a client put there reached
  the model.** That field is where an AG-UI front end describes the user's
  situation — what page they are on, what they have selected — and
  `AGUIAdapter` consumes only `thread_id`, `run_id`, `id`, `messages`, `tools`
  and `state`. Leaving `context` (along with `forwardedProps` and `parentRunId`)
  to the consumer is deliberate upstream policy, not an upstream bug — and this
  package is the consumer that never wired it. Every entry was parsed,
  validated, and dropped on the floor. What a user saw: an agent answering a
  question about the screen in front of them with "I don't have one in this chat
  yet", and an agent asked about the PDF they had just attached replying "please
  attach the PDF here". Both sources now reach the model as a fenced, labelled
  block delivered as additional run instructions, after the operator's own. ⇒ *A
  page-aware client stops having to paste its own context into the user's
  message to get it seen.*

  **Client-supplied text now reaches the model where it previously did not.**
  That is the fix, and it is also a widening: whatever your front end puts in
  `context` is now in front of the model on every request of every run. The block
  says in as many words that its contents are data and not instructions, and the
  marker is neutralised wherever a client value contains it so the fence cannot
  be forged or closed early — but fencing frames text, it does not sanitise it,
  and it is no defence against instructions hidden *inside* a page or a file.
  `DJANGO_AG_UI["RUN_CONTEXT"] = {"CLIENT_CONTEXT": False}` restores the old
  silence. A project whose client never populated the field sends no block at all
  and is unaffected.

- **Attachment refs are now derived from the messages themselves, so they survive
  the next turn.** The composer rides them on the user message as an
  `attachments` field: undeclared by AG-UI, kept intact by `ag_ui.core`'s
  `extra="allow"` validation, and ignored by `AGUIAdapter.load_messages` — so the
  ids never reached the model and `read_attachment` had nothing to be called
  with. The manifest is built from the posted message list rather than from this
  run's own uploads, because the client clears its per-run list once a run
  settles; derived from the messages, a file attached ten turns ago is still
  listed (once — refs are deduped by id) when the user finally asks about it.

- **A completed run no longer re-dumps the client's turn.** `on_complete` stored
  `dump_messages(result.all_messages())` — the *model's* history, round-tripped
  back to the wire — which regenerated every message id and dropped the
  `attachments` field off the user message. Reloading a thread therefore lost its
  attachment chips, and the ids the model had been told about matched nothing
  stored. The prior turns are now stored as the client posted them, and only the
  run's own new messages are dumped.

  The same helper closes a second gap on the way: the failure/cancellation path
  built its list from the client's messages alone, never including the
  server-loaded history, so a **resumed** run that errored or was cancelled
  persisted only the new turn and silently truncated the thread it was resuming.

  **Two behaviour changes ride along.** A client-posted **system** message is
  now stored — it is still stripped before the model sees it (`sanitize_messages`,
  under the default `MANAGE_SYSTEM_PROMPT = "server"`), so it is inert, but it is
  in the row where before it was dropped on the way to storage. And a stored user
  message now keeps the **client's** id rather than a freshly generated one, which
  is the point of the change: it is what makes an attachment ref still resolvable
  after a reload.

### Added

- **`RUN_CONTEXT` and `RunContextConfig`**,
  resolved per endpoint like every other scalar: `CLIENT_CONTEXT` and
  `ATTACHMENT_MANIFEST` (both `True` by default — the flags are read the way
  `TOOL_FAILURE` is rather than `TOOL_GUARD`, so an empty dict and no dict at all
  are the same answer) plus `MAX_CHARS` (`20000`). The ceiling exists because
  `context` is unbounded client text limited only by
  `DATA_UPLOAD_MAX_MEMORY_SIZE`, and run instructions are re-rendered on *every*
  model request, so an over-eager page map is paid for repeatedly; content over
  the limit is truncated behind a visible marker naming it rather than dropped in
  silence. `AGUIConfig` accordingly gains a required `run_context` field — build
  it through `build_ag_ui_config(...)`, as its own docstring already asks, rather
  than constructing it directly.

## [0.41.0] — 2026-08-11

### Fixed

- **`csrf_exempt` now reaches every view `AGUIServer` mounts, not the run
  endpoint alone.** It was passed into `DjangoAGUIView` while the sub-views were
  built from the auth dict beside it, so `csrf_exempt=True` exempted the stream
  and left five write routes under `CsrfViewMiddleware`: `POST /attachments/`
  (upload), `DELETE /attachments/<id>/`, `PATCH /threads/<id>/` (rename),
  `DELETE /threads/<id>/`, and `POST /transcribe/`. Each answered a hard **403**
  for exactly the header-authenticated client the exemption exists to serve, and
  no consumer-side setting could fix it — `CSRF_USE_SESSIONS = True` mints no
  readable `csrftoken` cookie, and a JWT-authenticated SPA has no session to
  carry one, which is the reason the run endpoint is exempt in the first place.

  **The failure read as half-broken rather than misconfigured**, which is why
  it survived: chatting worked, history listed fine, and a *new* thread is
  created through the run stream, so only the writes died. `tools/`, `skills/`
  and `runs/` were unaffected because they are `GET`-only — they would have
  broken the same way the day either gained a write verb. ⇒ *Cookie-less
  deployments get working uploads, thread rename/delete and voice input with no
  mount-point patching.*

  **This widens what the *unstated* default covers.** `csrf_exempt` left unset
  resolves to exempt, and now resolves that way across the mount — so a project
  that passed nothing goes from "writes enforced by accident" to "writes
  exempt". Two groups: one already gets the `RuntimeWarning` at construction and
  already has the strictly larger hole open on the run endpoint, where tools act
  as the logged-in user; the other passes a `get_user` hook, which silences the
  warning precisely because the request carries its own credential rather than
  the session cookie. Neither is left worse off than the run endpoint already
  left them, but the change is a widening and not only a fix — **if you want
  CSRF enforced, state `csrf_exempt=False`**, which behaved correctly throughout
  and still does. Note too that `csrf_exempt=True` lets a cross-site page `POST
  multipart/form-data` to `attachments/` without a preflight; the store is
  owner-scoped, the response is unreadable cross-origin and
  `ATTACHMENT_MAX_BYTES` caps the size, so it is a nuisance upload rather than a
  disclosure.

  The flag now resolves through one shared helper, and the policy travels in the
  same dict as `require_authenticated` / `get_user` / `authorize` — a key added
  there reaches every view or fails loudly at construction, where a second
  forwarding path could silently miss one. `ToolsView`, `ThreadsView`,
  `AttachmentsView` and `TranscribeView` accordingly accept `csrf_exempt=` when
  constructed directly, as they already accept the auth arguments.

  **Nothing could have caught this.** `tests/conftest_settings.py` mounts no
  middleware, so an exempt view and an enforced one are indistinguishable in the
  suite. The regression tests assert the flag across the whole set derived from
  `server.urls` — never a hand-written path list, which cannot cover a view a
  later release starts mounting — and drive the same mount through a real
  `CsrfViewMiddleware`, because asserting the attribute proves only what the
  views declare, not what Django does with the declaration.

- **Docstrings still described the removed `get_urls` factory and the removed
  settings-resolved collaborators.** `ThreadsView`, `AttachmentsView` and
  `TranscribeView` each opened with "Mounted by `get_urls` with
  `threads=<store>`" — a Sphinx cross-reference to a symbol that no longer
  exists, naming keyword arguments that no longer exist, on three classes whose
  docstrings are published in the API reference. They now name `AGUIServer` and
  the real constructor arguments (`conversation_store=` / `attachment_store=` /
  `transcription_backend=`).

  **Two of these were instructions that break a project rather than merely
  dangling references.** `OpenAITranscriptionBackend` told you to enable it by
  pointing `DJANGO_AG_UI["TRANSCRIPTION_BACKEND"]` at its dotted path, and
  `NullTranscriptionBackend` raised `NotImplementedError` advising the same key —
  but that key, along with `CONVERSATION_STORE` and `ATTACHMENT_STORE`, is
  refused at startup by `check_removed_settings`, so following the documentation
  produced an `ImproperlyConfigured`. `AGUIServer`'s own docstring likewise still
  claimed the three collaborators "default to the `DJANGO_AG_UI`
  settings-resolved backend" and that "configuring a store in settings mounts its
  sub-view automatically". They are passed or absent; unpassed, each falls back
  to its `Null*` backend and the sub-view simply does not mount.

- **The same docstring also claimed `audit_logger` and `csrf_exempt` fall back to
  settings.** Neither has been settings-resolvable since collaborators became
  constructor arguments; `AGUIConfig` carries no such field. `model` and
  `instructions` still do, and the docstring now says only that.

## [0.40.0] — 2026-08-11

### Changed

- **The upper bound came off every sibling window: `django-pydantic-agent>=0.13`,
  `djangorestframework-mcp-server>=0.30`, `djangorestframework-pydantic-ai>=0.16`,
  `pydantic-ai-harness[code-mode]>=0.13`, `ag-ui-protocol>=0.1.19`.** Each was a
  one-minor window, and for the first three that window sat over a package we
  ship ourselves — which is not a compatibility statement but a *schedule*:
  every upstream release made this package unresolvable until someone re-cut it,
  whether or not anything broke. Against that there is no recorded case of a
  ceiling here catching a real incompatibility, while they caused four incidents
  in this ecosystem, including a **Security** release published-and-unreachable,
  and two disjoint windows that resolved *successfully* by silently downgrading
  a consumer past every fix. ⇒ *`django-admin-agent` and any other consumer can
  now combine this transport with the current substrate on the day it ships.*

  **`ag-ui-protocol` and `pydantic-ai-harness` are external 0.x packages,
  where a minor bump is breaking by SemVer and we do not control the release.**
  That is the riskiest part of this change and it is a bet, not a proof: that
  the weekly drift job finds a breaking 0.x minor faster than a stale ceiling
  would have been noticed. The evidence behind the bet is that a stale harness
  ceiling has already made this stack unreachable twice, and neither time was
  the ceiling what noticed. The `>=0.1.19` floor on `ag-ui-protocol` is
  unchanged and still load-bearing — it is what gates the interrupt/resume
  approval lifecycle, and `pydantic-ai-slim` only floors it at `>=0.1.10`.

- **`pydantic-ai-slim` keeps every `<3`.** A major bound is a real compatibility
  statement — the v2 capability seam and the `[ag-ui]` adapter are what this
  transport is built on — and nothing here argues for dropping it.

### Added

- **A `floor` job in `tests.yml`, wired into the `tests` aggregate gate.** It
  resolves every *declared* dependency at `--resolution lowest-direct` and runs
  the suite, then installs the package **alone** — no extras, no dev group — and
  imports it plus a few public symbols. ⇒ *The two measurements that replace the
  ceiling are now both in place: `upstream-drift.yml` resolves unpinned weekly
  (the newest end), and `floor` resolves lowest-direct per PR (the oldest end).*
  An all-extras install cannot check a floor on its own, because one extra can
  hold a shared dependency above the floor being claimed.

## [0.39.0] — 2026-08-11

### Fixed

- **`AGUIServer(service_specs=...)` is typed for the shapes it actually
  accepts.** The annotation said `Mapping[str, Any] | SpecSource | None` while
  the docstring on the same method documented handling a pre-built toolset.
  Passing one worked at runtime and `ty` flagged it, so consumers added a
  suppression on correct code — which trains them to suppress this class of
  error generally.

  **A runtime test could never have caught it**, and one already existed:
  constructing with a pre-built toolset was covered and passed against the
  broken annotation. `SpecToolset` satisfies a `runtime_checkable` `SpecSource`
  because `hasattr(x, "specs")` is true of a property, while failing
  assignability — so the structural check and the type checker disagreed, which
  is exactly how the defect survived. The guard added alongside runs `ty` over a
  consumer-shaped snippet.

  The two new protocols are declared structurally rather than importing
  drf-pydantic-ai's concrete classes, for the same reason `SpecSource` is
  duck-typed: that package arrives only with the optional `[spec-tools]` extra,
  so naming its types in a signature would force the dependency on every
  install.

### Changed

- **Upstream windows moved onto the releases where a `FilterSet` owns
  ordering** — `django-pydantic-agent>=0.13,<0.14`,
  `djangorestframework-mcp-server>=0.30,<0.31`,
  `djangorestframework-pydantic-ai>=0.16,<0.17`. No code change here; ordering
  reaches the model through the toolsets these packages build.

- **The `[harness]` window widened to `<0.19`, from a `<0.17` that had gone
  stale against a published 0.18.1.** The extra resolved to 0.16.x and could not
  combine with a project on the current harness. ⇒ *The third instance of
  published-and-unreachable in this stack, and the first a consumer spotted —
  from our own changelog wording, which makes the pattern legible from outside
  and leaving one open more expensive than the bump.*

## [0.38.0] — 2026-08-11

### Changed

- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.29,<0.30`**
  (was `>=0.28,<0.29`), and the `django-pydantic-agent` floor moves to
  `>=0.12,<0.13`.

  **Both edits are needed, and neither works alone.** drf-mcp 0.29.0 was
  published-and-unreachable through this package's own extra; moving that
  ceiling alone still leaves the bridge's backing package pinned to a
  `django-pydantic-agent` whose `[drf-mcp]` extra caps at `<0.29`. The two
  ceilings are the same ceiling wearing different labels, so they move
  together.

  What 0.29.0 carries is a *default*, not a fix: drf-mcp's `MAX_PAGE_SIZE`
  drops from 500 to 100, so a `paginate=True` selector tool no longer
  advertises a `limit.maximum` five times its own dispatch default — a model
  reads `maximum` as a target, which is how an unconfigured deployment came to
  request 500 rows.

  No code changes; the suite passes unmodified at 100% coverage against
  drf-mcp 0.29.0 and django-pydantic-agent 0.12.0.

## [0.37.0] — 2026-08-11

### Changed

- **A skill can now keep its prompt on the server.** `SkillSpec.prompt` is
  optional; leave it unset and the catalog advertises only the name and label,
  with the client sending the bare `/name` token for the agent to resolve —
  from the harness `Skills` capability, or from your own instructions.

  **The catalog is a plain `GET`, so a prompt in it is public to anyone who
  can reach the endpoint and sits in the page for anyone who opens the source.**
  A skill is often where a project's internal workflow is written down most
  plainly, which made shipping it to the browser the wrong default. Setting
  `prompt` is still right for a user-facing convenience, or for one carrying
  `{placeholder}`s only the page can fill.

  Additive: `prompt` keeps working exactly as before when set, and the key is
  **omitted** rather than serialised as `null` when it is not.

## [0.36.0] — 2026-08-11

### Fixed

- **Stored conversations were served with the wrong key names, and a restored
  transcript lost every tool call and tool result.** `messages_to_jsonable`
  dumped the AG-UI message union by Python field name rather than by alias, so
  the thread endpoint returned `tool_calls` / `tool_call_id` / `encrypted_value`
  to a client reading the protocol's `toolCalls` / `toolCallId`. On reload an
  assistant turn that was nothing but tool calls rendered as nothing at all, and
  every tool result missed the card it belonged to. Prose survived, so the
  transcript looked thin rather than broken.

  **The reason it went unnoticed is worth more than the fix.** `ag_ui.core`
  sets `populate_by_name=True`, so decoding accepts either spelling: encode →
  store → decode → resume agreed with itself perfectly, and the suite asserted
  exactly that. **A round-trip proves agreement, not correctness** — two ends
  using the same non-wire spelling agree completely. The mismatch could only
  appear where another language read the JSON. The new tests assert the emitted
  **keys**.

  **The fix is two-sided.** Dumping by alias corrects new writes; rows already
  stored hold the old spelling, so `ThreadsView` now re-serialises on read
  instead of handing storage records straight out. Both eras come back on the
  wire shape and **no data migration is needed**.

- **A run that ends in error now persists the exchange.** Completion was covered
  by `on_complete` and a client disconnect by the stream guard, which left the
  third exit saving nothing at all: a failed run dropped the whole turn from the
  thread, including the user's own message. The partial exchange is now stored
  the way a cancelled one is, and audited at the run level.

### Changed

- **`django-pydantic-agent` floor raised to `>=0.11,<0.12`**, for
  `ToolFailurePolicy`. A tool that raises now fails its own call instead of the
  whole run: the model gets a result marked failed naming the tool, and the turn
  continues rather than ending in `RUN_ERROR` with everything else discarded.

  New `TOOL_FAILURE` settings block (and a `build_ag_ui_config(tool_failure=)`
  keyword): `ENABLED` defaults to `True` — the one policy here whose
  absent-settings answer is "on" — and `INCLUDE_DETAIL` defaults to `False`, so
  the exception's text does not reach the model or the browser rendering its
  answer. Your `AuditLogger` still receives the real exception.

  This changes behaviour for an existing project: a run that used to die on a
  raising tool now completes. Set `"TOOL_FAILURE": {"ENABLED": False}` to keep
  the old behaviour.

## [0.35.0] — 2026-08-10

### Changed

- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.28,<0.29`**
  (was `>=0.27,<0.28`), and the `django-pydantic-agent` floor moves to
  `>=0.10,<0.11`.

  **The previous ceiling excluded a security release.** drf-mcp 0.28.0
  refuses an authenticated caller with no `pk` instead of collapsing every such
  caller onto the shared `"anonymous"` principal — where any two of them can
  present each other's sessions. This package, `django-pydantic-agent` and
  `django-admin-agent` all pinned `<0.28`, so the fix was **published and
  unreachable to the whole ecosystem**: the announcement reads as completion
  while every install keeps resolving the vulnerable version.

  **It reaches the bridge, not just the MCP endpoint.** `DRFMCPToolset` calls
  `list_tools` / `acall_tool`, so a project using `drf_mcp_server=` runs the
  same principal resolution the HTTP transport does — this is not an
  HTTP-only concern.

  **Found from the other end, by the extras-matrix check.** Bumping
  `django-admin-agent[mcp]` to `>=0.28` made it *unsatisfiable* against this
  package's `[drf-mcp]` at `<0.28` — the disjoint-window shape again, one layer
  down from the pair 0.34.0 fixed. The conflict was the symptom; the stuck
  security floor was the cause. ⇒ *a resolution conflict is worth reading as a
  question about which side is stale, not only as a pin to widen.*

  No code changes; the suite passes unmodified at 100% coverage.

## [0.34.0] — 2026-08-10

### Changed

- **`django-pydantic-agent` floor raised to `>=0.9,<0.10`.**

  **Not a routine bump — the previous ceiling excluded a fix aimed at this
  package.** dpa 0.9.0 moves its `[spec-tools]` and `[harness]` windows onto the
  ones used here; until this floor moves, that release is unreachable and the
  defect stands.

  **The defect it closes never raised an error.** A project asking for both
  packages' extras — `django-ag-ui[spec-tools]` alongside
  `django-pydantic-agent[spec-tools]` — resolved *successfully* by silently
  **downgrading django-ag-ui to 0.3.0** (0.17.0 for the `[harness]` pair). A
  resolver satisfies disjoint windows by walking the consumer back to a version
  whose pins overlap, and there is no version far enough back to be refused, so
  the install looked clean while shipping a transport from months earlier —
  behind the fail-open auth fix and the closed-by-default authentication flip.

  **Neither package was wrong alone**, and `django-ag-ui[spec-tools]` on its
  own was fine too, since dpa arrives here as a plain dependency with no extras.
  It took asking for both, which no per-package check does.

  Verified with both packages' full extras resolved together and the versions
  asserted: dpa 0.9.0 · PAI 0.15.0 · harness 0.16.0 · drf-mcp 0.27.0 ·
  drf-services 0.35.0. Suite green untouched at 361 tests.

## [0.33.1] — 2026-08-10

### Fixed

- **28 references to settings removed in 0.19.0**, across the README and eight
  doc pages — `TOOLSETS`, `CAPABILITIES`, `AGENT_FACTORY`, `PROVIDER`,
  `SERVICE_SPECS`, `CONVERSATION_STORE`, `ATTACHMENT_STORE`, `DRF_MCP_SERVER`.
  All of them are constructor arguments now, and setting any of the old keys
  raises `ImproperlyConfigured` — so the docs taught a configuration that fails
  at startup.

  **Two of them contradicted the snippet directly beneath them**: "`CAPABILITIES`
  takes dotted paths to zero-argument callables", followed by an example passing
  `capabilities=[…]` to `AGUIServer`. Dotted paths were removed wholesale; there
  is no `import_string` in this package.

- **There is no `ALLOW_ANONYMOUS` setting**, and there never was one that
  worked. It was documented as the default for
  `ModelConversationStore(allow_anonymous=)`, and nothing read it — not this
  package, not `django-pydantic-agent`.

  **It could not have worked.** *You* construct the store and pass it in, so
  there is no point at which this package could apply a settings value to it. A
  project that set the key got the `False` default and no indication otherwise.
  Documented now as what it is: a store constructor argument.

- **Two API cross-links pointed at the old package path** —
  `build_model` and `build_tool_catalog` moved to `django-pydantic-agent`, and
  the links still said `django_ag_ui`.

- **Said plainly that SSE stream resumability does not exist.** `resume/` and
  `fork/` seed a **new run** from a saved snapshot; they do not reattach to an
  interrupted stream. AG-UI has no such primitive — there is no `Last-Event-ID`
  replay and no way to rejoin a run in flight, so a client should treat a
  dropped stream as a lost run and offer resume as a deliberate action.

### Added

- **`make docs-check` now runs in CI**, and checks more than it did.

  **Its fence pattern matched only a bare ` ```python `,** so every fence
  titled ` ```python title="urls.py" ` was skipped — while the run still
  reported clean. Those are the copy-this-into-your-project examples.

  **It now binds each call's arguments** rather than only checking keyword
  names, which catches an argument passed positionally to a keyword-only
  parameter. That reads perfectly and a name-only check cannot see it, because
  the keyword was never written down.

- **Missing anchors now fail the docs build.** mkdocs reports them at `INFO`, so
  a link to a heading that moved survives a clean `--strict` build; two had
  accumulated exactly that way. `validation.anchors` is now `warn`, and CI runs
  `--strict`.

- **A test asserting every `__all__` name is actually bound**, checked against
  the source rather than the imported module — importing `pkg.thing` binds
  `thing` on `pkg`, so a runtime `hasattr` check passes while
  `from pkg import thing` hands back a module that is not callable.

## [0.33.0] — 2026-08-10

### Added

- **A `throttle` hook on the agent endpoint**, plus a cache-backed
  `FixedWindowThrottle` reference implementation.

  ```python
  from django_ag_ui import AGUIServer, FixedWindowThrottle

  AGUIServer(registry, throttle=FixedWindowThrottle(max_runs=20, per_seconds=60))
  ```

  One method — `consume(request) -> int | None` — returning the suggested
  `Retry-After` in seconds, or `None` to allow the run. A refusal is `429` with
  a `Retry-After` header.

  **Applied to the agent endpoint only.** It is the one route that costs a
  model call per request; the catalogs and the thread drawer are cheap reads and
  keep sharing the auth seam instead.

  **It runs after authentication**, so a limiter can key on the acting user
  rather than only an IP — including a user established by a `get_user` hook —
  and **before** the body is parsed, so a throttled request costs nothing beyond
  the auth it already did. A request that was going to be `401` never spends
  quota.

  **One method, not check-then-commit.** `consume` is the gate *and* the
  bookkeeping update, because "check, then commit" races under exactly the
  concurrency a limiter exists for.

  **`consume` is synchronous**, run off the event loop so it may touch the
  cache or the ORM directly. An `async def consume` is **refused at
  construction**: awaiting it silently would make every request a `429` whose
  `Retry-After` is a coroutine, so the endpoint would look rate-limited rather
  than misconfigured. Same call, same reasoning as the MCP permission and
  rate-limit hooks.

  The contract mirrors `djangorestframework-mcp-server`'s `MCPRateLimit`, so a
  project protecting both transports writes one kind of limiter.

## [0.32.0] — 2026-08-10

### Added

- **`model_for_request` / `instructions_for_request`** — two narrow
  `(request) -> value` hooks on `AGUIServer` / `DjangoAGUIView` for the
  per-tenant model and the per-tenant system prompt.

  ```python
  AGUIServer(registry, model_for_request=lambda request: request.tenant.model)
  ```

  A model string goes through the same `API_KEY` / `provider=` resolution the
  configured model does, so a hook returning `"anthropic:…"` behaves like the
  setting rather than falling back to environment inference.

  **Narrow on purpose, and the reason is the agent reuse below rather than
  ergonomics.** A hook handed the whole request could vary the agent on anything
  it read off one, and that is not a set anyone can enumerate — so a reused agent
  behind it is either impossible to justify or wrong for the second tenant, and
  the second failure is silent. Two named axes can be reasoned about.

  They also sidestep the `agent_factory=` cliff: supplying a factory turns off
  the drf-mcp bridge, the spec capability, step persistence, the attachment
  toolset, `MODEL_SETTINGS`, `RETRIES`, `toolsets` and `capabilities` in one go.
  Varying the model per tenant should not cost all of that.

### Changed

- **The agent is built once per endpoint and reused by every run**, instead of
  being rebuilt per request. Rebuilding meant re-deriving a JSON Schema for
  every registered tool on every call — the most expensive thing the endpoint
  did, repeated to produce a byte-identical result.

  **What made the rebuild look unavoidable was that the agent carried
  request-shaped things.** It no longer does: the drf-mcp toolset, the
  `read_attachment` toolset, the `StepPersistence` capability and the per-run
  model / instructions all ride the **run** now, through pydantic-ai's own
  per-run `toolsets` / `capabilities` / `model` / `instructions`. What stays on
  the agent is exactly what the constructor fixed, which is what makes the reuse
  provable rather than merely plausible.

  **One thing had to move upstream first: the client IP.** It was closed over
  when the agent was built, so a reused agent would have stamped every audit
  record with the IP of whoever arrived first — well-formed records, wrong
  provenance, nothing to notice it by. It now rides `AgentDeps.ip_address`,
  which is why this release requires **`django-pydantic-agent>=0.8`**.

  Instructions are deliberately **absent** from the agent and supplied per
  run. Pydantic-AI treats per-run instructions as *additional* to the agent's,
  which is exactly a replacement when the agent carries none — and baking them in
  would have made `instructions_for_request` either impossible or a cache key,
  which a project can vary per user and so would never hit.

  No API changes. An `agent_factory=` is likewise called once; it takes no
  request and never did.

- **`django-pydantic-agent` floor raised to `>=0.8,<0.9`.**

## [0.31.0] — 2026-08-10

### Changed — BREAKING

- **`require_authenticated` now defaults to `True`.** Every endpoint this
  package mounts — the agent endpoint, the tool and skill catalogs, the thread
  drawer, the attachment routes, transcription, and the run index — refuses an
  anonymous request with `401` unless told otherwise.

  **Deployments currently serving anonymous runs will get 401s until they opt
  in.** The one-line migration:

  ```python
  AGUIServer(registry, require_authenticated=False)  # was the old default
  ```

  Passing it to `AGUIServer` waives the requirement across the whole mount, the
  same way passing `True` used to lock it down. The individual view classes take
  the same argument.

  **Why break it.** Server-side tools act as `request.user`. With the old
  default, a project that mounted the endpoint and configured nothing had an
  agent any visitor could drive — and an endpoint that *looks* configured is the
  failure mode worth breaking a release over, because nothing about it is
  visible from the outside. Refusing the request is the only state an operator
  cannot miss.

### Added

- **A construction-time warning for the CSRF configuration nothing else can
  see.** Building an endpoint that leaves `csrf_exempt` unstated *and* supplies
  no `get_user` hook now emits a `RuntimeWarning`.

  **This is the deployment the `require_authenticated` default cannot
  reach.** That default refuses anonymous callers, which is the whole of the
  protection when nobody is logged in. It says nothing about the case where
  callers *are* authenticated — by session cookie, through Django's auth
  middleware — and the endpoint is CSRF-exempt. There, any third-party page can
  drive the agent as the logged-in user, and every request looks perfectly
  authenticated from the inside.

  **The unstated state is the signal, not the value.** `csrf_exempt=True` is
  a defensible choice — it is right for Bearer / API-key clients, where CSRF
  does not apply — so warning on the value would fire on a correct
  configuration with no way to say so, which is how a project learns to filter
  the warning. Any of three answers silences it: `csrf_exempt=False` (cookie
  auth, CSRF enforced), `csrf_exempt=True` (deliberately exempt), or a
  `get_user` hook (the request carries its own credential).

  `csrf_exempt` accordingly accepts `None` (the new default, meaning *unstated*)
  as well as `True` / `False`. Unstated still resolves to exempt, so the
  attribute Django's `CsrfViewMiddleware` reads is unchanged.

- **A `Security defaults` section in the README** covering all three: the
  anonymous refusal, establishing the acting user with `get_user`, and the
  CSRF/cookie trade-off. The material existed only as a `DjangoAGUIView`
  docstring, which is not where anyone evaluating the package looks.

## [0.30.0] — 2026-08-10

### Added

- **`service_specs=` accepts an already-built `SpecToolset` or
  `SpecCapability`**, not just a mapping or a `SpecRegistry`.

  **The two escape hatches used to cost each other.** `service_specs=` could
  pass only the mapping, so a project needing any `SpecToolset` knob —
  `max_page_size`, an `exception_map`, a `build_context` override, or
  `require_permissions=False` while migrating — had to abandon it for
  `capabilities=`. That path is not wired into the tool catalog, so its
  tool-call cards render unlabelled. Taking the powerful form meant losing the
  labels.

  **Now it doesn't.** The endpoint attaches the object as-is *and* extracts
  its `specs` for the tool catalog and the tool-name dedup, so the powerful form
  and the labelled form are the same form.

  **A pre-built toolset is not filtered.** For a mapping, a tool name the
  `@tool` registry already defines is dropped in the registry's favour; that
  cannot apply to an object the consumer built, so a collision is **refused at
  construction**. Left alone, pydantic-ai raises `UserError` for the duplicate
  *mid-run*, long after the catalog looked clean.

  **Nor is it re-checked.** Its constructor already ran the
  `permission_classes` check and may have been given `require_permissions=False`
  on purpose — which is the entire reason for accepting one. Re-validating on
  arrival would take that decision back and leave no route to it.

### Changed

- **`[spec-tools]` requires `djangorestframework-pydantic-ai>=0.15`**, for
  `SpecToolset.specs` — the synchronous enumeration this needs to feed a
  pre-built toolset's names into the catalog and the dedup pass.

## [0.29.0] — 2026-08-10

### Changed — BREAKING for `service_specs=`

- **`AGUIServer(service_specs=…)` now refuses a spec with no
  `permission_classes`**, raising `ImproperlyConfigured` at construction and
  naming every offender at once.

  `permission_classes=None` means *inherit* over HTTP — the viewset's own
  classes, then DRF's `DEFAULT_PERMISSION_CLASSES`. A spec dispatched off HTTP
  has neither, so one that is correctly guarded behind a viewset, with passing
  HTTP tests, was **callable by whatever the model decided to call**. Set
  `spec.permission_classes` on each.

  **The check is upstream (`djangorestframework-pydantic-ai` 0.13); what this
  release adds is *when*.** This transport builds its spec capability **per
  request** — it needs the request-scoped name set for tool dedup — so the
  upstream refusal alone would surface as a 500 on the first agent call rather
  than as a failure to start. An operator reading a traceback in `urls.py` is in
  a different situation from one reading it in Sentry a week later.

  Constructing a `DjangoAGUIView` directly still fails per request. That is
  the un-documented path; the general fix is for the view to stop rebuilding the
  capability every request, which is a larger change to the construction seam.

  **Migrating a registry too large to guard in one commit?** Attach the
  capability yourself instead of using `service_specs=` — it is the only place
  PAI's `require_permissions=False` is reachable:
  `AGUIServer(registry, capabilities=[SpecCapability(SPECS,
  require_permissions=False)])`. That path skips `service_specs=`'s
  tool-catalog registration, so tool-call cards render unlabelled. A migration
  step, not a destination.

- **List tools advertise `ordering`, not `order`** (PAI 0.13), and only where
  the toolset declares `ordering_fields`. **The `service_specs=` example in
  `docs/configuration.md` would itself have raised** — its two specs carried no
  `permission_classes`. Fixed, along with a stale claim in the same paragraph
  that the acting user is bound from `request`; it comes from the run's `deps`,
  which is what lets one built agent serve many runs.

### Changed

- **Floors raised**: `django-pydantic-agent>=0.7`, `[spec-tools]` to
  `djangorestframework-pydantic-ai>=0.13`, `[drf-mcp]` to
  `djangorestframework-mcp-server>=0.27`.

  **The drf-mcp move is what makes the pair installable.** PAI 0.13 requires
  `djangorestframework-services>=0.35,<0.36` while drf-mcp 0.26 required
  `>=0.34.0,<0.35` — disjoint, so `django-ag-ui[drf-mcp,spec-tools]` **could not
  resolve at all**. drf-mcp 0.27.0 moves its window to 0.35.

- **`[harness]` now requires `pydantic-ai-harness[code-mode]>=0.13`** (was
  `>=0.12`), and every `SlidingWindow` reference here is now
  `SlidingWindowCompaction`. harness 0.13 renamed the strategy and kept the old
  spelling as a deprecated alias that warns on import and is slated for removal.

  **A raised floor rather than a compat shim**, because the two spellings
  never overlap: `SlidingWindowCompaction` does not exist on 0.12, so no single
  import works across the old `>=0.12,<0.17` range. Nothing in this package
  constructs a harness capability — the consumer does — so the floor costs a
  version that only the deprecated name could reach, and buys one honest
  spelling in the docs and tests.

  **The package never imported the alias.** The only non-test occurrence was
  a docstring example on `CompactionObserver`, which is why the
  `HarnessDeprecationWarning` showed up in this repo's own test runs and never
  in a consumer's — the floor raise is the tidy-up, not a fix for anything
  consumers were hitting.

## [0.28.2] — 2026-08-10

### Security

- **`[drf-mcp]` now requires `djangorestframework-mcp-server>=0.26`** (was
  `>=0.25,<0.26`), which closes a fail-open authentication defect. The old
  exclusive ceiling *excluded* the fix, so installing this extra resolved to the
  vulnerable release.

  **The bridge is in the blast radius, not just the HTTP transport.** The
  `[drf-mcp]` toolset calls `MCPServer.list_tools` / `acall_tool` in process, so
  it runs the same permission and listing checks — and those were the sites that
  failed open. A project whose `MCPPermission.has_permission` or `is_listable`
  was written `async def` got back an un-awaited coroutine, which is truthy and
  never `None`, so **every binding was listed and every call granted**, to the
  agent as much as to an HTTP client. Upstream now raises `ImproperlyConfigured`
  naming the offending class, and sweeps the same shape across the auth backend,
  rate limiters, and the sync transport's session store.

  No code changes here — this package supplies none of those hooks. A project
  that does write one `async def` will see a loud refusal where there was a
  silent yes.

## [0.28.1] — 2026-08-08

### Changed

- **`[harness]` now accepts `pydantic-ai-harness[code-mode]>=0.12,<0.17`** (was
  `<0.13`). A widened ceiling rather than a raised floor, because nothing in this
  package constructs a harness capability — the consumer does, and
  `CompactionObserver` reads only `.messages` off the request context either
  side of the delegated call. That surface is unchanged across 0.12–0.16, so
  every pairing in the range genuinely works.

  **harness 0.13 renamed `SlidingWindow` to `SlidingWindowCompaction`**,
  keeping the old name as a deprecated alias. The docs keep using
  `SlidingWindow` because it is the only spelling that imports across the whole
  accepted range — `SlidingWindowCompaction` does not exist on 0.12. Pin harness
  to `>=0.13` in your own project to use the new name warning-free.

  **The upgrade broke only the tests, and for a reason worth recording.** Two
  hand-rolled `_RequestContext` doubles here carried nothing but `messages`;
  harness 0.13 started reading `model` off the context to notice a capability
  swapping the model mid-run, and the doubles fell over. They were replaced with
  the genuine `pydantic_ai.models.ModelRequestContext`, which costs four
  keywords to build and cannot drift from the contract it stands for.

## [0.28.0] — 2026-08-07

### Changed

- **Floors raised across the drf chain**: `django-pydantic-agent>=0.6`,
  `[drf-mcp]` → `djangorestframework-mcp-server>=0.25`, `[spec-tools]` →
  `djangorestframework-pydantic-ai>=0.12`. Dev-group pins move in lockstep with
  the extras they back.

  **Floors rather than widened ceilings**, because drf-mcp 0.25 changes
  behaviour rather than adding surface: an unguarded tool now *raises* at
  registration instead of warning, and a request with no `Mcp-Session-Id`
  returns `400` rather than `404`. Admitting 0.24 alongside 0.25 is a pairing
  that resolves cleanly and behaves differently — which no resolver can see.

  **It arrived as an import failure, not a test failure.** Three bridge
  fixture servers here registered tools with no permissions and stopped
  collecting outright. They now declare `AllowAny` explicitly — the honest form
  for a fixture. Consumers upgrading should expect the same shape;
  `REST_FRAMEWORK_MCP['REQUIRE_TOOL_PERMISSIONS'] = False` is the migration
  hatch.

### Security

- **`cryptography` 48.0.1 → 50.0.0** (HIGH — PKCS#7 `EnvelopedData` decryption
  exposing a Bleichenbacher oracle), **`pyasn1` 0.6.3 → 0.6.4** (HIGH), and
  **`pymdown-extensions` → 11.0.1** (MEDIUM — path traversal in the `b64`
  extension). All three open advisories on this repo are closed.

- **Tested against Django 6.1**, with the lock moved to `djangorestframework>=3.18`.
  Django 6.1 removed `django.utils.cache.cc_delim_re`, which DRF 3.17.x
  imports at module level, so that pairing fails at `import rest_framework`.

## [0.27.1] — 2026-08-02

### Changed

- **Extra floors raised to `djangorestframework-mcp-server>=0.24.1` and
  `djangorestframework-pydantic-ai>=0.11.1`.**

  These are **floor** moves, not ceiling widenings — the previous ranges already
  admitted the patched releases, so nothing was unresolvable. What they did not
  do is *guarantee* them, and the versions below the new floor carry an
  authorization bypass in their transitive `djangorestframework-services`
  dependency: nested target resolution built its kwarg pool without stripping the
  reserved dispatcher seeds, so a caller-supplied `user` key outranked the
  authenticated one in the pool that decides which row gets mutated and which set
  gets bulk-deleted. Fixed in drf-services 0.33.0.

  A version pair that resolves cleanly and leaves the bypass live is exactly
  what a resolver cannot see, which is why the floor moves rather than the
  ceiling. Installing this extra now gets the fix, rather than merely permitting
  it.

  No source changes; the full suite passes against the updated chain untouched.

## [0.27.0] — 2026-07-31

### Changed

- **Ceilings raised across the drf chain:** drf-mcp-server to `<0.25` (was
  `<0.22`), djangorestframework-pydantic-ai to `<0.12` (was `<0.11`), and
  **django-pydantic-agent to `>=0.5,<0.6`** (was `>=0.4,<0.5`).

  **This closes a live install conflict rather than refreshing stale pins.**
  drf-mcp 0.24.0 requires drf-services `>=0.32` while PAI `<0.11` required
  `<0.30`, so the `[drf-mcp]` and `[spec-tools]` extras had become **mutually
  unsatisfiable at their new versions** — masked only by ceilings old enough to
  hold both at earlier releases. The chain moved upstream-first: PAI 0.11.0 →
  django-pydantic-agent 0.5.0 → here.

  **The dpa floor is `>=0.5`, not `>=0.4`, on purpose.** 0.5.0 changes how an
  unknown drf-mcp tool is reported: drf-mcp moved that condition from `-32004`
  to `-32602` in 0.24.0 (matching the MCP spec's worked example), which made it
  indistinguishable from malformed arguments, so the bridge now answers both
  with `ModelRetry` instead of ending the run. Admitting dpa 0.4.x alongside
  drf-mcp 0.24 would pair the new error code with a bridge that still treats it
  as fatal — a combination that resolves cleanly and behaves wrongly.

  Nothing in this package needed adaptation: its drf-mcp surface is passing the
  server object through to that bridge, which is where the change lives. Doc
  snippets were re-checked against the upgraded packages (`make docs-check`).

## [0.26.3] — 2026-07-30

### Changed

- **`[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.22`**, taking in both
  0.20.0 and 0.21.0. Two consumer-reported blockers, neither of which touches
  this transport:
  - **0.21.0** — `DjangoOAuthToolkitBackend` rejected every bearer token once a
    resource URL was configured. Audience enforcement read a `resource` field
    that DOT's `AccessToken` does not have, so it could never succeed;
    enforcement is now the separate `ENFORCE_AUDIENCE`, default off.
  - **0.20.0** — dynamically registered clients could not be issued an ID token
    (`Application.algorithm` was never set), so the token endpoint 500'd whenever
    the advertised `openid` scope was requested.

  Both are confined to drf-mcp's OAuth surface. The bridge consumes `MCPServer`
  in-process and never constructs an auth backend, so no adaptation was needed —
  verified with the lock updated to 0.21.0 and the suite green.

  0.20.0 also added `UndescribedToolWarning`, which the bridge fixtures trip by
  registering throwaway tools; filtered in `pyproject.toml`.

## [0.26.2] — 2026-07-29

### Changed

- **`[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.20`**, so 0.19.0 is
  installable. That release fixes dynamic client registration, which issued
  credentials that could never authenticate: `token_endpoint_auth_method` was
  not modelled, so every registration silently became a confidential client, and
  the `client_secret` handed back was the stored PBKDF2 digest rather than the
  secret. Both are confined to drf-mcp's `contrib.oauth` — the bridge this
  extra backs consumes `MCPServer`, which did not change, so the widening is
  purely a ceiling lift and this transport's own behaviour is unaffected.

  Verified rather than assumed: the suite runs green against 0.19.0 with the
  lock updated.

## [0.26.1] — 2026-07-29

### Changed

- **Widened the two agent-tool integration pins**, matching
  `django-pydantic-agent` 0.4.2, so the current majors of both backing packages
  are installable:
  - `[spec-tools]` → `djangorestframework-pydantic-ai>=0.9,<0.11`. 0.10.0 adds
    `SpecToolset(host=…)`, the origin that makes DRF's `FileField` /
    `Hyperlinked*` fields render absolute URLs off the HTTP path. Nothing here
    uses it — the widening is what lets a *consumer* pass it.
  - `[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.19`. 0.18.0 fixes two
    reported crashes: serializer-context providers called positionally
    (`TypeError` for any provider not leading with `view, request`) and the
    missing DRF baseline context (`KeyError: 'request'`). Its one deliberate
    break — a provider whose first two parameters are named something other than
    `view` / `request` now raises — applies to *user* provider signatures, not to
    anything this package calls.

  The floors stay at `0.9` / `0.17`: neither integration uses new API, so both
  ranges are honestly satisfiable, and a project already pinned to an older
  release isn't forced forward.

## [0.26.0] — 2026-07-28

### Added

- **`CompactionObserver` — tell the client when history was compacted.**
  Compaction is deliberately invisible upstream: it runs inside
  `before_model_request`, mutates the message list, and **emits nothing**. Right
  for the model, wrong for a person watching a long run, who sees earlier turns
  quietly stop informing the answers with no explanation. Wrapping the capability
  is the seam for saying so:

  ```python
  capabilities = [CompactionObserver(SlidingWindow(max_messages=80, keep_messages=40))]
  ```

  Opt-in by construction — pass the strategy unwrapped and nothing is emitted.
  - **The compaction itself is untouched.** `CompactionObserver` subclasses
    pydantic-ai's `WrapperCapability` and overrides one hook, so ordering,
    deferral and hook introspection all delegate to the wrapped capability.
    A hand-rolled proxy would have silently dropped those.
  - **The wire stays vanilla AG-UI.** Each firing emits a standard
    `ACTIVITY_SNAPSHOT` with `activityType: "compaction"` (exported as
    `COMPACTION_ACTIVITY_TYPE`) and `content` carrying `removed` / `before` /
    `after` — not a `CUSTOM` event, so any AG-UI client can render it and ours
    is not privileged. Handle it with `@ag-ui/client`'s
    `onActivitySnapshotEvent`.
  - **Placed where a reader wants it**: interleaved immediately *before* the
    events of the turn that ran with the shortened history, with a fresh
    `messageId` per firing — a compaction is a distinct occurrence, not a
    mutation of a previous one.
  - **What it does not detect**, stated plainly: detection is a message-count
    comparison across the wrapped call, because that is all the upstream
    contract exposes. A strategy that rewrites history *without* shortening it
    does not register — matching what the indicator claims (turns were dropped)
    rather than over-promising a general "history changed" signal.
  - **Per-run, not per-instance.** The observer records into a `ContextVar`. A
    consumer builds the capability once at configuration time and the same
    instance serves every request, so instance state would interleave concurrent
    runs into each other's transcripts.

### Documentation

- **New page: [Compaction & skills](compaction.md)** — adopting the harness
  `compaction` and `Skills` capabilities through the `CAPABILITIES` seam (pure
  composition, no code here), then the indicator above. Strategy-by-strategy
  guidance lives in django-pydantic-agent's integrations guide, where the seam
  itself is documented.

## [0.25.0] — 2026-07-28

### Changed

- **`[harness]` now requires `pydantic-ai-harness>=0.12,<0.13`** (was `>=0.7,<0.8`)
  and **`django-pydantic-agent>=0.4,<0.5`** (was `>=0.3,<0.4`). The harness ceiling
  had gone five minors stale, which also gated
  [`pydantic_ai_harness.skills`](https://github.com/pydantic/pydantic-ai-harness/pull/396) —
  it does not exist below 0.11.
- **The resume / fork path needed the substrate fix that came with it.** Harness's
  `StepStore` protocol gained `latest_snapshot(*, run_id, include_interrupted=False)`
  and its `continue_run()` passes the new argument, so `resume/<run_id>/` raised
  `TypeError: latest_snapshot() got an unexpected keyword argument` under 0.12
  until `DefaultStepStore` was adapted (django-pydantic-agent 0.4.0, which adds a
  `state` column and **migration `0002`** — run `migrate` when upgrading).
  **This package's resume tests are what surfaced the break**; the substrate's own
  suite could not see it, because it calls `latest_snapshot()` directly rather
  than through the harness helper that drives the protocol.
- **`GET runs/`'s `continuable` flag still means what it says.** It is computed
  from `latest_snapshot(run_id=…)` precisely because that is the call `resume`
  makes, and both now default to `include_interrupted=False` — so a run reported
  continuable is still exactly a run `resume` would find a snapshot for. Had the
  new argument defaulted the other way upstream, the flag would have started
  lying about interrupted-only runs rather than failing loudly.
- `StepPersistence`'s constructor is unchanged at 0.12, so the capability wiring
  needed no adaptation.
- **Raise the drf-chain ceilings: `[drf-mcp]` → `djangorestframework-mcp-server>=0.17,<0.18`
  (was `>=0.15,<0.16`) and `[spec-tools]` → `djangorestframework-pydantic-ai>=0.9,<0.10`
  (was `>=0.8,<0.9`).** The MCP ceiling had gone stale a wave earlier — drf-mcp
  0.16.0 (MCP Apps) was already excluded — so two upstream releases were
  unreachable from here rather than one. **No adaptation was needed**, which the
  three relevant upstream changes explain:
  - **MCP Apps (drf-mcp 0.16.0)** adds `ui://` resources and `_meta.ui` links on
    tool definitions. The bridge reads `name` / `description` / `inputSchema` /
    `outputSchema` / `annotations` off `tools/list` and ignores `_meta`, so the
    addition is inert here. The resource-encoding fix in the same release (non-JSON
    resource bodies no longer come back as quoted JSON string literals) touches
    the resource surface, which this bridge does not use — it calls tools only.
  - **The shared `UrlKwarg` / `QueryParam` (drf-mcp 0.17.0, PAI 0.9.0)** are
    re-exported from `djangorestframework-services` rather than defined locally,
    behind permanently preserved import paths. Neither is imported here. PAI's
    switch from `ValueError` to `ImproperlyConfigured` for a bad channel
    declaration is likewise unreachable: `SpecCapability` is constructed with a
    spec mapping and no channel registrations.
  - **`InputRequired` enforcement (drf-services 0.28)** makes a missing
    marked-required input raise `ServiceValidationError` at dispatch. Over the
    MCP bridge that already arrives as an `isError` result with
    `type == "validation_error"`, which `call_tool` maps to `ModelRetry` — so a
    spec adopting the marker gets a model-correctable failure through this path
    with no change here.

## [0.24.0] — 2026-07-27

### Added

- **Every run is given typed dependencies.** The endpoint now builds an
  `AgentDeps(user=request.user)` per run and passes it to the agent, so tools,
  toolsets and capabilities read request-scoped values off `RunContext.deps` —
  pydantic-ai's own seam — instead of the agent closing over the request:

  ```python
  @tool(registry)
  def whoami(ctx: RunContext[AgentDeps]) -> str:
      """Report the acting user."""
      return str(ctx.deps.user)
  ```

  - **Spec tools bind the user natively.** `SpecToolset`'s default extractor
    reads `ctx.deps.user`; this package used to override it with a closure,
    purely because deps were `None`.
  - **AG-UI `state` now lands somewhere.** `AgentDeps` satisfies pydantic-ai's
    `StateHandler` protocol, so a run's `RunAgentInput.state` is validated into
    `deps.state` rather than dropped with a `UserWarning`. Emitting state back
    is a tool's job — see the new [Shared state](https://artui.github.io/django-ag-ui/shared-state/)
    guide.
  - **It is the precondition for reusing a built agent.** A capability that
    closes over a request can only serve that request, which is why the agent
    (and every tool's JSON schema) is rebuilt on each call today. Nothing about
    that caching changes here; this removes the blocker.
  - A request served without Django's auth middleware has no `user` attribute
    at all — that is an anonymous run (`deps.user is None`), matching what
    `materialize_request_user` already does, not an `AttributeError`.

- **`deps_factory` — supply the run's `AgentDeps` yourself.** A
  `request -> AgentDeps` callable on `AGUIServer` / `DjangoAGUIView` replacing
  the default, which binds only the acting user.
  - **This closes a gap the previous release opened.** Typed deps shipped, but
    the endpoint constructed `AgentDeps(user=request.user)` itself with no hook,
    so a project could not carry its own per-run context — and, specifically,
    **could not have AG-UI's inbound shared state validated**, since pydantic-ai
    validates it against `type(deps.state)` and that requires the deps to arrive
    pre-seeded with a model instance. Now:
    `deps_factory=lambda request: AgentDeps(user=request.user, state=DocumentState())`.
  - Subclass `AgentDeps` to carry anything else per run (a tenant, a
    feature-flag snapshot); whatever the factory returns reaches every tool,
    toolset and capability as `ctx.deps`.

### Changed

- **`django-pydantic-agent` floor raised to `>=0.3,<0.4`** — `AgentDeps` and the
  request-free `build_spec_capability` come from there.
- **`AgentSession(...)` now requires a keyword-only `deps`.** Deliberately
  required rather than defaulted: a forgotten `deps` would mean spec tools
  silently acting as nobody. Only affects code constructing `AgentSession`
  directly; going through `DjangoAGUIView` / `AGUIServer` needs no change.

### Documentation

- **New [Shared state](https://artui.github.io/django-ag-ui/shared-state/) guide.**
  AG-UI's state channel works end to end and needed **no package code** — a tool
  reads `ctx.deps.state` and writes back by returning a `StateSnapshotEvent` as
  `ToolReturn` metadata, which pydantic-ai's adapter streams verbatim. The guide
  is the recipe joining the two ends, plus when to prefer a *tool* instead: a
  tool call is visible in the transcript and can be gated by a confirmation
  card, which state events cannot.
  - Writing it surfaced the missing `deps_factory` seam (above) — without which
    inbound state could not be validated into a model at all. The guide shows
    the validated shape rather than a workaround.

- **CI now checks doc snippets against the *installed* packages** —
  `make docs-check` / `scripts/check_docs_snippets.py`, wired into the docs job.
  Every Python fence in `docs/` and `README.md` must parse, every
  `from X import Y` must resolve, and every keyword argument at a resolvable
  call must exist in the real signature.
  - **It closes the one gap the other gates share**: ruff, `ty` and
    `mkdocs --strict` all stop at this package's boundary, so nothing checked a
    claim about a *dependency's* API — where drift is likeliest, since the
    dependency moves on its own schedule and no test imports the snippet.
  - **A module the reader is meant to own is told apart from one that moved** by
    whether its *root package* is installed, not by a name list and not by
    "the import failed". `myproject.agent` is a placeholder; a dependency
    submodule that has been relocated is a failure — which is the shape of a
    real, boot-breaking defect found in these docs before.
  - Verified against known-bad snippets, not just a clean run: a moved
    dependency module, a bad keyword on one of our types, and a bad keyword on a
    dependency's type are each caught.
  - Limits, stated so nobody over-trusts it: it does not execute snippets or
    check semantics, and **it cannot see non-Python fences** — a JavaScript
    example remains a matter for review.

## [0.23.0] — 2026-07-27

### Added

- **`GET runs/` — the discovery half of resume/fork.** Mounted alongside
  `resume/<run_id>/` and `fork/<run_id>/` whenever a `step_store` is configured,
  the same way `threads/` mounts with a conversation store.

  Both existing endpoints address a run **by id**, so on their own a client can
  only continue a run whose id it still holds — which rules out resuming after a
  page reload or from another device, most of what durable step persistence is
  for. `list_runs()` was already implemented on the store; nothing exposed it.

  Each row carries `run_id`, `thread_id`, `parent_run_id`, `started_at` and
  **`continuable`** — whether the run has a snapshot to seed from, answered by
  making the same `latest_snapshot` call `resume` itself makes rather than
  inferring it from event counts. A run that never reached a provider-valid
  boundary has no snapshot, so resuming it would start from nothing: a client
  should offer the action only where `continuable` is true and treat the rest as
  informational. `parent_run_id` exposes fork lineage so a UI can show a branch
  rather than listing near-identical transcripts.

  Owner-scoped by the store, and nothing on the wire names an owner — another
  user's runs are simply absent, never a `403` that would confirm the id exists.
  Carries the same authentication seam as every other mounted view, and
  authorization runs **before** the store is built, so a denied request never
  reaches the database.

## [0.22.0] — 2026-07-27

### Added

- **`AGUIServer(service_specs=…)` accepts a `SpecRegistry`** as well as a
  `name -> spec` mapping (`djangorestframework-services` 0.27+). A project
  exposing the same specs over this endpoint *and* an MCP server *and* HTTP views
  declares them once in the registry; each transport reads that one source
  instead of repeating the list. A filtered view (`by_tag` / `subset`) is itself
  a registry, so two endpoints can be given different projections with no shared
  state.
  - **Normalised once, at construction**, into a plain dict — which is the whole
    design. Three things downstream consume this value, and a registry reaching
    any of them unresolved fails differently: `build_tool_catalog` calls
    `.items()` (`AttributeError`), and the view's tool-name reservation
    *iterates* it — a registry yields `RegisteredSpec` records rather than name
    strings, which would fill the collision set with dataclasses and **silently**
    stop detecting duplicate tool names between the `@tool` registry, the drf-mcp
    bridge and the spec tools. Resolving at the entry point means nothing
    downstream changes.
  - The mapping is **copied**, so a caller mutating theirs afterwards no longer
    leaks into a configured server.

### Changed

- **Dependency pins advance together**: `django-pydantic-agent` `>=0.2,<0.3`
  (for `resolve_spec_mapping` / `SpecSource`), `[spec-tools]`
  `djangorestframework-pydantic-ai>=0.8,<0.9`, `[drf-mcp]`
  `djangorestframework-mcp-server>=0.15,<0.16`. The last two are not optional
  housekeeping: drf-mcp 0.12 capped drf-services at `<0.26` while PAI 0.8
  requires `>=0.27`, so raising one without the other leaves the two extras
  mutually uninstallable.

### Fixed

- **The persistence docs pointed at a module that no longer exists.** Four pages
  told readers to add `"django_ag_ui.contrib.store"` to `INSTALLED_APPS` and to
  import `DefaultConversationStore` / `DefaultAttachmentStore` /
  `DefaultStepStore` from it. That app moved to
  `django_pydantic_agent.contrib.store` in 0.21.0's extraction and the docs were
  not updated, so following them raised `ModuleNotFoundError` at startup. All
  references repointed.

### Documentation

- **The `service_specs=` reference was three ways stale.** It described the
  argument as "a dotted path" — `import_string` was removed package-wide in
  0.19.0, and the value has been the mapping itself since. Its example built
  `SelectorSpec(serializer=…, queryset=…)`, and neither is a field (nor was the
  required `kind` present), so the snippet raised `TypeError` as written. And it
  credited `SpecCapability` with emitting the spec conventions, which moved onto
  `SpecToolset.get_instructions()` in PAI 0.6.0 — the capability delegates so the
  block reaches the prompt exactly once. All three corrected, plus a section on
  declaring specs once across transports.
- A snippet in the `agent_factory=` section put a bare `...` after a keyword
  argument, which is a `SyntaxError` — so it could not be copied, *and* it made
  the whole fence invisible to doc-checking tooling. That is how the broken
  `SelectorSpec` example above survived in the same file.

## [0.21.0] — 2026-07-23

### Changed

- **The agent-host substrate now lives in
  [`django-pydantic-agent`](https://github.com/Artui/django-pydantic-agent)**, a new
  settings-agnostic package this release depends on. Agent construction, the tool
  registry, toolset/capability composition, audit, the tool guard, user resolution,
  the storage contracts and the reference store models moved there; this package
  keeps the AG-UI transport — the view, the SSE stream, `AGUIServer.urls`, the
  browser-facing sub-views, skills and transcription. It is the lift-down that lets
  a second transport share one substrate.

  **The public surface is unchanged**: every moved symbol is **permanently
  re-exported**, so `from django_ag_ui import ToolRegistry` (and friends) keeps
  working and downstream projects need only a version bump.

### Breaking

- **`INSTALLED_APPS`**: the reference store app moved — replace
  `"django_ag_ui.contrib.store"` with `"django_pydantic_agent.contrib.store"`.
- **The model stores no longer read `DJANGO_AG_UI["ALLOW_ANONYMOUS"]`.** A
  settings-agnostic substrate cannot read a transport's settings key, so pass
  `allow_anonymous=True` to the store constructor instead.
- **`Conversation.messages` holds JSON-serialisable records, not `ag_ui` `Message`
  objects.** The substrate persists transport-owned records verbatim (client
  message ids survive untouched) and the AG-UI wire shape is converted at this
  package's boundary. Code that read `message.content` off a loaded conversation
  now reads `message["content"]`; `messages_to_jsonable` / `messages_from_jsonable`
  live in `django_ag_ui.persistence.utils`.
- The attachment toolset's internal id is now `django-pydantic-agent-attachments`.
  Tool names (`read_attachment`) and the wire are unaffected.

## [0.20.0] — 2026-07-22

### Added

- **Durable step persistence** — a model-backed, owner-scoped store for
  `pydantic-ai-harness`'s `StepPersistence` capability. Pass
  `AGUIServer(step_store=DefaultStepStore)` (the constructor *is* the
  `request -> StepStore` factory — the harness protocol carries no request, so
  the store binds one and is built per run) and every run records an append-only
  event log, a `(run_id, tool_call_id)` tool-effect ledger, and a continuable
  snapshot at each provider-valid boundary, keyed on the AG-UI `run_id`. Four new
  models under `django_ag_ui.contrib.store` (`StoredRun` / `StoredStepEvent` /
  `StoredSnapshot` / `StoredToolEffect`, migration `0003`) back
  `DefaultStepStore`, which structurally satisfies the harness `StepStore`
  protocol. Every row filters by the resolved owner, so a `run_id` from one user
  can't read another's runs; an anonymous request without `ALLOW_ANONYMOUS`
  degrades to no-op rather than aborting the run. Requires the `[harness]` extra.
  A custom backend is any `request -> StepStore` callable. See
  [Durable step persistence](https://artui.github.io/django-ag-ui/step-persistence/).
- **Resume / fork endpoints** — configuring a `step_store` also mounts owner-scoped
  `resume/<run_id>/` and `fork/<run_id>/` endpoints. Both seed a new run from a
  prior run's last continuable snapshot: the server loads it (a `run_id` from
  another owner is a clean `404`), injects it as the run's `message_history`
  (`AgentSession` gained the seam; `run_stream_native` composes it ahead of the
  client's new turn), and records the new run with `parent_run_id` pointing back
  at the source — so the parent is never mutated. `resume` and `fork` are two
  names for one mechanism (the harness's `continue_run` / `fork_run` are
  data-identical). The web-component checkpoint UI rides a downstream release.

## [0.19.0] — 2026-07-17

Configuration is now **per-endpoint**: collaborators are constructor arguments
taking real objects, and `DJANGO_AG_UI` is no longer read on the request path at
all. This makes running more than one AG-UI endpoint in one project actually
work — previously two mounts served, but only their tool registries could
differ. Everything else — toolsets, capabilities, the tool-guard policy, the
drf-mcp bridge, retry budgets, upload caps — was global.

**Breaking**, with a migration. Ten settings that named a class by dotted path
are removed (they raise if left in place, naming the replacement), `AppSettings`
/ `get_settings` and the `resolve_*` helpers are gone from the public API, and
settings now resolve when a server is built rather than per request. Every
remaining setting survives as a **default**, so a single-endpoint project that
configures settings and passes nothing keeps working.

The dotted paths only ever existed because `settings.py` cannot hold a live
object. `urls.py` can — which is also why `drf_mcp_server=internal_mcp` is
expressible at all: with one global path there was no way to say *which* agent
bridges to *which* MCP server. There is now no `import_string` anywhere in the
package.

See [Multiple endpoints](https://artui.github.io/django-ag-ui/configuration/#multiple-endpoints).

### Added

- **`AGUIConfig` + `build_ag_ui_config()`** — an endpoint's scalars, resolved
  **once** in `AGUIServer.__init__` and threaded to the agent view and every
  sub-view. Thirteen `get_settings()` calls across eight files re-read the
  settings on every request; read there they could only ever be global.

  ```python
  AGUIServer(registry, config=build_ag_ui_config(retries=5))
  ```

  Use `build_ag_ui_config(**overrides)` rather than constructing `AGUIConfig`
  directly — it layers your overrides over the project's settings instead of
  discarding them.

- **Collaborators as constructor arguments** on `AGUIServer` / `DjangoAGUIView`:
  `toolsets=`, `capabilities=`, `agent_factory=`, `drf_mcp_server=`,
  `service_specs=`, `provider=` (the stores already were). Passing objects lifts
  a constraint the dotted paths imposed — a collaborator needing constructor
  arguments could not be named by path, so `audit_logger=LoggingAuditLogger(...)`
  and `SimpleJWT`-style configured instances now just work.

- **`ScopedConversationStore`** — partitions a store between endpoints:

  ```python
  AGUIServer(registry, conversation_store=ScopedConversationStore(store, scope="internal"))
  ```

  Stores key threads by `(owner_id, thread_id)`, so two endpoints sharing one
  shared a user's thread list: a conversation started at `/internal/agent`
  appeared in `/public/agent`'s drawer and resumed there under the *public*
  agent's model, tools and guard policy. Prefixing the storage key avoids a
  migration and a `ConversationStore` protocol break (which would hit every
  custom store). Opt-in and explicit — wrapping automatically from the server's
  `namespace` would silently orphan an existing project's history.

  There is deliberately no `ScopedAttachmentStore`: attachments are
  id-referenced with no enumeration and already owner-scoped, so two endpoints
  sharing a store expose nothing across the user boundary. Thread *lists* are
  the case that leaks.

- `ModelConversationStore(allow_anonymous=)` / `ModelAttachmentStore(allow_anonymous=)`.
  `ALLOW_ANONYMOUS` turned out to be a **store** policy — only the model stores
  read it — so it lives there, with `DJANGO_AG_UI["ALLOW_ANONYMOUS"]` as the
  default. A subclass that overrides `__init__` and forgets `super()` fails
  **closed** (refusing anonymous requests) rather than defaulting open.

### Changed

- **`DJANGO_AG_UI` is no longer read on the request path.** Mutating settings no
  longer reconfigures an already-built server. If your tests wrap a request in
  `override_settings(DJANGO_AG_UI=...)` against a server built at URL-conf
  import, the change is now ignored — build the server inside the test with
  `config=build_ag_ui_config(...)` instead.

- `AgentFactoryFn` receives `AGUIConfig` instead of `AppSettings`:
  `(registry: ToolRegistry, config: AGUIConfig) -> Agent`.

- `build_tool_catalog(registry, *, drf_mcp_server=, service_specs=)`,
  `resolve_owner_id(request, *, allow_anonymous=)`, and the `ToolsView` /
  `ThreadsView` / `AttachmentsView` / `TranscribeView` constructors take the
  values they used to read from settings. Only affects code calling these
  directly.

- `provider=` no longer accepts a dotted-path string — pass the `Provider`.

- The `[drf-mcp]` extra now requires `djangorestframework-mcp-server>=0.12,<0.13`
  (was `>=0.9,<0.12`), picking up its per-server identity, session namespacing
  and RFC 8707 audience binding.

### Removed

- **Ten dotted-path settings**: `AGENT_FACTORY`, `TOOLSETS`, `CAPABILITIES`,
  `AUDIT_LOGGER`, `CONVERSATION_STORE`, `ATTACHMENT_STORE`,
  `TRANSCRIPTION_BACKEND`, `DRF_MCP_SERVER`, `SERVICE_SPECS`, `PROVIDER`. Each
  raises `ImproperlyConfigured` naming its replacement if left in settings —
  a silently-ignored `TOOLSETS` would mean an agent quietly losing its tools.

  ```python
  # before — settings.py
  DJANGO_AG_UI = {
      "TOOLSETS": ("myproject.toolsets.weather",),
      "CONVERSATION_STORE": "myproject.stores.MyStore",
  }

  # after — urls.py
  AGUIServer(registry, toolsets=[weather], conversation_store=MyStore())
  ```

- **`AppSettings` and `get_settings`** — a process-global settings snapshot is
  precisely what made two endpoints indistinguishable. `conf.py` keeps one
  primitive, `get_setting(name, default)`. Use `AGUIConfig` /
  `build_ag_ui_config` for an endpoint's resolved scalars.

- **`resolve_audit_logger`, `resolve_conversation_store`,
  `resolve_attachment_store`, `resolve_transcription_backend`,
  `resolve_agent_factory`, `resolve_dotted_instances`** — the whole dotted-path
  resolver layer, which existed only to turn strings back into objects.

## [0.18.1] — 2026-07-16

### Added

- Docs recipe: [Delegating to sub-agents](subagents.md) — wire the
  `pydantic-ai-harness` `SubAgents` capability through the existing
  `CAPABILITIES` seam (the `[harness]` extra) to give the agent a
  `delegate_task` tool over a roster of named child agents. Zero new package
  code — a stateless capability adopted like CodeMode; covered by an
  `importorskip`-guarded recipe test. Per-delegate limits (`usage_limits` /
  `timeout_seconds` / `max_calls` / `on_failure`) are fields on `SubAgent`.

## [0.18.0] — 2026-07-14

### Added

- **`[harness]` extra + a CodeMode batching recipe.** Optional
  `pip install django-ag-ui[harness]` pulls `pydantic-ai-harness` (and its
  `pydantic-monty` sandbox) — lazy-imported, so the core install stays `django` +
  `pydantic-ai-slim`. Its `CodeMode` capability drops into the existing
  `CAPABILITIES` seam to collapse many tools (notably a large drf-mcp bridge) into
  one sandboxed `run_code` tool the model batches in a single round-trip. New
  [CodeMode guide](code-mode.md).

### Changed

- **The drf-mcp bridge now carries each tool's `outputSchema` onto
  `ToolDefinition.return_schema`.** drf-mcp advertises an output schema by default
  (`INCLUDE_OUTPUT_SCHEMA`); the bridge previously dropped it. Propagating it means
  the tool's return type reaches the model — chiefly so CodeMode renders each
  bridged tool as a **typed** Python stub (`-> <Model>`) instead of `-> Any`. A
  service with no output serializer advertises no schema, so its stub stays
  untyped (unchanged). No effect on tool dispatch or results.

### Documentation

- **New "Tool approval (human-in-the-loop)" guide** documenting the end-to-end
  approval flow: enabling `TOOL_GUARD`, what counts as destructive, what the user
  sees (approve / deny / resume), how a custom (non-web-component) client drives
  the interrupt/resume loop, and the `ask_user` typed-question tool. Cross-linked
  from the `TOOL_GUARD` configuration section.

## [0.17.0] — 2026-07-14

### Added

- **Server-side tool-approval interrupt/resume loop (HITL, part 1 — "turn it on").**
  A tool flagged `requires_approval` now finishes the run on a `RUN_FINISHED`
  *interrupt* outcome (carrying the tool call id and an approve/deny/edit response
  schema) instead of executing; a follow-up run carrying `RunAgentInput.resume[]`
  approves (runs the tool), denies, or overrides its arguments. The entire
  lifecycle is upstream (pydantic-ai + the AG-UI adapter) and was already driven by
  `AgentSession` — this release unlocks it:
  - Pins a direct `ag-ui-protocol>=0.1.19,<0.2` dependency (the interrupt/resume
    protocol floor; `pydantic-ai-slim` only floors it at `>=0.1.10`).
  - Puts `DeferredToolRequests` in the agent `output_type`, so approval works for
    **server-side** tools too — the AG-UI adapter only augments `output_type` when a
    run carries *frontend* tools, so a server-only gated tool would otherwise crash
    the run with a `RUN_ERROR`.

  This part turns the loop *on*; the `TOOL_GUARD` policy below is what flags tools.

- **`ToolGuard` — opt-in server-side approval gate (HITL, part 2 — the policy).**
  A new `DJANGO_AG_UI["TOOL_GUARD"]` setting composes a `ToolGuard` capability
  that flips **destructive** server-side tools to require approval (`kind=
  "unapproved"`) at `prepare_tools` time, so they defer to the interrupt loop
  above instead of running mid-stream. Off by default — no surprise gates.
  - Destructiveness is unified from three sources: a registry
    `@tool(destructive=True)`; a drf-mcp tool whose MCP `readOnlyHint` is `False`
    (the bridge now reads the tool `annotations` it previously discarded and
    stamps a `DESTRUCTIVE_METADATA_KEY` onto the tool definition — **no drf-mcp
    release needed**); and per-name `REQUIRE_APPROVAL` / `EXEMPT` overrides
    (`EXEMPT` wins).
  - Only `function` tools are flipped — an `external` (frontend) tool is already
    gated client-side, an `output` tool isn't executed.
  - See [`TOOL_GUARD`](configuration.md#tool_guard) for the settings shape.

### Changed

- **`AuditCapability` now declares its composition order** (`get_ordering()` →
  outermost) instead of relying on list position at the `build_agent` call site.
  This makes capability composition deterministic now that a second capability
  (`ToolGuard`) can join the chain: pydantic-ai's `CombinedCapability`
  topologically sorts by these constraints, so audit stays outermost (wrapping
  every tool execution) regardless of insertion order. No behavioural change with
  a single capability.

## [0.16.0] — 2026-07-13

### Changed

- **`SERVICE_SPECS` now uses `SpecCapability` instead of a bare `SpecToolset`.**
  The exposed tool set is byte-identical, but the spec path
  is now a Pydantic-AI *capability* on `AgentConfig.capabilities`, so it also
  teaches the model `SpecToolset`'s conventions — that list tools accept
  `page` / `limit` / `order`, and the error contract (an `{"error": …}` result is
  a final answer, a retry message means fix the argument, a permission error is
  final) — via instructions appended to the system prompt, closing the gap where
  the model rediscovered them by failing a call. Requires
  `djangorestframework-pydantic-ai>=0.5` (the `[spec-tools]` extra pin moves from
  `>=0.2,<0.4` to `>=0.5,<0.6`). Name-collision precedence, the tool-card catalog,
  and the per-request user binding are unchanged. The internal
  `django_ag_ui.integrations.build_spec_toolset` helper is replaced by
  `build_spec_capability` (not a public export).

## [0.15.0] — 2026-07-10

### Added

- **`AuditCapability`.** The audit boundary re-modelled as a Pydantic-AI
  capability on the `wrap_tool_execute` lifecycle hook, so **every** tool the
  agent runs is audited — registry tools and composed toolsets (drf-mcp / spec /
  attachment) alike, where the old per-tool wrapper saw only registry tools.
  `AuditEvent` gains optional context fields: `ip_address` (filled by the view
  from the driving request via the new `AgentConfig.audit_ip_address`), and
  `organization_id` / `target_type` / `target_id` for custom sinks;
  `LoggingAuditLogger` appends them to the log line when set. Recording is
  **non-raising** — a sink that throws is caught and logged to the
  `django_ag_ui.audit` Python logger, so a broken audit backend degrades to
  lost audit records instead of a broken agent run.
- **`AgentSession`** — one run's orchestration (adapter, stream composition,
  persistence, cancel handling) extracted from `DjangoAGUIView` into a public
  class, so the pipeline is drivable as a plain async iterator (testable apart
  from SSE, reusable under another transport). The view keeps its exact
  behaviour and public API.
- **`MANAGE_SYSTEM_PROMPT` setting** (`"server"` default): who owns the system
  prompt on the AG-UI wire. `"server"` strips a client-posted system message
  before it reaches the model; `"client"` honours it.
- **`ALLOW_UPLOADED_FILES` setting** (`False` default): whether `UploadedFile`
  references in client-submitted messages are honoured — they are fetched with
  the server's credentials, so they stay off unless the client is trusted. The
  attachment flow is unaffected (it travels server-issued refs in message text,
  not AG-UI file parts).

### Changed (breaking)

- **The `pydantic-ai-slim` floor moves to `>=2,<3`** (core and the
  `anthropic` / `openai` / `google` extras): the capability seam
  `AuditCapability` is built on (`pydantic_ai.capabilities`) and the AG-UI
  adapter's server-trust knobs are v2-only. The 1.x line is no longer supported.

### Verified

- Pydantic-AI's `sanitize_messages` hardening runs on the view's hand-composed
  streaming path (client-posted system prompts are stripped by default), the
  reasoning filter's `REASONING_*` event naming holds on the locked 2.x, and
  the attachment flow is unaffected by the `allow_uploaded_files` default —
  each now pinned by session-level tests.

## [0.14.0] — 2026-07-10

### Added

- `DRFMCPToolset(max_retries=...)` — each tool's retry budget: how many times a
  `ModelRetry` (malformed arguments, a service-raised validation error) is fed
  back to the model before the run aborts. Defaults to `1`, matching
  pydantic-ai's own function-tool default.

### Changed (breaking)

- **`DrfMcpToolset` is renamed `DRFMCPToolset`**, matching the capitalized
  acronyms of its sibling classes (`MCPServer`, `AGUIServer`) and PEP 8's
  CapWords convention. The class is built internally by the view from the
  `DRF_MCP_SERVER` setting, so only code importing it directly from
  `django_ag_ui.integrations.drf_mcp` needs the one-line rename; no alias is
  kept.

### Changed

- `DRFMCPToolset` now subclasses `pydantic_ai.toolsets.AbstractToolset`
  directly (the documented extension point) instead of `ExternalToolset`,
  building its tool definitions `kind="function"` from the start. Previously
  it inherited from a base class that models the opposite of in-process
  execution (external tools are *deferred* to the client) and re-stamped every
  tool definition back to `kind="function"` per run — the version-fragile seam
  behind the historically tight `<2` pydantic-ai pin. Public API and tool
  behaviour are otherwise unchanged.

### Fixed

- A bridged tool's `ModelRetry` (malformed arguments, a service-raised
  validation error) now actually reaches the model to self-correct, as
  documented. `ExternalToolset` pinned every tool's retry budget to `0`, so in
  a real agent run the first `ModelRetry` aborted the run with
  `UnexpectedModelBehavior` instead of retrying. Pinned by a full agent-run
  integration test.

## [0.13.0] — 2026-07-09

### Removed

- Remove the inert server-side confirmation machinery: the `needs_confirmation`
  helper (and its `django_ag_ui.policy.auto_confirm` module) and the
  `AUTO_CONFIRM` setting / `AppSettings.auto_confirm` field. These never gated
  anything — server-side tools execute mid-stream, so `@tool(destructive=True)`
  only ever reached the LLM as an `x-destructive` schema hint, never a runtime
  gate. Per-tool `destructive=` / `confirm=` metadata and the `x-destructive` /
  `x-confirm` schema stamps are unchanged (they remain LLM/client hints, and the
  web component still gates *client-registered* tools). A real server-side gate is
  planned separately (a `ToolGuard` + typed `ask_user` mechanism).
  **Breaking:** the `needs_confirmation` export and the `AUTO_CONFIRM` setting are
  gone; a project that set `AUTO_CONFIRM` should drop it (it was a no-op).

## [0.12.1] — 2026-07-08

### Changed

- Widen the `pydantic-ai-slim` dependency constraint from `>=1.0,<2` to
  `>=1.0,<3` (core plus the `anthropic` / `openai` / `google` provider extras),
  so the package installs against Pydantic-AI 2.x. Verified against
  `pydantic-ai-slim` 2.6.0. The 1.x line remains supported.
- Widen the `[spec-tools]` extra's `djangorestframework-pydantic-ai` pin to
  `>=0.2,<0.4` (was `<0.3` — stale; the backing package is at 0.3.x) and the
  `[drf-mcp]` extra's `djangorestframework-mcp-server` pin to `>=0.9,<0.12`
  (was `<0.11`). Together these let a project resolve `pydantic-ai-slim` 2.x
  through the optional bridges without back-tracking to the 1.x line.

### Notes on Pydantic-AI 2.x

`build_model` delegates provider-prefix → model-class resolution to Pydantic-AI
(there is no hand-maintained table), so it inherits these 2.x vocabulary
changes for projects that install `pydantic-ai-slim>=2`:

- The bare `openai:` prefix now builds an `OpenAIResponsesModel` (the Responses
  API) rather than an `OpenAIChatModel`. Use `openai-chat:` for the Chat
  Completions model.
- Bare model names no longer infer a provider — `claude-sonnet-4-5` (no
  `provider:` prefix) previously resolved to Anthropic; it now raises
  `ImproperlyConfigured` pointing at the `PROVIDER` setting. Pass an explicit
  `provider:name` string.
- The `google-gla:` and `google-vertex:` provider prefixes were removed
  upstream; only `google:` remains (our `gemini:` → `google:` alias is
  unaffected).

## [0.12.0] — 2026-07-08

### Added

- **`AGUIServer` — one config object with a namespaced `.urls`.** The
  Django-idiomatic front door for the package: construct it once with the tool
  registry (plus optional stores / skills / auth) and mount its `.urls` the
  `django.contrib.admin` `site.urls` way — `path("agent/", server.urls)`. It
  builds the agent view **and** every sub-view (tools / skills / threads /
  attachments / transcribe) internally from the registry passed **once** (no
  `tools=registry` echo), forwards one `require_authenticated` / `get_user` /
  `authorize` policy to all of them, and mounts each persistence sub-view only
  when its backend is active (a non-`Null` store, resolved from `DJANGO_AG_UI` by
  default or passed explicitly). `.urls` returns the
  `(patterns, app_name, namespace)` triple `path()` mounts directly — the
  `admin.site.urls` idiom, `path("agent/", server.urls)` with no `include()` — so
  endpoints are **namespaced** and reversible — `reverse("ag_ui:endpoint")`,
  `"ag_ui:tools"`, `"ag_ui:threads"`, … — and two mounts no longer collide on flat
  global names. Override the namespace with `namespace=`.

### Removed (breaking)

- **`get_urls` is removed** in favour of `AGUIServer`. It was a free factory that
  returned a bare, un-namespaced pattern list and required pre-building the view
  then re-passing the same registry as `tools=`. Migrate:

  ```python
  # before
  from django_ag_ui import DjangoAGUIView, get_urls

  urlpatterns = [
      *get_urls(DjangoAGUIView(registry), prefix="agent/", tools=registry, threads=store),
  ]

  # after
  from django_ag_ui import AGUIServer

  urlpatterns = [
      path("agent/", AGUIServer(registry, conversation_store=store).urls),
  ]
  ```

  The endpoint URL **names are now namespaced** — update `reverse()` /
  `{% url %}` calls from the old flat names (`django_ag_ui`, `django_ag_ui_tools`,
  `django_ag_ui_threads`, …) to `"<namespace>:endpoint"`, `"<namespace>:tools"`,
  `"<namespace>:threads"`, … (default namespace `"ag_ui"`). The threads /
  attachments / transcribe sub-views now mount automatically when their store is
  configured in settings, so passing them explicitly is only needed to override
  the settings-resolved backend.

## [0.11.1] — 2026-07-03

### Changed

- Adopted the current sibling-package release lines. The `[spec-tools]` extra now
  requires `djangorestframework-pydantic-ai>=0.2,<0.3` — 0.2.0 renamed its import
  to `rest_framework_pydantic_ai`, and `build_spec_toolset` was updated to import
  the new name. The `[drf-mcp]` extra's `djangorestframework-mcp-server` pin was
  widened to `>=0.9,<0.11` to allow the published 0.10.x line.

## [0.11.0] — 2026-07-02

### Added

- **Thread-index cap + `?limit`.** `GET threads/` returns at most
  `DJANGO_AG_UI["THREAD_LIST_LIMIT"]` rows (default 200); the client may request
  fewer via `?limit=N` and a larger value is clamped to the ceiling, so a user
  with thousands of threads no longer fetches and serializes all of them per
  drawer open. `ConversationStore.list` gains a `limit` argument.
- **`ConversationStore.exists()`.** A cheap owner-scoped presence check
  (no message body loaded). The rename endpoint now probes it instead of loading
  and deserializing the whole thread just to 404. Model-backed stores answer with
  a metadata-only `.exists()` query.

### Changed (breaking)

- **`OpenedAttachment.content` is now an open readable stream, not `bytes`.**
  Downloads stream the file via `FileResponse` instead of buffering it
  whole in memory, so a large attachment (with the size cap disabled) no longer
  lands in memory. `AttachmentStore.open` returns the open handle; the download
  view and the `read_attachment` tool consume/close it. A custom
  `AttachmentStore` that built `OpenedAttachment(content=<bytes>)` must now pass a
  readable binary handle.
- **`ConversationStore` gains required `exists()` and a `limit` on `list()`.** A
  custom store implementation must add `exists()` and accept `limit=` on `list()`.

### Fixed

- **Rename / upload title & filename caps.** A `PATCH threads/<id>/`
  title longer than the model's `CharField(max_length=255)` is truncated rather
  than raising a database `DataError` on a strict backend. (Uploaded filenames
  were already truncated to 255 by Django's `UploadedFile`.)
- **Cross-toolset name-collision guard.** The drf-mcp, `SpecToolset`, and
  `read_attachment` toolset builders previously each excluded only the `@tool`
  registry's names, so if `DRF_MCP_SERVER` and `SERVICE_SPECS` exposed the same
  tool name (or either exposed `read_attachment`), pydantic-ai raised a duplicate
  `UserError` mid-run while the catalog looked clean. One `seen` set is now
  threaded through all three builders in `build_tool_catalog`'s precedence order
  (registry → drf-mcp → spec → attachment) so a duplicate can't reach the run.
- **Early upload abort.** A `CappedUploadHandler` aborts an oversized
  upload mid-parse (honoring `ATTACHMENT_MAX_BYTES`) instead of spooling the whole
  body to a temp file before the 413.
- **Transcription client reuse + timeout.** `OpenAITranscriptionBackend`
  caches its `AsyncOpenAI` client on the instance rather than constructing one
  (with a new connection pool) per call, and sets a bounded default `timeout`
  (60 s, overridable) instead of the SDK's 10-minute default.

## [0.10.0] — 2026-07-02

### Added

- **`authorize=` predicate on every view + `get_urls`.** All the mounted views
  (`DjangoAGUIView`, `ToolsView`, `SkillsView`, `ThreadsView`, `AttachmentsView`,
  `TranscribeView`) and `get_urls` now accept an optional
  `authorize(request) -> bool` predicate, run *after* the acting user is
  established. A failing predicate denies with **403** (authenticated but
  forbidden) — as distinct from `require_authenticated`'s **401** — returning
  JSON, not an HTML login redirect. This is the seam a staff-gated mount uses
  (`authorize=lambda r: r.user.is_staff`).
- **`get_urls` forwards the auth seam to every sub-view it builds.**
  `require_authenticated` / `get_user` / `authorize` passed to `get_urls` now
  reach the skills, tools, threads, attachments, and transcribe endpoints, so a
  single call locks the whole mount down (the agent `view` carries its own auth,
  set when you construct `DjangoAGUIView`). Defaults stay open.
- **`DJANGO_AG_UI["ALLOW_ANONYMOUS"]` setting** (default `False`) governing how
  the model-backed stores treat anonymous requests — see below.

### Security

- **Model-backed stores no longer collapse every anonymous visitor into one
  shared owner bucket.** Previously an anonymous request resolved to owner id
  `None` → stored as `""`, so *all* anonymous visitors shared one bucket and
  could list / load / rename / delete / download each other's threads and
  attachments. The stores now resolve the owner via `resolve_owner_id`: an
  authenticated user's pk, or — only when `ALLOW_ANONYMOUS=True` — a per-browser
  `anon:<session_key>` bucket. With `ALLOW_ANONYMOUS` off (the default) an
  anonymous store operation raises `AnonymousOperationError`, which the
  persistence views turn into **403** and the agent endpoint's save path skips
  (the run still streams; it just isn't persisted). Authenticate the endpoints
  (`require_authenticated=True` / `get_user`) whenever a store persists.

### Fixed

- `get_urls`' docstring now documents the `threads/<id>/` **PATCH rename** route
  and the anonymous-scoping caveat.

## [0.9.0] — 2026-06-30

### Added

- **drf-services specs as tools, no MCP hop.** A new `SERVICE_SPECS`
  setting (dotted path to a `name -> ServiceSpec/SelectorSpec` mapping) exposes
  drf-services specs to the agent via `djangorestframework-pydantic-ai`'s
  `SpecToolset` — dispatched in-process through drf-services' transport-neutral
  surface, **without** standing up an MCP server. It drops into the same
  per-request `AgentConfig.toolsets` seam as the drf-mcp bridge: the agent acts
  as the logged-in AG-UI user, each spec's `permission_classes` are enforced, a
  registry `@tool` wins a name collision, and the spec tools' labels surface in
  the `data-tools-url` catalog. Requires the new `[spec-tools]` extra
  (`djangorestframework-pydantic-ai`), imported lazily.

## [0.8.0] — 2026-06-30

### Added

- **Voice input (server).** A new `TranscriptionBackend` Protocol (async,
  owner-scoped `transcribe`) with a `NullTranscriptionBackend` default (voice
  off → `410`), resolved from `DJANGO_AG_UI["TRANSCRIPTION_BACKEND"]` via
  `resolve_transcription_backend`. `get_urls(view, transcribe=backend)` mounts a
  `TranscribeView` at `<prefix>transcribe/` (POST a multipart `audio` clip →
  `{"text": "<transcript>"}`), server-validated by `TRANSCRIPTION_MAX_BYTES` /
  `TRANSCRIPTION_ALLOWED_TYPES` and carrying the same `require_authenticated` /
  `get_user` auth seam as the agent endpoint. The opt-in
  `django_ag_ui.contrib.transcription.openai_transcription_backend.OpenAITranscriptionBackend`
  is a ready reference impl over any OpenAI-compatible `/audio/transcriptions`
  endpoint (lazy `openai` import via the `[openai]` extra; subclass to change the
  model or `base_url`). New exports: `TranscriptionBackend`,
  `NullTranscriptionBackend`, `TranscribeView`, `resolve_transcription_backend`.
- **Model reasoning forwarding.** When a reasoning model is configured to think
  (via `MODEL_SETTINGS`), its chain-of-thought now streams to the client as the
  standard AG-UI reasoning events Pydantic-AI emits — a pure pass-through (no
  protocol extension), and the run transcript ignores the ephemeral events so
  nothing reasoning-related is persisted. The new `FORWARD_REASONING` setting
  (default `True`) gates it: set `False` to let the model reason privately while
  the events are stripped from the stream.

## [0.7.0] — 2026-06-25

### Added

- **File uploads (server side).** A new `AttachmentStore` Protocol
  (async, owner-scoped `save` / `open` / `delete`) is the persistence seam for
  files a user attaches to a conversation, set via
  `DJANGO_AG_UI["ATTACHMENT_STORE"]`. The package ships `NullAttachmentStore`
  (the default — uploads disabled, the endpoint answers `410`) and an abstract
  `ModelAttachmentStore` base (async wrapping + owner scoping over three sync
  ops). Uploads return a lightweight `AttachmentRef` (`id`/`name`/`mime`/`size`),
  never bytes — the AG-UI message stream stays vanilla.
- **`AttachmentsView`**, mounted by `get_urls(view, attachments=<store>)` at
  `<prefix>attachments/` (POST multipart upload → `201` ref) and
  `<prefix>attachments/<id>/` (GET download, DELETE). Uploads are validated
  **server-side** against `ATTACHMENT_MAX_BYTES` (oversize → `413`) and
  `ATTACHMENT_ALLOWED_TYPES` (disallowed → `415`); downloads stream as an
  `attachment` with `X-Content-Type-Options: nosniff`. Every operation is
  owner-scoped — another user's id reads as `404` — with the same
  `require_authenticated` / `get_user` auth seam as `DjangoAGUIView`.
- **`read_attachment` tool.** When an attachment store is configured, the view
  wires a per-request `read_attachment(attachment_id)` tool onto the agent,
  scoped to the acting user, so the model reads a file's contents server-side
  (text inlined; binary summarised) — the bytes never ride the wire. A consumer
  that registers its own `read_attachment` keeps it (registry tools win).
- **New settings:** `ATTACHMENT_STORE` (dotted path, default `None` = off),
  `ATTACHMENT_MAX_BYTES` (default 10 MiB, `0` disables), and
  `ATTACHMENT_ALLOWED_TYPES` (default `()` = any).
- **Reference durable file store (opt-in).** The `django_ag_ui.contrib.store`
  app now also ships a `StoredAttachment` model + migration and
  `DefaultAttachmentStore` (a `ModelAttachmentStore` subclass keeping bytes in
  Django `Storage` — S3/GCS via `STORAGES` — and metadata in a row, owner-scoped
  by an opaque `attachment_id`). The base package still ships no model.

## [0.6.0] — 2026-06-24

### Added

- **Thread index for the chat-history drawer (server side).**
  `ConversationStore` gains an async `list(*, request)` returning owner-scoped
  `ConversationMeta` (`thread_id`, `title`, `updated_at`, `preview`) — **metadata
  only**, no message bodies — so a thread list stays cheap. `NullConversationStore`
  lists nothing; `DjangoSessionConversationStore` enumerates the session's own
  threads (titles/previews derived from messages, `updated_at` stamped on save);
  `ModelConversationStore` adds a `_list(owner_id)` hook that **defaults to `[]`**
  (listing is opt-in, so existing subclasses keep working) and is overridden for
  a cheap column-backed listing.
- **`ThreadsView`**, mounted by `get_urls(view, threads=<store>)` at
  `<prefix>threads/` (GET — the user's threads) and `<prefix>threads/<id>/`
  (GET messages, **PATCH rename**, DELETE). Every operation is owner-scoped —
  another user's thread reads as `404` — and the view carries the same
  `require_authenticated` / `get_user` auth seam as `DjangoAGUIView`.
- **Thread rename.** `ConversationStore` gains `rename(thread_id, title, *, request)`.
  `DjangoSessionConversationStore` persists the title (it overrides the derived
  one in `list`); `ModelConversationStore` adds a `_rename(thread_id, title,
  owner_id)` hook that **defaults to a no-op** (override with a `title` column);
  `NullConversationStore` is a no-op. `PATCH <prefix>threads/<id>/` takes
  `{"title": "..."}` — a blank title is `400`, a missing/cross-owner thread `404`.
- **Reference durable store (opt-in).** A new `django_ag_ui.contrib.store` app
  ships a `StoredConversation` model + migration and `DefaultConversationStore`
  (a `ModelConversationStore` subclass implementing fetch/store/remove/list/rename
  with denormalised `title`/`preview`/`updated_at` columns for a cheap thread
  list). Enable it by adding `"django_ag_ui.contrib.store"` to `INSTALLED_APPS`,
  running `migrate`, and pointing `DJANGO_AG_UI["CONVERSATION_STORE"]` at it.
  The base package still ships no model, so projects that don't opt in get no
  migration.

## [0.5.0] — 2026-06-23

### Changed

- **The drf-mcp bridge now rides drf-mcp's public in-process surface** instead of
  importing handler internals. `DrfMcpToolset` lists tools via
  `MCPServer.list_tools` and executes them via `MCPServer.acall_tool` (drf-mcp's
  transport-complete in-process methods), passing the Django `request` +
  `request.user` so the call context is built inside drf-mcp. Behaviour is
  unchanged — the same merged `inputSchema`, serializer validation, permissions,
  and error mapping as before — but the bridge no longer reaches into
  `handle_tools_list` / `handle_tools_call_async` / `MCPCallContext`. **Requires
  `djangorestframework-mcp-server >= 0.9`** (the pin is now `>=0.9,<0.10`).

## [0.4.0] — 2026-06-12

### Added

- **Cancelling a run is now handled explicitly** (AG-UI has no server-side
  cancel route — the client aborts the streaming request and the server
  observes the disconnect). The view's event stream is wrapped in a
  teardown-aware guard that, on `CancelledError`/`GeneratorExit`:
    - **guarantees provider teardown** — the innermost event generator (whose
      context manager owns the model provider's streaming request) is closed
      explicitly rather than left to garbage-collection order, so no orphaned
      upstream generation keeps billing after the client stopped listening;
    - **persists the partial exchange** — with a non-null
      `CONVERSATION_STORE`, the truncated conversation (client history plus
      the assistant text and completed tool calls streamed so far, dropping
      partially streamed tool calls) is saved with the same thread/owner
      scoping as a completed run;
    - **audits the cancellation** — the configured `AuditLogger` receives a
      run-level `AuditEvent` with `tool_name="agent.run"`, `success=False`,
      and an `error` starting with `"cancelled:"`, riding the existing
      `record()` surface so custom loggers keep working unchanged;
    - **re-raises the cancellation** — persist/audit failures are logged,
      never substituted for the `CancelledError`.

  No new settings and no new endpoint: cancellation stays transport-level,
  and partial persistence follows the store you already configured.

## [0.3.1] — 2026-06-10

### Fixed

- **The `get_user` auth hook now accepts sync or async callables** and runs
  sync hooks off the event loop (`sync_to_async`, thread-sensitive), so the
  headline use case — a sync ORM token → user lookup — works without
  `SynchronousOnlyOperation`. Async hooks, previously called without being
  awaited (a coroutine landed on `request.user` and the auth gate silently
  failed), now work too. A sync hook that returns a coroutine (e.g. a
  `functools.partial` over an async fn) is awaited rather than leaked.
- **`require_authenticated` no longer crashes under ASGI with DB-backed
  sessions**. The lazy `request.user` is materialized in a worker
  thread before the gate, instead of being resolved on the event loop; the
  cached resolution also makes later loop-side readers (the drf-mcp
  bridge's `TokenInfo`, conversation ownership) safe.
- **Bridge errors no longer kill the chat**. The drf-mcp bridge
  previously raised `RuntimeError` for every `JsonRpcError`, which
  pydantic-ai treats as fatal — the most common failure (the model sending
  slightly wrong arguments) emitted `RUN_ERROR` and ended the run. Now:
  malformed-arguments (`-32602`) and service-raised validation raise
  `pydantic_ai.ModelRetry` carrying the field errors so the model
  self-corrects; business-rule failures and missing rows (drf-mcp 0.7's
  `isError` results) are returned as model-readable `{"error": {...}}`
  content; a hard `RuntimeError` is reserved for genuine protocol faults
  (unknown tool, auth, rate limits).
- **Tool-name collisions no longer break the agent**. The catalog
  deduped registry-vs-drf-mcp collisions ("registry wins") but the agent
  registered both, so pydantic-ai raised `UserError` at the first run. The
  drf-mcp toolset now receives the registry's names and skips collisions —
  catalog and agent agree on one rule.
- **The bridge no longer pins a hardcoded MCP protocol version**.
  Synthesised in-process calls advertise drf-mcp's own first supported
  version (`REST_FRAMEWORK_MCP["PROTOCOL_VERSIONS"][0]`), so the bridge
  tracks the server across upgrades.

### Added

- **Auth seam on the catalog views**. `ToolsView` and `SkillsView`
  accept the same `require_authenticated` / `get_user` (sync or async) pair
  as `DjangoAGUIView`, so one policy covers the agent endpoint and the
  catalogs it advertises — previously both answered any anonymous GET, even
  with a locked-down agent endpoint. Defaults stay open for backwards
  compatibility; lock the catalogs down whenever the endpoint is.
- Shared authorize helpers in `django_ag_ui.utils` (`aauthorize` /
  `authorize` / `acall_get_user` / `call_get_user`) — the single policy
  implementation behind all three views.

### Changed

- **Dependency ranges tightened**: `pydantic-ai-slim[ag-ui]` is now
  capped `>=1.0,<2` (the bridge touches semi-internal pydantic-ai surface —
  `ExternalToolset.tool_defs`, tool-def re-stamping), and the `drf-mcp`
  extra now requires `djangorestframework-mcp-server>=0.7,<0.8` — the range
  actually tested, and the floor that returns business failures as
  `isError` results (which the bridge's error mapping consumes).
- **CSRF guidance made prominent**: the view keeps `csrf_exempt=True` by
  default (right for header-token auth), but cookie-authenticated
  deployments should pass `csrf_exempt=False` — tools act as
  `request.user`, so an unprotected cookie-auth endpoint lets a third-party
  page drive the agent as the logged-in user. Documented in the quickstart
  and the view docstring.

## [0.3.0] — 2026-06-03

### Added
- **Tool-metadata catalog.** A read-only `ToolsView` (GET, JSON) returns the
  agent's server-tool catalog; `get_urls(view, tools=registry)` mounts it at
  `<prefix>tools/` (named `django_ag_ui_tools`), passing the same `ToolRegistry`
  the view uses. `build_tool_catalog(registry)` builds the list — each entry is
  `{"name", "summary", "description"?}`. `summary` is always present, resolved
  from a fallback chain: registry `@tool(summary=…)` → a prettified tool name
  (`query_model` → "Query model"); for drf-mcp tools `display_name` → `title` →
  prettified name. `description` is included when available (`ToolSpec.description`,
  or drf-mcp `display_description` → `description`); registry tools win on name
  collisions. **Purpose:** server-side tools execute server-side, so their JSON
  Schema never reaches the browser — the catalog is the channel the web component
  fetches via its `data-tools-url` attribute to label tool-call cards.

### Changed
- The `[drf-mcp]` extra now requires `djangorestframework-mcp-server>=0.6.1`
  (which pulls `djangorestframework-services>=0.15.0`). Additive, no code change:
  it lets the tool catalog read drf-mcp tools' `display_name` / `display_description`
  binding metadata (consumer-only, never on the MCP wire) as the label source.

## [0.2.2] — 2026-06-02

### Fixed
- **drf-mcp tools are now actually executed in-process.** `DrfMcpToolset`
  extended `ExternalToolset`, whose tools are `kind="external"` — Pydantic-AI
  *defers* those: it yields the call to the client and ends the run, never
  invoking the toolset's `call_tool`. So drf-mcp tool calls were handed off and
  silently dropped (no `TOOL_CALL_RESULT`, the model never continued, and an
  AG-UI client's pending indicator would hang). The toolset now advertises its
  tools as `kind="function"`, so the run loop runs them via the per-user
  `MCPCallContext` and streams a real `TOOL_CALL_RESULT`. Regression test drives
  a full agent run, not just a direct `call_tool`.

## [0.2.1] — 2026-06-02

### Fixed
- `API_KEY`-based model construction now works for **every provider Pydantic-AI
  knows** (`openai-responses`, `groq`, `bedrock`, …), not just a hand-maintained
  short list. `build_model` delegates the `provider:name` → Model-class mapping
  to Pydantic-AI's own `infer_model`, injecting the key via a `provider_factory`,
  so `MODEL = "openai-responses:…"` with an `API_KEY` no longer raises. A bare
  model name Pydantic-AI can map to a provider (e.g. `claude-…`) is accepted too.
- The **drf-mcp toolset** now sources each tool's schema from drf-mcp's own
  `tools/list` instead of re-deriving it from the input serializer alone. So the
  agent sees the full advertised `inputSchema` — a selector tool's
  filter / ordering / pagination arguments and the `additionalProperties` policy,
  not just the serializer's fields — matching the HTTP transport exactly.

### Changed
- `DEFAULT_SYSTEM_PROMPT` gained gentle steering for two common failure modes:
  use a listing/search tool's arguments to find things by name and then act on
  the result (don't stop at the lookup), treat "open / go to / show me" as
  navigation, and always finish a turn with a reply or completed action.

## [0.2.0] — 2026-06-02

### Added
- `@tool(confirm="…")` / `ToolSpec.confirm` — an optional human-readable
  confirmation prompt for a destructive tool, stamped into the JSON Schema as
  the `x-confirm` extension (`X_CONFIRM_KEY`) for the frontend to display.
- `DJANGO_AG_UI["API_KEY"]` and `["PROVIDER"]` — supply the model's API key (or
  a full `Provider` instance / dotted path) explicitly instead of inferring it
  from the environment, while keeping the built-in toolset wiring. `MODEL` may
  also be a pre-built `Model` instance.
- Provider extras `django-ag-ui[anthropic]`, `[openai]`, `[google]`.
- **Skills** — `SkillRegistry` + `SkillSpec` (pre-defined prompts) and a
  read-only catalog endpoint mounted by `get_urls(view, skills=registry)` at
  `<prefix>skills/`, serving the JSON the web component consumes.
- `DjangoAGUIView(require_authenticated=True)` fails closed (401) for
  unauthenticated requests, and a `get_user(request)` hook establishes the user
  (assigned to `request.user`) before tools run — closing the "tools run as
  AnonymousUser" footgun. The contract is documented on the view.
- `@tool(summary="…")` / `ToolSpec.summary` → `x-summary` (`X_SUMMARY_KEY`): a
  short display label the frontend shows on the tool-call card.

### Changed
- `DEFAULT_SYSTEM_PROMPT` now steers the model to call destructive tools
  directly and rely on the client's explicit confirmation step, instead of
  asking the user to confirm in prose.
- **Dependency: `pydantic-ai[ag-ui]` → `pydantic-ai-slim[ag-ui]`.** Drops the
  full meta-package's logfire / fastmcp / temporalio / otel footprint. **Action
  required:** install a provider — `pip install django-ag-ui[anthropic]` (or
  `[openai]` / `[google]`, or the provider lib directly) — to use a
  `"provider:model"` `MODEL` string.

### Notes
- The AG-UI endpoint now emits a one-time `RuntimeWarning` when served over WSGI
  (SSE can't stream there); deploy under ASGI (Daphne / Uvicorn).

## [0.1.1] — 2026-06-01

### Fixed
- `build_input_schema` derives parameter types from raw annotations
  (`inspect.signature(eval_str=True)`) instead of `typing.get_type_hints`, so
  the JSON Schema is identical across Python versions — Python ≤ 3.10 no longer
  adds a spurious `nullable: true` to `None`-defaulted parameters.

### Changed
- Expanded README (full badge set + quickstart); the release now publishes a
  coverage badge to `gh-pages` for the README's coverage shield.

## [0.1.0] — 2026-06-01

### Added
- `ToolRegistry` + the `@tool` decorator (`destructive=` / `category=`), with
  JSON-Schema derived from signatures and `x-destructive` / `x-category`
  extensions.
- `DjangoAGUIView`, an async endpoint over Pydantic-AI's `AGUIAdapter` (SSE),
  plus `get_urls()` for mounting.
- `AgentConfig` + `build_agent`, and the `DJANGO_AG_UI` settings (`MODEL`,
  `MODEL_SETTINGS`, `RETRIES`, `AGENT_FACTORY`, `TOOLSETS`, `CAPABILITIES`,
  `AUTO_CONFIRM`, `SYSTEM_PROMPT`).
- `AuditLogger` protocol with `NullAuditLogger` / `LoggingAuditLogger`.
- Opt-in server-side conversation persistence: the `ConversationStore` protocol,
  `NullConversationStore` (default, stateless), `DjangoSessionConversationStore`,
  and the abstract `ModelConversationStore` base.
- In-process `drf-mcp` toolset bridge behind the `[drf-mcp]` extra.

[Unreleased]: https://github.com/Artui/django-ag-ui/compare/v0.51.0...HEAD
[0.51.0]: https://github.com/Artui/django-ag-ui/compare/v0.50.0...v0.51.0
[0.50.0]: https://github.com/Artui/django-ag-ui/compare/v0.49.0...v0.50.0
[0.49.0]: https://github.com/Artui/django-ag-ui/compare/v0.48.0...v0.49.0
[0.48.0]: https://github.com/Artui/django-ag-ui/compare/v0.47.0...v0.48.0
[0.47.0]: https://github.com/Artui/django-ag-ui/compare/v0.46.0...v0.47.0
[0.46.0]: https://github.com/Artui/django-ag-ui/compare/v0.45.0...v0.46.0
[0.45.0]: https://github.com/Artui/django-ag-ui/compare/v0.44.0...v0.45.0
[0.44.0]: https://github.com/Artui/django-ag-ui/compare/v0.43.0...v0.44.0
[0.43.0]: https://github.com/Artui/django-ag-ui/compare/v0.42.0...v0.43.0
[0.42.0]: https://github.com/Artui/django-ag-ui/compare/v0.41.0...v0.42.0
[0.41.0]: https://github.com/Artui/django-ag-ui/compare/v0.40.0...v0.41.0
[0.40.0]: https://github.com/Artui/django-ag-ui/compare/v0.39.0...v0.40.0
[0.39.0]: https://github.com/Artui/django-ag-ui/compare/v0.38.0...v0.39.0
[0.38.0]: https://github.com/Artui/django-ag-ui/compare/v0.37.0...v0.38.0
[0.37.0]: https://github.com/Artui/django-ag-ui/compare/v0.36.0...v0.37.0
[0.36.0]: https://github.com/Artui/django-ag-ui/compare/v0.35.0...v0.36.0
[0.35.0]: https://github.com/Artui/django-ag-ui/compare/v0.34.0...v0.35.0
[0.34.0]: https://github.com/Artui/django-ag-ui/compare/v0.33.1...v0.34.0
[0.33.1]: https://github.com/Artui/django-ag-ui/compare/v0.33.0...v0.33.1
[0.33.0]: https://github.com/Artui/django-ag-ui/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/Artui/django-ag-ui/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/Artui/django-ag-ui/compare/v0.30.0...v0.31.0
[0.30.0]: https://github.com/Artui/django-ag-ui/compare/v0.29.0...v0.30.0
[0.29.0]: https://github.com/Artui/django-ag-ui/compare/v0.28.2...v0.29.0
[0.28.2]: https://github.com/Artui/django-ag-ui/compare/v0.28.1...v0.28.2
[0.28.1]: https://github.com/Artui/django-ag-ui/compare/v0.28.0...v0.28.1
[0.28.0]: https://github.com/Artui/django-ag-ui/compare/v0.27.1...v0.28.0
[0.27.1]: https://github.com/Artui/django-ag-ui/compare/v0.27.0...v0.27.1
[0.27.0]: https://github.com/Artui/django-ag-ui/compare/v0.26.3...v0.27.0
[0.26.3]: https://github.com/Artui/django-ag-ui/compare/v0.26.2...v0.26.3
[0.26.2]: https://github.com/Artui/django-ag-ui/compare/v0.26.1...v0.26.2
[0.26.1]: https://github.com/Artui/django-ag-ui/compare/v0.26.0...v0.26.1
[0.26.0]: https://github.com/Artui/django-ag-ui/compare/v0.25.0...v0.26.0
[0.25.0]: https://github.com/Artui/django-ag-ui/compare/v0.24.0...v0.25.0
[0.24.0]: https://github.com/Artui/django-ag-ui/compare/v0.23.0...v0.24.0
[0.23.0]: https://github.com/Artui/django-ag-ui/compare/v0.22.0...v0.23.0
[0.22.0]: https://github.com/Artui/django-ag-ui/compare/v0.21.0...v0.22.0
[0.21.0]: https://github.com/Artui/django-ag-ui/compare/v0.20.0...v0.21.0
[0.20.0]: https://github.com/Artui/django-ag-ui/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/Artui/django-ag-ui/compare/v0.18.1...v0.19.0
[0.18.1]: https://github.com/Artui/django-ag-ui/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/Artui/django-ag-ui/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/Artui/django-ag-ui/compare/v0.16.0...v0.17.0
[0.16.0]: https://github.com/Artui/django-ag-ui/compare/v0.15.0...v0.16.0
[0.15.0]: https://github.com/Artui/django-ag-ui/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/Artui/django-ag-ui/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/Artui/django-ag-ui/compare/v0.12.1...v0.13.0
[0.12.1]: https://github.com/Artui/django-ag-ui/compare/v0.12.0...v0.12.1
[0.12.0]: https://github.com/Artui/django-ag-ui/compare/v0.11.1...v0.12.0
[0.11.1]: https://github.com/Artui/django-ag-ui/compare/v0.11.0...v0.11.1
[0.11.0]: https://github.com/Artui/django-ag-ui/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/Artui/django-ag-ui/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/Artui/django-ag-ui/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/Artui/django-ag-ui/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/Artui/django-ag-ui/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Artui/django-ag-ui/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Artui/django-ag-ui/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/Artui/django-ag-ui/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Artui/django-ag-ui/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Artui/django-ag-ui/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/Artui/django-ag-ui/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/Artui/django-ag-ui/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/Artui/django-ag-ui/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Artui/django-ag-ui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Artui/django-ag-ui/releases/tag/v0.1.0
