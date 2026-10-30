"""Real PostgreSQL multi-session CAS (not SQLite).

Default: skipped. Do NOT treat skip as pass.

Run (isolated test DB only — never production)::

  set RUN_PG_INTEGRATION=1
  set DATABASE_URL=postgresql+psycopg://USER:PASS@127.0.0.1:5432/agenttruss_test
  set STORAGE_BACKEND=postgres
  pytest tests/test_approvals_pg_cas.py -q --tb=short

Uses production ``claim_ticket_executing`` (conditional UPDATE + rowcount).
Threads use independent SQLAlchemy sessions via ``session_scope``.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest
from app.infra.db import create_all, dispose_db, init_db, session_scope
from app.infra.models import TicketRow
from app.infra.ticket_repo import claim_ticket_executing
from app.modules.approvals.domain.enums import CLAIMABLE_STATUSES

_RUN = os.getenv("RUN_PG_INTEGRATION") == "1"
_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not _RUN,
    reason="set RUN_PG_INTEGRATION=1 and postgresql DATABASE_URL (test DB only)",
)


@pytest.fixture
def pg_cas(monkeypatch):
    if not _URL.startswith("postgresql"):
        pytest.fail(
            "DATABASE_URL must start with postgresql when RUN_PG_INTEGRATION=1 "
            f"(got {(_URL[:32] + '…') if len(_URL) > 32 else (_URL or '<empty>')!r})"
        )
    monkeypatch.setenv("STORAGE_BACKEND", "postgres")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DATABASE_URL", _URL)
    # Settings() reads env each call (no lru_cache); rebuild engine only.
    dispose_db()
    init_db()
    create_all()
    yield
    with session_scope() as session:
        session.query(TicketRow).filter(TicketRow.id.like("pg_cas_%")).delete(
            synchronize_session=False
        )
    dispose_db()


def _insert(ticket_id: str, status: str, *, idem: str) -> None:
    with session_scope() as session:
        session.add(
            TicketRow(
                id=ticket_id,
                tenant_id="demo",
                status=status,
                applicant_id="u1",
                tool_name="demo_refund",
                args_masked_json="{}",
                idempotency_key=idem,
            )
        )


def test_pg_claimable_aligned_with_domain(pg_cas):
    assert {s.value for s in CLAIMABLE_STATUSES} == {"approved", "revalidated"}


def test_pg_claim_only_one_winner_multi_session(pg_cas):
    """Eight concurrent claims → exactly one True; DB status executing."""
    _insert("pg_cas_1", "approved", idem="pg_cas_idem_1")
    results: list[bool] = []

    def attempt() -> None:
        results.append(claim_ticket_executing("pg_cas_1", "demo"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: attempt(), range(8)))

    assert sum(1 for ok in results if ok) == 1
    assert sum(1 for ok in results if not ok) == 7
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_1")
        assert row is not None
        assert row.status == "executing"


def test_pg_claim_rejects_non_claimable_status(pg_cas):
    _insert("pg_cas_2", "pending", idem="pg_cas_idem_2")
    assert claim_ticket_executing("pg_cas_2", "demo") is False
    with session_scope() as session:
        assert session.get(TicketRow, "pg_cas_2").status == "pending"


def test_pg_second_claim_after_status_change_fails(pg_cas):
    _insert("pg_cas_3", "approved", idem="pg_cas_idem_3")
    assert claim_ticket_executing("pg_cas_3", "demo") is True
    assert claim_ticket_executing("pg_cas_3", "demo") is False
    with session_scope() as session:
        assert session.get(TicketRow, "pg_cas_3").status == "executing"


def test_pg_cas_loser_does_not_invoke_executor_hook(pg_cas):
    """Application-level contract: lost claim must not call tool runner.

    Uses real PG claim; executor is a MagicMock never invoked on False claim.
    """
    from app.modules.approvals.application.dto import ActorContext, ExecuteCommand
    from app.modules.approvals.application.services import ApprovalApplicationService
    _insert("pg_cas_4", "revalidated", idem="pg_cas_idem_4")
    # Seed already-claimed ticket (control); race uses pg_cas_5 below.
    assert claim_ticket_executing("pg_cas_4", "demo") is True

    class PgBackedStore:
        def get(self, tenant_id: str, ticket_id: str):
            with session_scope() as session:
                row = session.get(TicketRow, ticket_id)
                assert row and row.tenant_id == tenant_id
                return {
                    "id": row.id,
                    "tenant_id": row.tenant_id,
                    "status": row.status,
                    "applicant_id": row.applicant_id,
                    "tool_name": row.tool_name,
                    "executed": bool(row.executed),
                    "idempotency_key": row.idempotency_key,
                    "args_encrypted": "x",
                    "args_digest": "y",
                }

        def save(self, ticket: dict) -> None:
            with session_scope() as session:
                row = session.get(TicketRow, ticket["id"])
                if row:
                    new_status = ticket["status"]
                    # Mirror production: never undo executing/succeeded/failed with stale claimable.
                    if row.status in {"executing", "succeeded", "failed"} and new_status in {
                        "approved",
                        "revalidated",
                        "pending",
                    }:
                        pass
                    else:
                        row.status = new_status
                    row.executed = bool(ticket.get("executed"))
                    row.execution_result = ticket.get("execution_result")
                    row.reason = ticket.get("reason")

        def persist_claimable_status(self, ticket: dict) -> None:
            self.save(ticket)

        def claim_executing(self, ticket_id: str, tenant_id: str) -> bool:
            return claim_ticket_executing(ticket_id, tenant_id)

        def find_succeeded_idempotent(self, **kwargs):
            return None

    class PassRevalidate:
        def revalidate(self, ticket, *, actor_id, tenant_id):
            return ticket

    class PassArgs:
        def verify_integrity(self, ticket):
            return True

        def load_args(self, ticket):
            return {}

    executor = MagicMock()
    executor.tool_registered.return_value = True
    executor.tool_scope.return_value = None

    # Ticket already executing — gate blocks before claim; also test claim-lose path
    # by resetting to revalidated without releasing... use fresh ticket pg_cas_5.
    _insert("pg_cas_5", "revalidated", idem="pg_cas_idem_5")
    # Pre-claim once so service loses
    assert claim_ticket_executing("pg_cas_5", "demo") is True
    # Reset status in DB to revalidated while another worker "holds" execution —
    # simulate race: status still claimable in app read but second CAS loses...
    # Stronger: two parallel service.execute after shared revalidated row.
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_5")
        row.status = "revalidated"

    results: list[BaseException | None] = []

    def run_once() -> None:
        svc = ApprovalApplicationService(
            PgBackedStore(),
            revalidation=PassRevalidate(),
            args_port=PassArgs(),
            executor=executor,
        )
        try:
            svc.execute(
                ExecuteCommand(
                    ticket_id="pg_cas_5",
                    actor=ActorContext(
                        user_id="admin",
                        tenant_id="demo",
                        perms=frozenset({"ticket:risk:approve"}),
                    ),
                )
            )
            results.append(None)
        except BaseException as exc:  # noqa: BLE001
            results.append(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: run_once(), range(4)))

    # Exactly one tool side-effect; losers conflict or later hit idempotent succeeded.
    assert executor.run.call_count == 1
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_5")
        assert final is not None
        assert final.status in {"succeeded", "executing"}


def test_stale_upsert_cannot_regress_status_or_execution_fields(pg_cas):
    """After CAS claim + winner fields, a stale claimable snapshot must not undo them.

    Deterministic (no sleep): claim first, then upsert loser snapshot.
    Relies on SELECT FOR UPDATE + no-op on stale claimable writes.
    """
    from app.infra.ticket_repo import upsert_ticket

    _insert("pg_cas_stale_1", "revalidated", idem="pg_cas_stale_idem_1")
    assert claim_ticket_executing("pg_cas_stale_1", "demo") is True
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_stale_1")
        assert row is not None
        row.executed = True
        row.execution_result = "winner_ok"
        row.reason = "claimed"
        row.args_digest = "digest_winner"
        row.args_encrypted = "enc_winner"
        row.tool_name = "demo_refund"

    upsert_ticket(
        {
            "id": "pg_cas_stale_1",
            "tenant_id": "demo",
            "type": "risk",
            "status": "revalidated",
            "applicant_id": "attacker",
            "tool_name": "evil_tool",
            "args_masked": {"x": 1},
            "args_encrypted": "enc_stale",
            "args_digest": "digest_stale",
            "idempotency_key": "pg_cas_stale_idem_hijack",
            "executed": False,
            "execution_result": None,
            "reason": "stale_loser",
        }
    )
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_stale_1")
        assert final is not None
        assert final.status == "executing"
        assert final.executed is True
        assert final.execution_result == "winner_ok"
        assert final.reason == "claimed"
        assert final.args_digest == "digest_winner"
        assert final.args_encrypted == "enc_winner"
        assert final.tool_name == "demo_refund"
        assert final.applicant_id == "u1"
        assert final.idempotency_key == "pg_cas_stale_idem_1"


def test_winner_progression_freezes_integrity_fields(pg_cas):
    """executing → succeeded may update result, but must not rewrite digest/tool/tenant."""
    from app.infra.ticket_repo import upsert_ticket

    _insert("pg_cas_stale_2", "revalidated", idem="pg_cas_stale_idem_2")
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_stale_2")
        row.args_digest = "digest_claimed"
        row.args_encrypted = "enc_claimed"
        row.tool_name = "demo_refund"
    assert claim_ticket_executing("pg_cas_stale_2", "demo") is True

    upsert_ticket(
        {
            "id": "pg_cas_stale_2",
            "tenant_id": "demo",
            "type": "risk",
            "status": "succeeded",
            "applicant_id": "should_not_apply",
            "tool_name": "hijacked_tool",
            "args_encrypted": "enc_hijack",
            "args_digest": "digest_hijack",
            "args_masked": {},
            "idempotency_key": "pg_cas_stale_idem_2_hijack",
            "executed": True,
            "execution_result": "ok",
            "reason": "done",
            "decided_at": "2026-10-26T00:00:00+00:00",
        }
    )
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_stale_2")
        assert final.status == "succeeded"
        assert final.executed is True
        assert final.execution_result == "ok"
        assert final.args_digest == "digest_claimed"
        assert final.args_encrypted == "enc_claimed"
        assert final.tool_name == "demo_refund"
        assert final.tenant_id == "demo"
        assert final.idempotency_key == "pg_cas_stale_idem_2"
        assert final.applicant_id == "u1"


def test_upsert_rejects_tenant_reassignment(pg_cas):
    """Existing ticket id must not be moved to another tenant via upsert."""
    from app.infra.ticket_repo import upsert_ticket
    from app.modules.approvals.domain.exceptions import TicketConflictError

    _insert("pg_cas_tenant_1", "pending", idem="pg_cas_tenant_idem_1")
    with pytest.raises(TicketConflictError, match="tenant conflict"):
        upsert_ticket(
            {
                "id": "pg_cas_tenant_1",
                "tenant_id": "other_tenant",
                "type": "risk",
                "status": "pending",
                "applicant_id": "u2",
                "tool_name": "demo_refund",
                "args_masked": {},
                "idempotency_key": "pg_cas_tenant_idem_1",
            }
        )
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_tenant_1")
        assert row is not None
        assert row.tenant_id == "demo"
        assert row.status == "pending"


def test_terminal_succeeded_not_regressed_by_stale_executing(pg_cas):
    """succeeded must not fall back to executing via generic upsert."""
    from app.infra.ticket_repo import upsert_ticket

    _insert("pg_cas_term_1", "revalidated", idem="pg_cas_term_idem_1")
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_term_1")
        row.args_digest = "d1"
        row.args_encrypted = "e1"
    assert claim_ticket_executing("pg_cas_term_1", "demo") is True
    upsert_ticket(
        {
            "id": "pg_cas_term_1",
            "tenant_id": "demo",
            "type": "risk",
            "status": "succeeded",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "args_digest": "digest_should_freeze",
            "args_encrypted": "enc_should_freeze",
            "idempotency_key": "pg_cas_term_idem_1",
            "executed": True,
            "execution_result": "winner_ok",
            "reason": "done",
        }
    )
    upsert_ticket(
        {
            "id": "pg_cas_term_1",
            "tenant_id": "demo",
            "type": "risk",
            "status": "executing",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_term_idem_1",
            "executed": False,
            "execution_result": None,
            "reason": "stale_executing",
        }
    )
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_term_1")
        assert final.status == "succeeded"
        assert final.executed is True
        assert final.execution_result == "winner_ok"
        assert final.args_digest == "d1"


def test_terminal_outcomes_cannot_overwrite_each_other(pg_cas):
    """succeeded must not become failed (or vice versa) via stale upsert."""
    from app.infra.ticket_repo import upsert_ticket

    _insert("pg_cas_term_2", "revalidated", idem="pg_cas_term_idem_2")
    assert claim_ticket_executing("pg_cas_term_2", "demo") is True
    upsert_ticket(
        {
            "id": "pg_cas_term_2",
            "tenant_id": "demo",
            "status": "succeeded",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_term_idem_2",
            "executed": True,
            "execution_result": "ok",
        }
    )
    upsert_ticket(
        {
            "id": "pg_cas_term_2",
            "tenant_id": "demo",
            "status": "failed",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_term_idem_2",
            "executed": False,
            "execution_result": "stale_fail",
        }
    )
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_term_2")
        assert final.status == "succeeded"
        assert final.execution_result == "ok"

    _insert("pg_cas_term_3", "revalidated", idem="pg_cas_term_idem_3")
    assert claim_ticket_executing("pg_cas_term_3", "demo") is True
    upsert_ticket(
        {
            "id": "pg_cas_term_3",
            "tenant_id": "demo",
            "status": "failed",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_term_idem_3",
            "executed": False,
            "execution_result": "real_fail",
        }
    )
    upsert_ticket(
        {
            "id": "pg_cas_term_3",
            "tenant_id": "demo",
            "status": "succeeded",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_term_idem_3",
            "executed": True,
            "execution_result": "stale_ok",
        }
    )
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_term_3")
        assert final.status == "failed"
        assert final.execution_result == "real_fail"


def test_upsert_cannot_bypass_cas_into_executing(pg_cas):
    """approved → executing must not happen through upsert (CAS only)."""
    from app.infra.ticket_repo import upsert_ticket

    _insert("pg_cas_term_4", "approved", idem="pg_cas_term_idem_4")
    upsert_ticket(
        {
            "id": "pg_cas_term_4",
            "tenant_id": "demo",
            "status": "executing",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_term_idem_4",
        }
    )
    with session_scope() as session:
        assert session.get(TicketRow, "pg_cas_term_4").status == "approved"


def test_executing_to_reconciliation_then_reconcile_succeeded(pg_cas):
    """Crash mark → reconciliation_required; operator reconcile → succeeded; no regress."""
    from app.infra.ticket_repo import upsert_ticket

    _insert("pg_cas_recon_1", "revalidated", idem="pg_cas_recon_idem_1")
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_recon_1")
        row.args_digest = "d_recon"
    assert claim_ticket_executing("pg_cas_recon_1", "demo") is True
    upsert_ticket(
        {
            "id": "pg_cas_recon_1",
            "tenant_id": "demo",
            "status": "reconciliation_required",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_recon_idem_1",
            "executed": False,
            "reason": "outcome_uncertain_after_restart",
            "execution_result": "side_effect_unknown_manual_reconcile_required",
        }
    )
    with session_scope() as session:
        mid = session.get(TicketRow, "pg_cas_recon_1")
        assert mid.status == "reconciliation_required"
        assert mid.args_digest == "d_recon"

    upsert_ticket(
        {
            "id": "pg_cas_recon_1",
            "tenant_id": "demo",
            "status": "succeeded",
            "applicant_id": "hijack",
            "tool_name": "evil",
            "args_masked": {},
            "args_digest": "evil_digest",
            "idempotency_key": "pg_cas_recon_idem_1",
            "executed": True,
            "execution_result": "reconciled_ok",
            "reason": "reconcile_confirmed_success",
        }
    )
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_recon_1")
        assert final.status == "succeeded"
        assert final.executed is True
        assert final.execution_result == "reconciled_ok"
        assert final.args_digest == "d_recon"
        assert final.tool_name == "demo_refund"

    # Hard terminal: cannot regress
    upsert_ticket(
        {
            "id": "pg_cas_recon_1",
            "tenant_id": "demo",
            "status": "reconciliation_required",
            "applicant_id": "u1",
            "tool_name": "demo_refund",
            "args_masked": {},
            "idempotency_key": "pg_cas_recon_idem_1",
        }
    )
    with session_scope() as session:
        assert session.get(TicketRow, "pg_cas_recon_1").status == "succeeded"


def test_parallel_stale_upserts_cannot_undo_cas(pg_cas):
    """Many concurrent stale upserts after CAS must leave executing + winner fields intact."""
    from concurrent.futures import ThreadPoolExecutor

    from app.infra.ticket_repo import upsert_ticket

    _insert("pg_cas_lock_1", "revalidated", idem="pg_cas_lock_idem_1")
    assert claim_ticket_executing("pg_cas_lock_1", "demo") is True
    with session_scope() as session:
        row = session.get(TicketRow, "pg_cas_lock_1")
        row.executed = True
        row.execution_result = "winner_ok"
        row.reason = "claimed"

    stale = {
        "id": "pg_cas_lock_1",
        "tenant_id": "demo",
        "type": "risk",
        "status": "revalidated",
        "applicant_id": "u1",
        "tool_name": "demo_refund",
        "args_masked": {},
        "idempotency_key": "pg_cas_lock_idem_1",
        "executed": False,
        "execution_result": None,
        "reason": "stale_loser",
    }

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: upsert_ticket(dict(stale)), range(24)))

    assert claim_ticket_executing("pg_cas_lock_1", "demo") is False
    with session_scope() as session:
        final = session.get(TicketRow, "pg_cas_lock_1")
        assert final.status == "executing"
        assert final.executed is True
        assert final.execution_result == "winner_ok"
        assert final.reason == "claimed"


def test_upsert_lock_hook_blocks_claim_until_commit(pg_cas):
    """FOR UPDATE held by upsert blocks claim until commit (event barriers, no sleep)."""
    import threading

    from app.infra import ticket_repo as tr
    from app.infra.db import get_engine
    from sqlalchemy import text

    _insert("pg_cas_lock_2", "revalidated", idem="pg_cas_lock_idem_2")
    locked = threading.Event()
    release = threading.Event()
    claim_outcome: list[str | bool] = []

    def after_lock(_session, _row) -> None:
        locked.set()
        assert release.wait(timeout=5)

    def claimer() -> None:
        assert locked.wait(timeout=5)
        # Prove row is locked: NOWAIT must fail while upsert holds FOR UPDATE.
        eng = get_engine()
        assert eng is not None
        with eng.connect() as conn:
            conn.execute(text("SET lock_timeout = '0'"))
            try:
                conn.execute(
                    text("SELECT id FROM risk_tickets WHERE id = 'pg_cas_lock_2' FOR UPDATE NOWAIT")
                )
                claim_outcome.append("nowait_unexpectedly_acquired")
            except Exception:  # noqa: BLE001
                claim_outcome.append("nowait_blocked")
        release.set()
        # After upsert commits, CAS succeeds once.
        claim_outcome.append(claim_ticket_executing("pg_cas_lock_2", "demo"))
        claim_outcome.append(claim_ticket_executing("pg_cas_lock_2", "demo"))

    tr._UPSERT_AFTER_LOCK_HOOK = after_lock
    t = threading.Thread(target=claimer)
    t.start()
    try:
        tr.upsert_ticket(
            {
                "id": "pg_cas_lock_2",
                "tenant_id": "demo",
                "type": "risk",
                "status": "revalidated",
                "applicant_id": "u1",
                "tool_name": "demo_refund",
                "args_masked": {},
                "idempotency_key": "pg_cas_lock_idem_2",
                "executed": False,
            }
        )
        t.join(timeout=8)
        assert not t.is_alive()
    finally:
        tr._UPSERT_AFTER_LOCK_HOOK = None
        release.set()

    assert claim_outcome[0] == "nowait_blocked"
    assert claim_outcome[1:] == [True, False]
    with session_scope() as session:
        assert session.get(TicketRow, "pg_cas_lock_2").status == "executing"
