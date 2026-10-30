"""JSONPlaceholder HTTPS demo Adapter (not a production business system).

Capability tier (do not confuse with Echo / durable / production):

- Echo protocol sandbox → ``http_echo_adapter`` (local durable store + HTTP).
- **This module** → ``JSONPlaceholder HTTPS integration``: real outbound HTTPS
  to a public *demo* REST API; POSTs are not a reliable durable business ledger;
  GET may succeed for synthetic ids without proving prior side effects persisted.
- Durable external result lookup → requires a real system with stable operation
  ids and authoritative status queries (not provided here).
- Production adapter → security, tenancy, and recovery acceptance in a real env.

Gate: ``RUN_JSONPLACEHOLDER_TOOL=1``. No API key. Never claim exactly-once.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any
from urllib.parse import urlparse

import httpx

from app.tools.reliability import ToolExecutionRef, ToolOutcome, ToolOutcomeKind

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://jsonplaceholder.typicode.com"
_ALLOWED_HOSTS = frozenset({"jsonplaceholder.typicode.com"})


def enabled() -> bool:
    return os.getenv("RUN_JSONPLACEHOLDER_TOOL") == "1"


def _base() -> str:
    raw = (os.getenv("JSONPLACEHOLDER_BASE_URL") or DEFAULT_BASE).rstrip("/")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in _ALLOWED_HOSTS:
        raise RuntimeError(
            f"JSONPLACEHOLDER_BASE_URL host not allowed: {host or raw!r}; "
            f"allowed={sorted(_ALLOWED_HOSTS)}"
        )
    return raw


def _timeout_s() -> float:
    try:
        return float(os.getenv("JSONPLACEHOLDER_TIMEOUT_S") or "10")
    except ValueError:
        return 10.0


def create_post(*, title: str, body: str, user_id: int = 1) -> dict[str, Any]:
    """POST /posts — real HTTPS call to third-party host."""
    url = f"{_base()}/posts"
    try:
        with httpx.Client(timeout=_timeout_s()) as client:
            resp = client.post(
                url,
                json={"title": title, "body": body, "userId": user_id},
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"jsonplaceholder_transport:{exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"jsonplaceholder_http_{exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise TimeoutError(f"jsonplaceholder_transport:{exc}") from exc
    if not isinstance(data, dict) or data.get("id") is None:
        raise RuntimeError("jsonplaceholder_malformed_create")
    return data


def lookup_post(post_id: str | int) -> ToolOutcome:
    """GET /posts/{id}. 404 → UNKNOWN (do not infer never-executed)."""
    url = f"{_base()}/posts/{post_id}"
    try:
        with httpx.Client(timeout=_timeout_s()) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"jsonplaceholder_lookup_transport:{exc}") from exc
    except httpx.HTTPError as exc:
        raise TimeoutError(f"jsonplaceholder_lookup_transport:{exc}") from exc

    if resp.status_code == 404:
        return ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="jp_not_found")
    if resp.status_code >= 400:
        return ToolOutcome(
            kind=ToolOutcomeKind.UNKNOWN,
            evidence=f"jp_http_{resp.status_code}",
        )
    try:
        data = resp.json()
    except json.JSONDecodeError:
        return ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="jp_malformed")
    if not isinstance(data, dict) or str(data.get("id")) != str(post_id):
        # Fake API may return empty {} for some ids — keep UNKNOWN.
        if isinstance(data, dict) and not data:
            return ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="jp_empty")
        return ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="jp_id_mismatch")
    # Optional failure signal via title convention for tests.
    title = str(data.get("title") or "")
    if title.startswith("FAIL:"):
        return ToolOutcome(
            kind=ToolOutcomeKind.FAILED,
            evidence="jp_title_fail_marker",
            raw_result=data,
        )
    return ToolOutcome(
        kind=ToolOutcomeKind.SUCCEEDED,
        evidence=f"jp_post_id={post_id}",
        raw_result=data,
    )


def lookup_ref(ref: ToolExecutionRef) -> ToolOutcome:
    op = ref.tool_request_id or ref.idempotency_key
    if not op:
        return ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="jp_missing_ref")
    return lookup_post(op)


def run_jsonplaceholder_tool(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    """Side-effect-ish create; returns external id for later lookup."""
    del ctx
    title = str(args.get("title") or "agentruss")
    body = str(args.get("body") or args.get("order_id") or "")
    created = create_post(title=title, body=body)
    return json.dumps(
        {"ok": True, "external_id": created.get("id"), "provider": "jsonplaceholder"}
    )[:500]
