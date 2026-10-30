"""Infrastructure adapters for execute use-case ports."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import HTTPException

from app.core.crypto import verify_args_integrity
from app.modules.approvals.domain.exceptions import RevalidationFailedError
from app.security.redaction import sanitize_exception
from app.tools.registry import get_tool


class PlatformRevalidationAdapter:
    """Wraps legacy revalidate_ticket; maps HTTPException → domain error."""

    def __init__(self, platform: Any) -> None:
        self._platform = platform

    def revalidate(self, ticket: dict[str, Any], *, actor_id: str, tenant_id: str) -> dict[str, Any]:
        from app.tools.tickets import revalidate_ticket

        auth = type("Auth", (), {"user_id": actor_id, "tenant_id": tenant_id})()
        try:
            return revalidate_ticket(self._platform, auth, ticket)
        except HTTPException as exc:
            raise RevalidationFailedError(str(exc.detail)) from exc


class PlatformArgsAdapter:
    def verify_integrity(self, ticket: dict[str, Any]) -> bool:
        return verify_args_integrity(ticket.get("args_encrypted"), ticket.get("args_digest"))

    def load_args(self, ticket: dict[str, Any]) -> dict[str, Any]:
        from app.tools.tickets import _load_args

        return _load_args(ticket)


class PlatformToolExecutor:
    def tool_registered(self, tool_name: str) -> bool:
        return get_tool(tool_name) is not None

    def tool_scope(self, tool_name: str) -> str | None:
        tool = get_tool(tool_name)
        if tool is None:
            return None
        return tool.get("scope")

    def run(self, tool_name: str, args: dict[str, Any], context: dict[str, Any]) -> Any:
        tool = get_tool(tool_name)
        if tool is None:
            raise LookupError("tool missing")
        try:
            return tool["handler"](args, context)
        except Exception as exc:  # noqa: BLE001
            from app.modules.approvals.domain.exceptions import OutcomeUncertainError
            from app.tools.reliability import (
                RetryDecision,
                capability_for,
                decide_after_timeout,
                is_uncertain_transport_error,
            )

            sanitized = sanitize_exception(exc)
            cap = capability_for(tool_name)
            if cap is not None and is_uncertain_transport_error(exc):
                decision = decide_after_timeout(cap)
                if decision in {RetryDecision.RECONCILE_ONLY, RetryDecision.LOOKUP_FIRST}:
                    raise OutcomeUncertainError(
                        f"tool_outcome_unknown: {sanitized};decision={decision.value}"
                    ) from exc
            raise RuntimeError(sanitized) from exc


class ProcessExecutionLock:
    def hold(self, ticket_id: str):
        from app.tools.tickets import _ticket_lock

        @contextmanager
        def _cm() -> Iterator[None]:
            with _ticket_lock(ticket_id):
                yield

        return _cm()


class ReliabilityOutcomePolicy:
    """Infrastructure: capability + resolve_after_uncertain → Application decision."""

    def resolve(
        self,
        *,
        ticket_id: str,
        tenant_id: str,
        tool_name: str,
        idempotency_key: str | None,
        tool_request_id: str | None,
        lookup: Any,
    ) -> tuple[str, str]:
        from app.tools.reliability import (
            ResolveAction,
            ToolExecutionRef,
            capability_for,
            resolve_after_uncertain,
        )

        cap = capability_for(tool_name)
        if cap is None:
            raise LookupError("unknown tool for lookup")
        ref = ToolExecutionRef(
            logical_execution_id=ticket_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            idempotency_key=idempotency_key,
            tool_request_id=tool_request_id,
        )
        action, outcome = resolve_after_uncertain(cap, ref, lookup=lookup.lookup)
        evidence = (outcome.evidence or action.value)[:500]
        if action == ResolveAction.MARK_SUCCEEDED:
            return "succeeded", evidence
        if action == ResolveAction.MARK_FAILED:
            return "failed", evidence
        return "unknown", f"{action.value}:{outcome.kind.value}:{evidence}"[:500]
