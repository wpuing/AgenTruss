"""Restart recovery scan for paused approval / workflow work.

Does not claim exactly-once. Interrupted ``executing`` tickets are marked
``reconciliation_required`` (outcome uncertain) — never auto-retried and never
treated as a confirmed tool failure.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.platform import Platform
from app.infra.ticket_repo import list_paused_for_recovery, load_all_into, persistence_enabled
from app.modules.approvals.domain.enums import HARD_TERMINAL_STATUSES, TicketStatus

logger = logging.getLogger(__name__)

_HARD = {s.value for s in HARD_TERMINAL_STATUSES}


def recover_on_startup(platform: Platform) -> dict[str, Any]:
    """Hydrate tickets and inventory paused work. Does not auto-execute tools.

    Only runs at process start (see ``app.main`` lifespan). All leftover
    ``executing`` rows are treated as uncertain — there is no in-process worker
    that could still be legally executing after restart.
    """
    report: dict[str, Any] = {
        "loaded_tickets": 0,
        "paused": [],
        "interrupted_executing": [],
        "reconciliation_required": [],
        "auto_executed": 0,
        "note": "",
    }
    if not persistence_enabled():
        report["note"] = "memory mode: no durable recovery"
        return report
    from app.core.config import get_settings
    from app.infra.entity_repo import (
        load_agents_into,
        load_kb_meta_into,
        load_strategies_into,
        load_users_into,
        seed_strategies_if_empty,
        upsert_agent,
        upsert_user,
    )

    settings = get_settings()
    # Users: DB authoritative when rows exist; otherwise persist demo seed once.
    seeded = dict(platform.users)
    platform.users.clear()
    loaded_users = load_users_into(platform)
    report["loaded_users"] = loaded_users
    if loaded_users == 0 and settings.allow_demo_users and seeded:
        platform.users.update(seeded)
        for user in platform.users.values():
            try:
                upsert_user(user)
            except Exception as exc:  # noqa: BLE001
                logger.error("failed to persist seed user: %s", exc)
    # Strategies: PG authority; seed stable ids when empty.
    try:
        loaded_strat = load_strategies_into(platform)
        report["loaded_strategies"] = loaded_strat
        if loaded_strat == 0:
            report["seeded_strategies"] = seed_strategies_if_empty(platform)
        else:
            # Keep default pointers on stable seed names when present.
            if "rs-loose" in platform.retrieve_strategies:
                platform.default_retrieve_id = "rs-loose"
            if "rs-strict" in platform.retrieve_strategies:
                platform.strict_retrieve_id = "rs-strict"
            if "cs-baseline" in platform.chunk_strategies:
                platform.default_chunk_id = "cs-baseline"
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy hydrate skipped: %s", exc)
        report["loaded_strategies"] = 0
    try:
        from app.admin.channel_config import hydrate_channel_overrides
        from app.admin.component_config import hydrate_component_overrides
        from app.infra.entity_repo import list_settings_kv

        # Single scan: component hydrate skips channel.*; channel hydrate takes only channel.*.
        kv_rows = list_settings_kv()
        report["loaded_settings_kv"] = hydrate_component_overrides(platform, kv_rows)
        report["loaded_channel_kv"] = hydrate_channel_overrides(platform, kv_rows)
    except Exception as exc:  # noqa: BLE001
        logger.warning("settings/channel kv hydrate skipped: %s", exc)
        report["loaded_settings_kv"] = 0
        report["loaded_channel_kv"] = 0
    report["loaded_kb_meta"] = load_kb_meta_into(platform)
    # Seed strategy ids are process-local; remap KB defaults onto current seed ids.
    remapped = 0
    for kb in platform.kbs.values():
        changed = False
        if kb.get("default_retrieve_strategy_id") not in platform.retrieve_strategies:
            kb["default_retrieve_strategy_id"] = platform.default_retrieve_id
            changed = True
        if kb.get("default_chunk_strategy_id") not in platform.chunk_strategies:
            kb["default_chunk_strategy_id"] = platform.default_chunk_id
            changed = True
        if changed:
            remapped += 1
            try:
                from app.infra.entity_repo import upsert_kb

                upsert_kb(kb)
            except Exception as exc:  # noqa: BLE001
                logger.warning("kb strategy remap persist failed kb=%s err=%s", kb.get("id"), exc)
    report["remapped_kb_strategies"] = remapped
    # Agents: merge durable kb_binding/status onto stable seed ids; seed when empty.
    seeded_agents = dict(platform.agents)
    loaded_agents = load_agents_into(platform)
    report["loaded_agents"] = loaded_agents
    if loaded_agents == 0:
        for agent in seeded_agents.values():
            platform.agents[agent["agent_id"]] = agent
            try:
                upsert_agent(agent)
            except Exception as exc:  # noqa: BLE001
                logger.error("failed to persist seed agent: %s", exc)
    else:
        for agent in seeded_agents.values():
            if agent["agent_id"] not in platform.agents:
                platform.agents[agent["agent_id"]] = agent
                try:
                    upsert_agent(agent)
                except Exception as exc:  # noqa: BLE001
                    logger.error("failed to persist missing seed agent: %s", exc)
    # Mock vector store is process-local — rebuild from durable segments when needed.
    try:
        from app.rag.service import rehydrate_mock_vectors

        report["rehydrated_vectors"] = rehydrate_mock_vectors(platform)
    except Exception as exc:  # noqa: BLE001
        logger.warning("mock vector rehydrate skipped: %s", exc)
        report["rehydrated_vectors"] = 0
    report["loaded_tickets"] = load_all_into(platform)

    interrupted: list[str] = []
    already_reconciling: list[str] = []
    for ticket in list(platform.tickets.values()):
        status = ticket.get("status")
        if status in _HARD:
            continue
        if status == TicketStatus.RECONCILIATION_REQUIRED.value:
            already_reconciling.append(ticket["id"])
            continue
        if status != TicketStatus.EXECUTING.value:
            continue
        # Crash while executing: side-effect may or may not have occurred.
        # Never auto-execute. LOOKUP_FIRST may close the ticket when a registry
        # adapter returns a definitive external outcome; otherwise reconcile.
        from app.tools.lookup_registry import get_lookup
        from app.tools.reliability import (
            ResolveAction,
            RetryDecision,
            ToolExecutionRef,
            capability_for,
            decide_after_uncertain,
            resolve_after_uncertain,
        )

        tool_name = str(ticket.get("tool_name") or "")
        cap = capability_for(tool_name)
        decision = (
            decide_after_uncertain(cap) if cap is not None else RetryDecision.RECONCILE_ONLY
        )
        ticket["executed"] = False
        resolved = False
        if decision == RetryDecision.LOOKUP_FIRST and cap is not None:
            lookup_fn = get_lookup(tool_name)
            ref = ToolExecutionRef(
                logical_execution_id=str(ticket["id"]),
                tenant_id=str(ticket.get("tenant_id") or ""),
                tool_name=tool_name,
                idempotency_key=ticket.get("idempotency_key"),
                tool_request_id=ticket.get("tool_request_id"),
            )
            try:
                action, outcome = resolve_after_uncertain(cap, ref, lookup=lookup_fn)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "recovery lookup failed ticket=%s err=%s",
                    ticket.get("id"),
                    exc,
                )
                action, outcome = ResolveAction.RECONCILE, None
            if action == ResolveAction.MARK_SUCCEEDED:
                ticket["status"] = TicketStatus.SUCCEEDED.value
                ticket["executed"] = True
                ticket["reason"] = "recovery_lookup_succeeded"
                ticket["execution_result"] = (outcome.evidence if outcome else "lookup_ok")[
                    :500
                ]
                resolved = True
            elif action == ResolveAction.MARK_FAILED:
                ticket["status"] = TicketStatus.FAILED.value
                ticket["reason"] = "recovery_lookup_failed_status"
                ticket["execution_result"] = (
                    outcome.evidence if outcome else "lookup_failed"
                )[:500]
                resolved = True
            else:
                logger.info(
                    "recovery lookup inconclusive ticket=%s action=%s",
                    ticket.get("id"),
                    action.value,
                )
        if not resolved:
            ticket["status"] = TicketStatus.RECONCILIATION_REQUIRED.value
            ticket["reason"] = "outcome_uncertain_after_restart"
            ticket["execution_result"] = (
                f"side_effect_unknown_manual_reconcile_required;decision={decision.value}"
            )
        try:
            from app.infra.ticket_repo import upsert_ticket

            upsert_ticket(ticket)
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to persist interrupted ticket %s: %s", ticket.get("id"), exc)
        interrupted.append(ticket["id"])
        logger.warning(
            "ticket recovery id=%s status=%s tenant=%s tool=%s",
            ticket.get("id"),
            ticket.get("status"),
            ticket.get("tenant_id"),
            ticket.get("tool_name"),
        )

    report["interrupted_executing"] = interrupted
    report["reconciliation_required"] = already_reconciling + interrupted
    paused = list_paused_for_recovery()
    report["paused"] = paused
    report["note"] = (
        "paused tickets reloaded; pending await approval; "
        "approved await explicit execute (no silent side effects on boot); "
        f"executing-at-crash → reconciliation_required={len(interrupted)} "
        f"(already={len(already_reconciling)}); do not auto-retry tools"
    )
    logger.info(
        "recovery scan: loaded=%s paused=%s interrupted=%s reconciling=%s",
        report["loaded_tickets"],
        len(paused),
        len(interrupted),
        len(report["reconciliation_required"]),
    )
    return report
