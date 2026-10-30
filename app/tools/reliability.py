"""Minimal tool reliability contract for execute / recovery decisions.

Does not claim external exactly-once. Demo tools declare no external idempotency
or result lookup — uncertain outcomes stay on the reconciliation path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.tools.registry import get_tool


class ToolOutcomeKind(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class RetryDecision(StrEnum):
    SAFE_RETRY = "safe_retry"
    LOOKUP_FIRST = "lookup_first"
    RECONCILE_ONLY = "reconcile_only"
    NO_RETRY = "no_retry"


@dataclass(frozen=True)
class ToolCapability:
    """Declared ability of a registered tool (adapter metadata).

    ``capability_tier`` is honesty labeling (local_demo / https_demo / …).
    Recovery authority for LOOKUP_FIRST is ``supports_result_lookup`` (and
    idempotency), never tier alone — Demo must not escalate via renaming tier.
    """

    name: str
    has_side_effects: bool
    supports_idempotency_key: bool
    supports_result_lookup: bool
    high_risk: bool
    capability_tier: str = "local_demo"


@dataclass(frozen=True)
class ToolExecutionRef:
    """Stable identifiers for one logical execution attempt."""

    logical_execution_id: str
    tenant_id: str
    tool_name: str
    idempotency_key: str | None = None
    tool_request_id: str | None = None


@dataclass(frozen=True)
class ToolOutcome:
    kind: ToolOutcomeKind
    evidence: str
    raw_result: Any | None = None


def capability_for(tool_name: str) -> ToolCapability | None:
    tool = get_tool(tool_name)
    if tool is None:
        return None
    high = bool(tool.get("high_risk"))
    from app.tools.registry import normalize_capability_tier

    try:
        tier = normalize_capability_tier(tool.get("capability_tier"))
    except ValueError:
        tier = "local_demo"
    return ToolCapability(
        name=tool_name,
        has_side_effects=bool(tool.get("has_side_effects", high)),
        supports_idempotency_key=bool(tool.get("supports_idempotency_key", False)),
        supports_result_lookup=bool(tool.get("supports_result_lookup", False)),
        high_risk=high,
        capability_tier=tier,
    )


def same_logical_idempotency_key(
    *,
    tenant_id: str,
    tool_name: str,
    clean_args: dict,
    previous_key: str,
) -> bool:
    """True when a retry of the same logical call must reuse ``previous_key``."""
    from app.tools.registry import _idempotency_key

    return _idempotency_key(tenant_id, tool_name, clean_args) == previous_key


def decide_after_uncertain(cap: ToolCapability) -> RetryDecision:
    """What recovery may do when DB says executing / reconciliation_required."""
    if not cap.has_side_effects:
        return RetryDecision.SAFE_RETRY
    if cap.supports_result_lookup:
        return RetryDecision.LOOKUP_FIRST
    if cap.supports_idempotency_key:
        # External idempotent submit may be safe after lookup failure — still prefer lookup.
        return RetryDecision.LOOKUP_FIRST
    return RetryDecision.RECONCILE_ONLY


def decide_after_timeout(cap: ToolCapability) -> RetryDecision:
    """Timeout ≠ failed. Prefer lookup when available; else reconcile for side effects."""
    if not cap.has_side_effects:
        return RetryDecision.SAFE_RETRY
    if cap.supports_result_lookup or cap.supports_idempotency_key:
        return RetryDecision.LOOKUP_FIRST
    return RetryDecision.RECONCILE_ONLY


def is_uncertain_transport_error(exc: BaseException) -> bool:
    """True when the exception indicates timeout/connectivity, not a confirmed tool failure."""
    if isinstance(exc, (TimeoutError, ConnectionError, BrokenPipeError, OSError)):
        return True
    text = str(exc).lower()
    return any(
        token in text
        for token in ("timeout", "timed out", "connection reset", "temporarily unavailable")
    )


def lookup_not_implemented(cap: ToolCapability, ref: ToolExecutionRef) -> ToolOutcome:
    """Placeholder until a real adapter implements result lookup."""
    del ref
    if not cap.supports_result_lookup:
        return ToolOutcome(
            kind=ToolOutcomeKind.UNKNOWN,
            evidence="tool_does_not_support_result_lookup",
        )
    return ToolOutcome(
        kind=ToolOutcomeKind.UNKNOWN,
        evidence="result_lookup_adapter_not_wired",
    )


class ResolveAction(StrEnum):
    """Next step after an uncertain execute — never silently re-runs without protocol."""

    MARK_SUCCEEDED = "mark_succeeded"
    MARK_FAILED = "mark_failed"
    LOOKUP_THEN_DECIDE = "lookup_then_decide"
    RESUBMIT_WITH_SAME_KEY = "resubmit_with_same_key"
    SAFE_RETRY_READ = "safe_retry_read"
    RECONCILE = "reconcile"


def resolve_after_uncertain(
    cap: ToolCapability,
    ref: ToolExecutionRef,
    *,
    lookup: Any | None = None,
) -> tuple[ResolveAction, ToolOutcome]:
    """Decide recovery action using optional lookup — never calls tool execute.

    ``lookup`` if provided must be ``Callable[[ToolExecutionRef], ToolOutcome]``.
    """
    decision = decide_after_uncertain(cap)
    if decision == RetryDecision.SAFE_RETRY:
        return (
            ResolveAction.SAFE_RETRY_READ,
            ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="safe_retry_read_path"),
        )
    if decision == RetryDecision.RECONCILE_ONLY:
        return (
            ResolveAction.RECONCILE,
            ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="no_safe_auto_retry"),
        )
    # LOOKUP_FIRST
    if lookup is None:
        outcome = lookup_not_implemented(cap, ref)
    else:
        outcome = lookup(ref)
    if outcome.kind == ToolOutcomeKind.SUCCEEDED:
        return ResolveAction.MARK_SUCCEEDED, outcome
    if outcome.kind == ToolOutcomeKind.FAILED:
        return ResolveAction.MARK_FAILED, outcome
    if cap.supports_idempotency_key and cap.supports_result_lookup:
        # Lookup unknown but external idempotent submit may be protocol-allowed later.
        return ResolveAction.RECONCILE, outcome
    if cap.supports_idempotency_key and not cap.supports_result_lookup:
        return ResolveAction.RESUBMIT_WITH_SAME_KEY, outcome
    return ResolveAction.RECONCILE, outcome
