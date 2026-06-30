# Repo conventions for `django-ag-ui`

This file is the single source of truth for how to write code in this package.
Rules are non-negotiable unless flagged as a heuristic.

## What this package is

A Django ↔ Pydantic-AI ↔ [AG-UI](https://docs.ag-ui.com) integration. Provides:
- A tool registry (`ToolRegistry`, `@tool` decorator) with `destructive=`, `category=`,
  `confirm=`, and `summary=` metadata, and a `build_input_schema` helper that emits
  `x-destructive` / `x-category` / `x-confirm` / `x-summary` JSON-Schema extensions when set.
- An async Django view (`DjangoAGUIView`) that wraps Pydantic-AI's
  `pydantic_ai.ui.ag_ui.AGUIAdapter` and returns a `StreamingHttpResponse` of AG-UI events.
- An `AuditLogger` Protocol with `NullAuditLogger` and `LoggingAuditLogger` implementations.
- `conf.py` reading the `DJANGO_AG_UI` settings dict.

Downstream packages (e.g. `django-admin-agent`) build on this. **No admin specifics live in
this package.**

The AG-UI stack design doc (`django-ag-ui-plan.md`) lives in the private ecosystem planning workspace, outside this repo.

## Commands

| Target | What it does |
| --- | --- |
| `make init` | `uv sync --all-groups` + install pre-commit hooks |
| `make test` | pytest with 100% line+branch coverage gate |
| `make lint` | `ruff check .` + `ty check django_ag_ui` |
| `make format` | `ruff format .` |
| `make docs-serve` | live-reload mkdocs at `localhost:8000` |
| `make docs-build` | `mkdocs build --strict` |
| `make release-bump VERSION=X.Y.Z` | rewrite `version.py` + promote `[Unreleased]` in CHANGELOG |
| `make release-publish` | end-to-end workstation release |

## Structural rules

1. **One exported class or function per file.** File name = `snake_case` of the symbol.
   `ToolRegistry` → `tool_registry.py`; `build_input_schema` → `build_input_schema.py`.
   **Exception:** `django_ag_ui/constants.py` is the package's single home for enums and
   constant-like module-level values, and is the only file allowed to export multiple symbols.
2. **Private helpers used in only one file** stay there with a leading `_`.
3. **Non-exported helpers shared across files** go into a sibling `utils.py`. Classes are
   allowed in `utils.py` if they are internal infrastructure.
4. **Top-level imports only.** No function-local / lazy imports unless a circular import is
   genuine and documented inline at the import site, **or** the dependency is optional —
   those imports go inside the function body with a clear `ImportError` message.
5. **Full type annotations on every function and method signature.** `Any` is allowed only at
   Django/Pydantic-AI boundaries where the type genuinely is `Any`.
6. **`__init__.py` is the only re-export point.** Each `__init__.py` lists the public surface
   in `__all__`. Internal modules import from leaf paths, never from the package's `__init__`.
7. **Always `from __future__ import annotations`** at the top of any file with type
   annotations. Python 3.10+, so no PEP 695 `type` statements.
8. **Absolute imports only.** Imports are ordered stdlib → third-party → first-party
   (`django_ag_ui`). Within each block, alphabetical.
9. **NEVER use relative imports.** `from . import x`, `from .foo import bar`, any dotted-
   relative form is forbidden everywhere in the package, including `__init__.py`. Always
   write the full absolute path (`from django_ag_ui.foo import bar`).
10. **Types and functionality live in separate sub-packages.** When a directory contains both
    type declarations (dataclasses, Protocols, frozen wire-shape records) and functionality
    (callables, registries, dispatch helpers), the types move into a `types/` sibling.
    `constants.py` remains the multi-export exception.

## API style rules

11. **Always dataclasses over `dict[str, Any]` for structured data.** Every wire-shape
    payload, response envelope, configuration record, and tool spec field is a frozen
    `@dataclass` with explicit field types. `dict[str, Any]` survives only at genuine
    serialisation boundaries.
12. **Tool callables are typed.** Every registered tool declares typed parameters and a typed
    return — no `**kwargs: Any` escape hatches. The registry uses signatures to derive JSON
    Schema for AG-UI's tool definitions; an untyped tool breaks the schema.

## Security boundary

Per-tool `destructive: bool` metadata is the surfaced risk signal; the registry stamps it into
the JSON Schema as `x-destructive: true`. **What that flag gates depends on where the tool runs:**

- **Client-registered tools** (the web component's `registerTool`, executed in the browser) are
  gated — `@artooi/ag-ui-web-component` shows an inline confirmation card before dispatching to
  the local handler.
- **Server-side tools** (this package's `@tool` registry, and drf-mcp-bridged tools) are **not**
  gated. They run **server-side, mid-stream**, so by the time the browser sees `TOOL_CALL_END`
  the tool already executed; the `x-destructive` / `x-confirm` stamps reach only the **LLM** (as
  schema hints), never a browser gate. `needs_confirmation` and the `AUTO_CONFIRM` setting are
  currently inert for server tools — do **not** rely on `@tool(destructive=True)` to gate a
  dangerous server-side operation. A real server-side gate is the open **GATE-1** decision (see
  the ecosystem roadmap). The wire stays vanilla AG-UI either way.

The `AuditLogger` Protocol is the audit boundary. `LoggingAuditLogger` is the default;
projects supply their own (Sentry, Honeycomb, custom) by setting `DJANGO_AG_UI["AUDIT_LOGGER"]`
to a dotted path.

## No module-level or class-level mutable state

State lives on instances. Module-level constants (lookup tables, regexes, frozen settings
defaults, dispatch tables) are fine — module-level **mutable** state is not.

- No module-level mutable singletons (registries, caches, "warned-once" flags).
- No class-level mutable attributes declared on the class body. Initialise mutables in
  `__init__`.
- The `ToolRegistry` is an instance: a `DjangoAGUIView` holds one; tests build a fresh
  registry per scenario.

## Tests

- `make test` runs pytest with `--cov=django_ag_ui --cov-fail-under=100` (line + branch).
  Restructure rather than reach for `# pragma: no cover`.
- Test layout mirrors the source tree under `tests/`. `django_ag_ui/foo/bar.py` →
  `tests/foo/test_bar.py`.
- `tests/conftest_settings.py` is the Django settings module pytest uses (set via
  `DJANGO_SETTINGS_MODULE` in `pyproject.toml`).
- Async tests: `async def test_...` with pytest-asyncio (`asyncio_mode = "auto"`).
- For the async view, use `httpx.AsyncClient` against the ASGI app to drive `RunAgentInput`
  POSTs and parse the SSE event stream.

## Lint and types

- `make lint` runs `ruff check .` + `ty check django_ag_ui`. CI fails on either.
- `ruff format` is the source of truth for layout.
- Pre-commit runs `make lint-fix`, `make format`, `make type-check`. Commits must be clean
  before push — never `--no-verify`.
- `ty` is scoped to `django_ag_ui/` only (not tests).

## Boundaries

- The package depends on `pydantic-ai-slim[ag-ui]` for the AGUIAdapter. The AG-UI wire types
  come from there; don't re-implement them. The slim package ships no model-provider library —
  those come via provider extras (`anthropic` / `openai` / `google` → `pydantic-ai-slim[<provider>]`).
- No admin specifics. Anything that touches `django.contrib.admin` belongs in
  `django-admin-agent`, not here.
- The `agent/` layer does not import from `registry/types`; it imports the public re-exports
  from `django_ag_ui.registry`.

## Compatibility floor

| Component | Floor | Tested |
| --- | --- | --- |
| Python | 3.10 | 3.10, 3.11, 3.12, 3.13, 3.14 |
| Django | 4.2 LTS | 4.2, 5.0, 5.1, 5.2, 6.0 |
| Pydantic-AI | 1.0 (with `pydantic-ai-slim[ag-ui]` extra) | latest in matrix |

## Branching

When working on a new feature or version bump, **ALWAYS** switch to a new branch first
(`git checkout -b feat/...` or `release/vX.Y.Z`) and push to that branch. Never commit
feature work or version bumps directly to `main`, and never push to `main` from the local
checkout — `main` only advances via merged PRs (or, for releases, the tagged commit produced
on the release branch).

## Releases

Merge-to-main triggered. `.github/workflows/release.yml` runs on every push to `main` and
calls `make release-publish-prepare`. The script in `scripts/release-publish.sh` is the
single source of truth:

1. Extract version from `django_ag_ui/version.py`.
2. Short-circuit if `vX.Y.Z` already exists locally or on origin.
3. Run `uv run pytest` as a final gate.
4. `uv build` into `dist/`.
5. Extract the `## [X.Y.Z]` section from `CHANGELOG.md` into release notes.
6. Emit `released=true`.

If released:
- Publish to PyPI via OIDC trusted publishing.
- Tag, push, create GitHub Release.
- `mkdocs gh-deploy` to `gh-pages`.

### Cutting a release

```bash
make release-bump VERSION=0.2.0
git diff
git commit -am "Release 0.2.0"
git push -u origin release/0.2.0
gh pr create
# Merge to main; release.yml fires on the merge commit.
```

### One-time setup (manual)

1. **PyPI Trusted Publisher** — `Artui/django-ag-ui`, workflow `release.yml`, environment `pypi`.
2. **GitHub Environment** — create `pypi` (no secrets; OIDC).
3. **GitHub Pages** — branch `gh-pages` (created on first release with docs).
