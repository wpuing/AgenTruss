"""Echo protocol sandbox — local HTTP + in-process durable op store.

Tier: **Echo protocol sandbox** (see docs/architecture/02-adapter-capability-tiers.md).
Real httpx round-trip to ThreadingHTTPServer; ops persist for lookup. Not public
HTTPS, not a business ledger, not a production adapter.

Gate: ``RUN_HTTP_ECHO_TOOL=1`` + ``HTTP_ECHO_BASE_URL`` (e.g. http://127.0.0.1:8765/).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.tools.reliability import ToolExecutionRef, ToolOutcome, ToolOutcomeKind

logger = logging.getLogger(__name__)

_OPS: dict[str, dict[str, Any]] = {}
_OPS_LOCK = threading.Lock()
_SERVER: ThreadingHTTPServer | None = None
_SERVER_THREAD: threading.Thread | None = None


def echo_enabled() -> bool:
    return os.getenv("RUN_HTTP_ECHO_TOOL") == "1" and bool(
        (os.getenv("HTTP_ECHO_BASE_URL") or "").strip()
    )


def _base() -> str:
    raw = (os.getenv("HTTP_ECHO_BASE_URL") or "").strip()
    if not raw:
        raise RuntimeError("HTTP_ECHO_BASE_URL required when echo tool enabled")
    parsed = urlparse(raw if "://" in raw else f"http://{raw}")
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError(
            f"HTTP_ECHO_BASE_URL host not allowed: {host or raw!r}; "
            "only 127.0.0.1/localhost"
        )
    return raw.rstrip("/") + "/"


def _timeout_s() -> float:
    try:
        return float(os.getenv("HTTP_ECHO_TIMEOUT_S") or "5")
    except ValueError:
        return 5.0


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        logger.debug("echo_server: " + format, *args)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def do_POST(self) -> None:  # noqa: N802
        parts = urlparse(self.path)
        segs = [s for s in parts.path.split("/") if s]
        if len(segs) != 2 or segs[0] != "ops":
            self.send_error(404)
            return
        op_id = segs[1]
        body = self._read_json()
        record = {
            "operation_id": op_id,
            "status": str(body.get("status") or "ok"),
            "payload": body,
        }
        with _OPS_LOCK:
            _OPS[op_id] = record
        raw = json.dumps(record).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parts = urlparse(self.path)
        segs = [s for s in parts.path.split("/") if s]
        if len(segs) != 2 or segs[0] != "ops":
            self.send_error(404)
            return
        op_id = segs[1]
        with _OPS_LOCK:
            record = _OPS.get(op_id)
        if record is None:
            self.send_error(404)
            return
        raw = json.dumps(record).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_local_echo_server(host: str = "127.0.0.1", port: int = 0) -> str:
    """Start ThreadingHTTPServer; return base URL. Idempotent if already running."""
    global _SERVER, _SERVER_THREAD
    if _SERVER is not None:
        addr = _SERVER.server_address
        return f"http://{str(addr[0])}:{int(addr[1])}/"
    server = ThreadingHTTPServer((host, port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _SERVER = server
    _SERVER_THREAD = thread
    addr = server.server_address
    return f"http://{str(addr[0])}:{int(addr[1])}/"


def stop_local_echo_server() -> None:
    global _SERVER, _SERVER_THREAD
    if _SERVER is not None:
        _SERVER.shutdown()
        _SERVER = None
        _SERVER_THREAD = None
    with _OPS_LOCK:
        _OPS.clear()


def post_echo(operation_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = urljoin(_base(), f"ops/{operation_id}")
    try:
        with httpx.Client(timeout=_timeout_s()) as client:
            resp = client.post(url, json={"operation_id": operation_id, **payload})
            resp.raise_for_status()
            return resp.json() if resp.content else {}
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"echo_transport:{exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(f"echo_http_{exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise TimeoutError(f"echo_transport:{exc}") from exc


def get_echo(operation_id: str) -> ToolOutcome:
    url = urljoin(_base(), f"ops/{operation_id}")
    try:
        with httpx.Client(timeout=_timeout_s()) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                return ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="echo_not_found")
            if resp.status_code >= 400:
                return ToolOutcome(
                    kind=ToolOutcomeKind.UNKNOWN,
                    evidence=f"echo_http_{resp.status_code}",
                )
            data = resp.json() if resp.content else {}
    except httpx.TimeoutException as exc:
        raise TimeoutError(f"echo_lookup_transport:{exc}") from exc
    except httpx.HTTPError as exc:
        raise TimeoutError(f"echo_lookup_transport:{exc}") from exc

    if not isinstance(data, dict) or data.get("operation_id") != operation_id:
        return ToolOutcome(kind=ToolOutcomeKind.UNKNOWN, evidence="echo_malformed")
    status = str(data.get("status") or "ok")
    if status in {"failed", "error", "declined"}:
        return ToolOutcome(
            kind=ToolOutcomeKind.FAILED,
            evidence=f"echo_status={status}",
            raw_result=data,
        )
    if status in {"pending", "processing"}:
        return ToolOutcome(
            kind=ToolOutcomeKind.UNKNOWN,
            evidence=f"echo_status={status}",
            raw_result=data,
        )
    return ToolOutcome(
        kind=ToolOutcomeKind.SUCCEEDED,
        evidence=f"echo_status={status}",
        raw_result=data,
    )


def lookup_ref(ref: ToolExecutionRef) -> ToolOutcome:
    op = ref.tool_request_id or ref.idempotency_key or ref.logical_execution_id
    return get_echo(str(op))


def run_echo_tool(args: dict[str, Any], ctx: dict[str, Any]) -> str:
    del ctx
    op = str(args.get("operation_id") or args.get("order_id") or "missing")
    result = post_echo(
        op,
        {"status": args.get("echo_status") or "ok", "order_id": args.get("order_id")},
    )
    return json.dumps({"ok": True, "operation_id": op, "echo": result})[:500]
