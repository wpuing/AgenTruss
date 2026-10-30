"""Redis-shaped short memory kept in process for the scaffold."""

from __future__ import annotations

from app.security.redaction import redact_for_api, redact_for_logs, redact_text

MAX_TURNS = 8


def memory_key(tenant_id: str, session_id: str) -> str:
    return f"session:{tenant_id}:{session_id}"


def read_turns(platform, tenant_id: str, session_id: str) -> list[dict]:
    """Return turns for history injection. Never a system message.

    Prefers in-process cache; when empty and postgres is on, hydrate from durable turns
    so multi-turn / post-restart chat can see prior context (Admin already reads PG).
    """
    key = memory_key(tenant_id, session_id)
    row = platform.memories.get(key) or {}
    turns = list(row.get("turns") or [])
    if turns:
        return turns
    try:
        from app.infra.ticket_repo import persistence_enabled

        if not persistence_enabled():
            return []
        from app.infra.entity_repo import list_session_turns

        loaded = list_session_turns(tenant_id, session_id)
        if not loaded:
            return []
        # Keep only non-blocked turns for LLM history (same as append policy).
        usable = [t for t in loaded if not t.get("blocked")][-MAX_TURNS:]
        platform.memories[key] = {
            "tenant_id": tenant_id,
            "user_id": (platform.session_index.get(key) or {}).get("user_id") or "",
            "turns": usable,
        }
        return list(usable)
    except Exception:  # noqa: BLE001
        return []


def read_turns_for_api(platform, tenant_id: str, session_id: str) -> list[dict]:
    """Admin/API view of turns — apply API redaction policy again at the edge."""
    turns = read_turns(platform, tenant_id, session_id)
    safe = redact_for_api(turns)
    return safe if isinstance(safe, list) else []


def append_turn(
    platform,
    tenant_id: str,
    user_id: str,
    session_id: str,
    user_text: str,
    assistant_text: str,
    blocked: bool,
) -> None:
    """Append one turn. Stored text is log-policy redacted (secrets never retained)."""
    key = memory_key(tenant_id, session_id)
    row = platform.memories.setdefault(key, {"tenant_id": tenant_id, "user_id": user_id, "turns": []})
    if not blocked:
        row["turns"].append(
            {
                "user": str(redact_for_logs(user_text))[:500],
                "assistant": str(redact_for_logs(assistant_text))[:500],
                "blocked": False,
            }
        )
        row["turns"] = row["turns"][-MAX_TURNS:]
    platform.session_index[key] = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "user_id": user_id,
        "turn_count": len(row["turns"]),
    }
    try:
        from app.infra.entity_repo import append_session_turn, upsert_session_index

        upsert_session_index(session_id, tenant_id, user_id, len(row["turns"]))
        append_session_turn(session_id, tenant_id, str(redact_for_logs(user_text))[:500], str(redact_for_logs(assistant_text))[:500], blocked)
    except Exception:  # noqa: BLE001
        from app.core.config import get_settings
        from app.infra.ticket_repo import persistence_enabled

        if get_settings().is_production or persistence_enabled():
            raise


def history_messages(turns: list[dict]) -> list[dict]:
    """Build non-system history messages; scrub credentials again before LLM use."""
    messages = []
    for turn in turns:
        user = redact_text(str(turn.get("user") or "")).value
        assistant = redact_text(str(turn.get("assistant") or "")).value
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": assistant})
    return messages
