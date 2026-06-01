from __future__ import annotations

import functools
import inspect
import json
import time
from collections.abc import Callable
from typing import Any, cast

from pydantic_ai import Agent

from django_ag_ui.agent.types.agent_config import AgentConfig
from django_ag_ui.policy.audit.null_audit_logger import NullAuditLogger
from django_ag_ui.policy.audit.types.audit_event import AuditEvent
from django_ag_ui.policy.audit.types.audit_logger import AuditLogger
from django_ag_ui.registry.tool_registry import ToolRegistry
from django_ag_ui.registry.types.tool_spec import ToolSpec


def build_agent(registry: ToolRegistry, config: AgentConfig) -> Agent[None, Any]:
    """Build a Pydantic-AI ``Agent`` from a registry and an :class:`AgentConfig`.

    Each registry tool is registered as a plain Pydantic-AI tool. When
    ``config.audit_logger`` is set, every tool call is timed and recorded; the
    wrapper preserves the original signature so Pydantic-AI's schema generation
    is unaffected. Frontend tools declared in the AG-UI ``RunAgentInput`` are
    merged automatically by the adapter and are not registered here.

    ``model_settings`` / ``retries`` tune the model; ``toolsets`` and
    ``capabilities`` compose external Pydantic-AI toolsets/capabilities (e.g. an
    MCP-client toolset) alongside the registry tools, so the agent can reach
    beyond the registered set.
    """
    logger = config.audit_logger if config.audit_logger is not None else NullAuditLogger()
    tools = [_audited(binding.spec, logger) for binding in registry]
    return Agent(
        model=config.model,
        tools=tools,
        instructions=config.instructions,
        # ``model_settings`` is a plain dict at the settings boundary; Agent
        # types it as the ``ModelSettings`` TypedDict.
        model_settings=cast("Any", config.model_settings),
        retries=config.retries,
        toolsets=list(config.toolsets) if config.toolsets is not None else None,
        capabilities=list(config.capabilities) if config.capabilities is not None else None,
    )


def _audited(spec: ToolSpec, logger: AuditLogger) -> Callable[..., Any]:
    fn = spec.fn
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
            except Exception as error:
                _record_failure(logger, spec.name, kwargs, started, error)
                raise
            _record_success(logger, spec.name, kwargs, started, result)
            return result

        return async_wrapper

    @functools.wraps(fn)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as error:
            _record_failure(logger, spec.name, kwargs, started, error)
            raise
        _record_success(logger, spec.name, kwargs, started, result)
        return result

    return sync_wrapper


def _args_repr(kwargs: dict[str, Any]) -> str:
    return json.dumps(kwargs, default=str, sort_keys=True)


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _record_success(
    logger: AuditLogger,
    name: str,
    kwargs: dict[str, Any],
    started: float,
    result: Any,
) -> None:
    logger.record(
        AuditEvent(
            tool_name=name,
            arguments_repr=_args_repr(kwargs),
            duration_ms=_elapsed_ms(started),
            success=True,
            result_size=len(str(result)),
        ),
    )


def _record_failure(
    logger: AuditLogger,
    name: str,
    kwargs: dict[str, Any],
    started: float,
    error: Exception,
) -> None:
    logger.record(
        AuditEvent(
            tool_name=name,
            arguments_repr=_args_repr(kwargs),
            duration_ms=_elapsed_ms(started),
            success=False,
            error=f"{type(error).__name__}: {error}",
        ),
    )


__all__ = ["build_agent"]
