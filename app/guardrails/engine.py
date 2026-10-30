"""Input, topic, and output guards. Action checks live next to the tool registry."""

from __future__ import annotations

import re
from dataclasses import dataclass

ZERO_WIDTH = re.compile(r"[\u200b-\u200f\ufeff\x00-\x08\x0b\x0c\x0e-\x1f]")
PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
INJECTION = [
    re.compile(r"ignore previous instructions", re.I),
    re.compile(r"忽略(以上|前面|之前).{0,12}(指令|规则)"),
    re.compile(r"disregard (all|your) (rules|instructions)", re.I),
]
LEAKS = [
    re.compile(r"system\s*prompt", re.I),
    re.compile(r"系统提示词"),
    re.compile(r"api[_-]?key\s*=", re.I),
    re.compile(r"BEGIN (RSA |OPENSSH )?PRIVATE KEY"),
]


@dataclass
class GuardResult:
    """Unified guard outcome. Callers must use ``allowed``."""

    allowed: bool
    sanitized_message: str
    topic: str | None = None
    confidence: float = 0.0
    reason: str | None = None
    block_message: str = "抱歉，您的问题暂时无法处理。"


def mask_pii(text: str) -> str:
    """Mask PII and credentials via the unified redaction service."""
    from app.security.redaction import redact_text

    return str(redact_text(text or "").value)


def clean_text(text: str) -> str:
    """Drop zero-width and control characters."""
    return ZERO_WIDTH.sub("", text)


def check_input(message: str, industry: dict) -> GuardResult:
    """Length, cleanup, injection, topic whitelist, then PII mask."""
    limit = min(int(industry.get("max_message_length") or 2000), 2000)
    if len(message) > limit:
        return GuardResult(False, "", reason="too_long", block_message="消息超出长度上限。")
    cleaned = clean_text(message).strip()
    for pattern in INJECTION:
        if pattern.search(cleaned):
            return GuardResult(False, mask_pii(cleaned), reason="injection", block_message="请求包含不受支持的指令。")
    topic, confidence = classify_topic(cleaned, industry)
    threshold = float(industry.get("confidence_threshold") or 0.6)
    allowed_topics = set(industry.get("topics") or [])
    if topic not in allowed_topics or confidence < threshold:
        return GuardResult(
            False,
            mask_pii(cleaned),
            topic=topic,
            confidence=confidence,
            reason="off_topic",
            block_message=str(industry.get("off_topic_message") or "该话题不在服务范围。"),
        )
    return GuardResult(True, mask_pii(cleaned), topic=topic, confidence=confidence)


def classify_topic(text: str, industry: dict) -> tuple[str, float]:
    """Keyword classifier. First whitelist hit wins."""
    for topic in industry.get("topics") or []:
        if topic and topic in text:
            return topic, 0.92
    return "other", 0.2


def check_output(reply: str, industry: dict) -> GuardResult:
    """Block high-confidence leaks and configured unauthorized promises."""
    for pattern in LEAKS:
        if pattern.search(reply or ""):
            return GuardResult(False, "", reason="output_leak", block_message="回复包含不允许展示的内容。")
    for phrase in industry.get("risky_promises") or []:
        if phrase and phrase in (reply or ""):
            return GuardResult(False, "", reason="risky_promise", block_message="不能直接承诺该操作，请走人工审批。")
    return GuardResult(True, reply or "")
