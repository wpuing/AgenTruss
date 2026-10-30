"""Shared API client. Each test gets a fresh platform."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture(autouse=True)
def _isolate_redis_unless_gated(monkeypatch):
    """Avoid durable Redis login/rate counters polluting the suite.

    Real Redis integration is opt-in via ``RUN_REDIS_INTEGRATION=1``.
    """
    if os.getenv("RUN_REDIS_INTEGRATION") == "1":
        yield
        return
    from app.infra import redis_client

    redis_client.close_redis()
    monkeypatch.setattr(redis_client, "get_redis", lambda: None)
    yield
    redis_client.close_redis()


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def login(client, username: str, tenant: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": username, "password": "changeme", "tenant_code": tenant},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
