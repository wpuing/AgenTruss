"""Unit tests for upsert write-mode classification (no DB required)."""

from __future__ import annotations

from app.infra.ticket_repo import _upsert_write_mode


def test_hard_terminal_always_noop():
    for terminal in ("succeeded", "failed", "rejected", "cancelled", "expired"):
        assert _upsert_write_mode(terminal, "executing") == "noop"
        assert _upsert_write_mode(terminal, "pending") == "noop"
        assert _upsert_write_mode(terminal, "succeeded") == "noop"
        assert _upsert_write_mode(terminal, "failed") == "noop"


def test_executing_completion_and_meta():
    assert _upsert_write_mode("executing", "succeeded") == "complete"
    assert _upsert_write_mode("executing", "failed") == "complete"
    assert _upsert_write_mode("executing", "reconciliation_required") == "complete"
    assert _upsert_write_mode("executing", "executing") == "busy_meta"
    assert _upsert_write_mode("executing", "revalidated") == "noop"


def test_reconcile_modes():
    assert _upsert_write_mode("reconciliation_required", "succeeded") == "reconcile"
    assert _upsert_write_mode("reconciliation_required", "failed") == "reconcile"
    assert _upsert_write_mode("reconciliation_required", "executing") == "noop"
    assert _upsert_write_mode("reconciliation_required", "pending") == "noop"


def test_cas_bypass_refused():
    assert _upsert_write_mode("approved", "executing") == "noop"
    assert _upsert_write_mode("revalidated", "executing") == "noop"
    assert _upsert_write_mode("pending", "approved") == "full"
