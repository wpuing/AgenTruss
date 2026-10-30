"""Shared ticket response shaping for HTTP routes."""

from __future__ import annotations

from app.security.redaction import redact_for_api


def public_ticket(ticket: dict) -> dict:
    """Strip encrypted payloads/digests and apply API redaction policy."""
    row = {
        k: v
        for k, v in ticket.items()
        if k not in {"args", "args_encrypted", "args_digest"}
    }
    return redact_for_api(row)
