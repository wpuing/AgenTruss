"""Production Durable Adapter — contract shapes (no vendor URL invented).

Echo / JSONPlaceholder remain demos. Real production adapters must satisfy these
schemas plus ``validate_capability_meta`` / ``production_acceptance_id``.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict


class ExecutionRequestContract(TypedDict, total=False):
    """Outbound execute request fields a production adapter must support."""

    tool_name: str
    tenant_id: str
    logical_execution_id: str
    idempotency_key: str | None
    args: dict[str, Any]
    # Opaque correlation returned by the business system when accepted.
    external_execution_id: str


class ExecutionAcceptResponse(TypedDict, total=False):
    """Accepted-but-not-necessarily-final response from the business system."""

    status: Literal["accepted", "succeeded", "failed", "running"]
    external_execution_id: str
    evidence: str


class ResultLookupRequest(TypedDict):
    """Result query keyed by durable execution identity — never re-runs the tool."""

    tenant_id: str
    tool_name: str
    logical_execution_id: str
    external_execution_id: str | None
    idempotency_key: str | None


class ResultLookupResponse(TypedDict, total=False):
    """Mapped lookup outcome. UNKNOWN must not be coerced to FAILED."""

    kind: Literal["succeeded", "failed", "unknown"]
    evidence: str
    http_status: int | None
    retryable: bool


REQUIRED_PRODUCTION_META = frozenset(
    {
        "name",
        "capability_tier",
        "supports_result_lookup",
        "production_acceptance_id",
    }
)


def assert_production_registration_ready(meta: dict[str, Any]) -> None:
    """Raise ValueError when production-tier meta lacks acceptance evidence.

    Does not prove the sandbox works — only that declaration is complete enough
    to register. Live sandbox URL/auth remain external inputs.
    """
    missing = [k for k in REQUIRED_PRODUCTION_META if not str(meta.get(k) or "").strip()]
    if missing:
        raise ValueError(f"production adapter meta missing required fields: {missing}")
    if str(meta.get("capability_tier") or "").strip().lower() != "production":
        raise ValueError("assert_production_registration_ready requires capability_tier=production")
    if not bool(meta.get("supports_result_lookup")):
        raise ValueError("production adapter must support_result_lookup=True")


# Checklist for operators (also mirrored in docs/architecture/08-*).
EXTERNAL_ENABLEMENT_CHECKLIST = (
    "official_sandbox_base_url",
    "auth_scheme_and_credential_env_name",
    "execution_id_field_path",
    "result_lookup_endpoint_and_id_param",
    "terminal_status_vocabulary",
    "idempotency_header_or_body_field",
    "result_retention_sla",
    "production_acceptance_id",
)
