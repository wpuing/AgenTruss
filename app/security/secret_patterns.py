"""Compiled patterns for credentials and PII. Fictional test data only in tests."""

from __future__ import annotations

import re

# --- Credentials / secrets (value shapes) ---
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")
JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")
OPENAI_KEY = re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")
DASHSCOPE_KEY = re.compile(r"\bsk-[a-z0-9]{20,}\b", re.I)  # overlap ok; both → mask
GITHUB_PAT = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")
GITLAB_PAT = re.compile(r"\bglpat-[A-Za-z0-9_\-]{20,}\b")
AWS_KEY = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
PEM_PRIVATE = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
# connection strings with password
DB_URL_PASS = re.compile(
    r"(?i)\b((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp|rediss)://[^:\s/]+):([^@/\s]+)@"
)
URL_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:token|access_token|refresh_token|api[_-]?key|password|secret|signature|auth)=)([^&\s#]+)"
)
# assignment / env-style secrets (fictional values only in tests)
ASSIGN_SECRET = re.compile(
    r"(?i)\b("
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|"
    r"credential|authorization|cookie|session[_-]?token|"
    r"minio[_-]?(?:secret[_-]?key|root[_-]?password)|"
    r"aws[_-]?secret[_-]?(?:access[_-]?key)?|"
    r"postgres[_-]?password|database[_-]?url|redis[_-]?url|"
    r"dashscope[_-]?api[_-]?key|openai[_-]?api[_-]?key|"
    r"jwt[_-]?secret|ticket[_-]?args[_-]?secret"
    r")\s*[:=]\s*([^\s,;\"']{6,})"
)
COOKIE_HEADER = re.compile(r"(?i)\bCookie:\s*([^\r\n]+)")
AWS_SECRET = re.compile(
    r"(?i)\b(?:aws_secret_access_key|aws_secret)\s*[:=]\s*([A-Za-z0-9/+=]{20,})"
)

# --- PII ---
PHONE_CN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
ID_CARD_CN = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Luhn-checked separately in detector; pattern is candidate card numbers 13-19 digits
CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]*?){13,19}(?!\d)")
BANK_ACCOUNT = re.compile(r"(?<!\d)\d{16,25}(?!\d)")

SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "access_key",
    "secret_key",
    "refresh",
    "jwt",
    "session_id_cookie",
)

# Keys that are business identifiers — preserve value for API/tool display sinks.
PRESERVE_KEYS = frozenset(
    {
        "order_id",
        "ticket_id",
        "audit_id",
        "run_id",
        "trace_id",
        "session_id",
        "conversation_id",
        "kb_id",
        "doc_id",
        "agent_id",
        "tenant_id",
        "status",
        "tool_name",
        "topic",
        "block_reason",
        "runtime_id",
        "workflow_thread_id",
    }
)

MASK_ALWAYS_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "api_key",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "secret",
        "private_key",
        "args_encrypted",
        "jwt_secret",
        "ticket_args_secret",
        "minio_secret_key",
        "minio_access_key",
        "minio_root_password",
        "demo_password",
        "postgres_password",
        "database_url",
        "redis_url",
        "aws_secret_access_key",
        "dashscope_api_key",
        "openai_api_key",
        "session_token",
    }
)
