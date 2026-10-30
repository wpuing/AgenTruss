"""Unified sensitive-data redaction for logs, traces, API, LLM, and tool views.

Policy version is bumpable. Never include original sensitive snippets in results.
On internal failure, returns a safe fallback — never the original plaintext for
secret-bearing sinks.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.security import secret_patterns as P

POLICY_VERSION = "redact-v1"


class RedactionSink(str, Enum):
    LOGS = "logs"
    TRACE = "trace"
    API = "api"
    LLM = "llm"
    TOOL_VIEW = "tool_view"
    GENERIC = "generic"


@dataclass
class RedactionResult:
    """Outcome without raw hit fragments."""

    value: Any
    categories: dict[str, int] = field(default_factory=dict)
    policy_version: str = POLICY_VERSION
    blocked: bool = False  # True when credential-class material was found for LLM sink

    def bump(self, category: str, n: int = 1) -> None:
        self.categories[category] = self.categories.get(category, 0) + n


def _luhn_ok(digits: str) -> bool:
    nums = [int(c) for c in digits if c.isdigit()]
    if len(nums) < 13 or len(nums) > 19:
        return False
    checksum = 0
    parity = len(nums) % 2
    for i, d in enumerate(nums):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _merge(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + v
    return out


def redact_text(text: str, *, sink: RedactionSink = RedactionSink.GENERIC) -> RedactionResult:
    """Mask credentials and PII in free text. Never raises with plaintext."""
    try:
        if text is None:
            return RedactionResult(value="")
        if not isinstance(text, str):
            text = str(text)
        if not text:
            return RedactionResult(value="")
        out = text
        cats: dict[str, int] = {}
        blocked = False

        def sub(pattern: re.Pattern[str], repl: str, category: str, whole: str | None = None) -> None:
            nonlocal out, blocked
            matches = list(pattern.finditer(out))
            if not matches:
                return
            cats[category] = cats.get(category, 0) + len(matches)
            if category in {"api_key", "bearer", "jwt", "password", "connection_secret", "private_key"}:
                blocked = True
            if whole is not None:
                out = pattern.sub(whole, out)
            else:
                out = pattern.sub(repl, out)

        # Secrets first (strongest)
        sub(P.PEM_PRIVATE, "[REDACTED_PRIVATE_KEY]", "private_key")
        sub(P.BEARER, "Bearer [REDACTED]", "bearer")
        sub(P.JWT, "[REDACTED_JWT]", "jwt")
        sub(P.GITHUB_PAT, "[REDACTED_TOKEN]", "api_key")
        sub(P.GITLAB_PAT, "[REDACTED_TOKEN]", "api_key")
        sub(P.OPENAI_KEY, "[REDACTED_API_KEY]", "api_key")
        sub(P.AWS_KEY, "[REDACTED_AWS_KEY]", "api_key")
        sub(P.AWS_SECRET, r"aws_secret_access_key=[REDACTED]", "password")
        sub(P.COOKIE_HEADER, "Cookie: [REDACTED]", "bearer")
        sub(P.DB_URL_PASS, r"\1:[REDACTED]@", "connection_secret")
        sub(P.URL_QUERY_SECRET, r"\1[REDACTED]", "url_secret")
        sub(P.ASSIGN_SECRET, r"\1=[REDACTED]", "password")

        # PII
        sub(P.PHONE_CN, "[REDACTED_PHONE]", "phone")
        sub(P.ID_CARD_CN, "[REDACTED_ID]", "id_card")
        sub(P.EMAIL, "[REDACTED_EMAIL]", "email")

        # Payment cards via Luhn
        def card_repl(m: re.Match[str]) -> str:
            raw = re.sub(r"[^\d]", "", m.group(0))
            if _luhn_ok(raw):
                cats["bank_card"] = cats.get("bank_card", 0) + 1
                return "[REDACTED_CARD]"
            return m.group(0)

        out = P.CARD_CANDIDATE.sub(card_repl, out)

        # Long digit runs that look like accounts (when not already masked) — conservative
        if sink in {RedactionSink.LOGS, RedactionSink.TRACE, RedactionSink.LLM}:
            def acct_repl(m: re.Match[str]) -> str:
                digits = m.group(0)
                if _luhn_ok(digits):
                    return m.group(0)  # already handled as card
                if 16 <= len(digits) <= 25:
                    cats["bank_account"] = cats.get("bank_account", 0) + 1
                    return "[REDACTED_ACCOUNT]"
                return digits

            out = P.BANK_ACCOUNT.sub(acct_repl, out)

        return RedactionResult(value=out, categories=cats, blocked=blocked)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("redact_text failed; returning safe stub")
        return RedactionResult(value="[REDACTION_ERROR]", categories={"error": 1}, blocked=True)


def _key_is_sensitive(key: str) -> bool:
    lowered = key.lower()
    if lowered in P.MASK_ALWAYS_KEYS or lowered in {k.lower() for k in P.MASK_ALWAYS_KEYS}:
        return True
    return any(frag in lowered for frag in P.SENSITIVE_KEY_FRAGMENTS)


def _key_preserve(key: str) -> bool:
    return key in P.PRESERVE_KEYS or key.lower() in P.PRESERVE_KEYS


def redact_mapping(
    data: Any,
    *,
    sink: RedactionSink = RedactionSink.GENERIC,
    _depth: int = 0,
) -> RedactionResult:
    """Recursively redact dict/list/str structures."""
    try:
        if _depth > 40:
            return RedactionResult(value="[MAX_DEPTH]", categories={"error": 1}, blocked=True)
        cats: dict[str, int] = {}
        blocked = False

        if data is None or isinstance(data, (bool, int, float)):
            return RedactionResult(value=data)

        if isinstance(data, str):
            return redact_text(data, sink=sink)

        if isinstance(data, list):
            items = []
            for item in data:
                r = redact_mapping(item, sink=sink, _depth=_depth + 1)
                items.append(r.value)
                cats = _merge(cats, r.categories)
                blocked = blocked or r.blocked
            return RedactionResult(value=items, categories=cats, blocked=blocked)

        if isinstance(data, dict):
            out: dict[str, Any] = {}
            for key, value in data.items():
                key_s = str(key)
                if _key_is_sensitive(key_s) and not _key_preserve(key_s):
                    out[key_s] = "***"
                    cats["secret_field"] = cats.get("secret_field", 0) + 1
                    blocked = True
                    continue
                if _key_preserve(key_s) and sink in {RedactionSink.API, RedactionSink.TOOL_VIEW}:
                    # Keep business ids for authorized API views; still scrub string secrets inside.
                    if isinstance(value, str):
                        # Only scrub embedded credentials, not the id itself if short.
                        r = redact_text(value, sink=sink)
                        # If the whole value looks like a credential, mask; else keep for short ids.
                        if r.blocked and len(value) > 40:
                            out[key_s] = r.value
                            cats = _merge(cats, r.categories)
                            blocked = True
                        else:
                            out[key_s] = value
                    else:
                        r = redact_mapping(value, sink=sink, _depth=_depth + 1)
                        out[key_s] = r.value
                        cats = _merge(cats, r.categories)
                        blocked = blocked or r.blocked
                    continue
                if key_s in {"args", "args_encrypted"} and sink in {
                    RedactionSink.API,
                    RedactionSink.LOGS,
                    RedactionSink.TRACE,
                    RedactionSink.TOOL_VIEW,
                }:
                    out[key_s] = None if key_s == "args" else "***"
                    cats["secret_field"] = cats.get("secret_field", 0) + 1
                    blocked = True
                    continue
                r = redact_mapping(value, sink=sink, _depth=_depth + 1)
                out[key_s] = r.value
                cats = _merge(cats, r.categories)
                blocked = blocked or r.blocked
            return RedactionResult(value=out, categories=cats, blocked=blocked)

        # Fallback objects
        return redact_text(str(data), sink=sink)
    except Exception:  # noqa: BLE001
        logging.getLogger(__name__).exception("redact_mapping failed")
        return RedactionResult(value={"error": "[REDACTION_ERROR]"}, categories={"error": 1}, blocked=True)


def redact_for_logs(data: Any) -> Any:
    return redact_mapping(data, sink=RedactionSink.LOGS).value


def redact_for_trace(data: Any) -> Any:
    return redact_mapping(data, sink=RedactionSink.TRACE).value


def redact_for_api(data: Any) -> Any:
    return redact_mapping(data, sink=RedactionSink.API).value


def redact_for_llm(text: str) -> RedactionResult:
    """Minimize secrets/PII before model context. Credentials always stripped."""
    return redact_text(text or "", sink=RedactionSink.LLM)


def mask_tool_args(args: dict[str, Any] | None) -> dict[str, Any]:
    """Safe summary for ticket/API display. Preserves order_id."""
    if not args:
        return {}
    result = redact_mapping(dict(args), sink=RedactionSink.TOOL_VIEW)
    out = result.value if isinstance(result.value, dict) else {}
    if "order_id" in args:
        out["order_id"] = args["order_id"]
    # Never surface owner ids in clear form
    for k in list(out):
        if k in {"owner_user_id", "user_id"}:
            out[k] = "***"
    return out


def sanitize_exception(exc: BaseException | str, *, max_len: int = 300) -> str:
    """Safe exception message for HTTP/logs — never raw SDK dumps with secrets."""
    raw = str(exc) if not isinstance(exc, str) else exc
    cleaned = redact_text(raw, sink=RedactionSink.LOGS).value
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1] + "…"
    return cleaned or "error"


class RedactingFilter(logging.Filter):
    """Apply redact_for_logs to LogRecord messages, args, and formatted exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if isinstance(record.msg, str):
                record.msg = redact_text(record.msg, sink=RedactionSink.LOGS).value
            if record.args:
                if isinstance(record.args, dict):
                    record.args = redact_for_logs(record.args)
                elif isinstance(record.args, tuple):
                    record.args = tuple(redact_for_logs(a) for a in record.args)
            # Exception stacks often embed URLs/credentials from SDKs.
            if record.exc_text:
                record.exc_text = redact_text(record.exc_text, sink=RedactionSink.LOGS).value
            if record.exc_info and record.exc_info[1] is not None:
                # Replace message on the exception object when safe; keep type for diagnostics.
                try:
                    err = record.exc_info[1]
                    safe = sanitize_exception(err, max_len=500)
                    if str(err) != safe:
                        record.exc_info = (record.exc_info[0], type(err)(safe), record.exc_info[2])
                except Exception:  # noqa: BLE001
                    record.exc_info = None
                    record.exc_text = "[REDACTED_EXCEPTION]"
        except Exception:  # noqa: BLE001
            record.msg = "[REDACTION_ERROR]"
            record.args = ()
            record.exc_text = "[REDACTION_ERROR]"
            record.exc_info = None
        return True


def install_logging_redaction() -> None:
    """Attach redacting filter to root and common library loggers."""
    targets = [
        logging.getLogger(),
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("app"),
    ]
    for logger in targets:
        if not any(isinstance(f, RedactingFilter) for f in logger.filters):
            logger.addFilter(RedactingFilter())
        for handler in logger.handlers:
            if not any(isinstance(f, RedactingFilter) for f in handler.filters):
                handler.addFilter(RedactingFilter())
    # Also cover handlers added via basicConfig on root after first install.
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
