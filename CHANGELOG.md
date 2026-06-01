# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/Artui/django-ag-ui/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Artui/django-ag-ui/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Artui/django-ag-ui/releases/tag/v0.1.0
