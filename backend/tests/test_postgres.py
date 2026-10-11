"""Runs only when TEST_DATABASE_URL points at a PostgreSQL database (CI provides one)."""

import os

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL not set")


def test_app_works_on_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setenv("AGENT_MODE", "rules")
    with TestClient(create_app(Settings())) as c:
        assert c.get("/api/health").json()["status"] == "ok"
        r = c.post("/api/chat", json={"message": "Where is OP-1003? sofia.rossi@example.com"}).json()
        assert r["tool_calls"][0]["ok"] is True
        assert c.get("/api/metrics").json()["total_turns"] >= 1
