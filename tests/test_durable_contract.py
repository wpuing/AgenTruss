"""Production Durable Adapter registration contract (no sandbox invented)."""

from __future__ import annotations

import pytest
from app.tools.durable_contract import (
    EXTERNAL_ENABLEMENT_CHECKLIST,
    assert_production_registration_ready,
)
from app.tools.registry import CapabilityDeclarationError, validate_capability_meta


def test_production_registration_ready_requires_acceptance_id() -> None:
    with pytest.raises(ValueError, match="missing required"):
        assert_production_registration_ready(
            {
                "name": "biz_pay",
                "capability_tier": "production",
                "supports_result_lookup": True,
            }
        )


def test_production_registration_ready_ok() -> None:
    assert_production_registration_ready(
        {
            "name": "biz_pay",
            "capability_tier": "production",
            "supports_result_lookup": True,
            "production_acceptance_id": "sandbox-acc-001",
        }
    )


def test_validate_capability_meta_aligns_with_durable_contract() -> None:
    tier = validate_capability_meta(
        {
            "name": "biz_pay",
            "capability_tier": "production",
            "supports_result_lookup": True,
            "production_acceptance_id": "sandbox-acc-001",
        }
    )
    assert tier == "production"
    with pytest.raises(CapabilityDeclarationError):
        validate_capability_meta(
            {
                "name": "biz_pay",
                "capability_tier": "production",
                "supports_result_lookup": True,
            }
        )


def test_enablement_checklist_covers_external_inputs() -> None:
    required = {
        "official_sandbox_base_url",
        "auth_scheme_and_credential_env_name",
        "execution_id_field_path",
        "result_lookup_endpoint_and_id_param",
        "production_acceptance_id",
    }
    assert required.issubset(set(EXTERNAL_ENABLEMENT_CHECKLIST))


def test_builtin_demo_tools_are_not_production() -> None:
    from app.tools.registry import get_tool

    for name in ("query_order", "refund_order"):
        meta = get_tool(name)
        assert meta is not None
        assert meta.get("capability_tier") == "local_demo"
        assert meta.get("supports_result_lookup") is False
        assert str(meta.get("production_acceptance_id") or "") == ""
