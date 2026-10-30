"""Optional result-lookup adapters keyed by tool name.

Empty by default — Demo tools do not register. Real adapters must only query
external status and never call the tool execute handler.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.tools.reliability import ToolExecutionRef, ToolOutcome

LookupFn = Callable[[ToolExecutionRef], ToolOutcome]

_REGISTRY: dict[str, LookupFn] = {}


def register_lookup(tool_name: str, fn: LookupFn) -> None:
    """Register a lookup callable for ``tool_name`` (tests / real adapters)."""
    _REGISTRY[tool_name] = fn


def unregister_lookup(tool_name: str) -> None:
    _REGISTRY.pop(tool_name, None)


def clear_lookups() -> None:
    _REGISTRY.clear()


def get_lookup(tool_name: str) -> LookupFn | None:
    return _REGISTRY.get(tool_name)


def lookup_for(tool_name: str, ref: ToolExecutionRef) -> ToolOutcome | None:
    """Run registered lookup or return None when no adapter is wired."""
    fn = get_lookup(tool_name)
    if fn is None:
        return None
    return fn(ref)


class RegistryResultLookup:
    """``ResultLookupPort`` backed by ``lookup_registry``."""

    def lookup(self, ref: Any) -> ToolOutcome:
        from app.tools.reliability import ToolOutcomeKind, capability_for, lookup_not_implemented

        tool_name = getattr(ref, "tool_name", "") or ""
        fn = get_lookup(tool_name)
        if fn is not None:
            return fn(ref)
        cap = capability_for(tool_name)
        if cap is None:
            return ToolOutcome(
                kind=ToolOutcomeKind.UNKNOWN,
                evidence="tool_capability_unknown",
            )
        return lookup_not_implemented(cap, ref)
