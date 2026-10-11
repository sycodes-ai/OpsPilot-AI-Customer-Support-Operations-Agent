from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.agent import Agent
from app.config import Settings
from app.db import init_db, make_engine, make_session_factory
from app.rag.retriever import Retriever
from app.seed import seed

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session_factory():
    engine = make_engine("sqlite://")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as s:
        seed(s, now=NOW)
    yield factory
    engine.dispose()


@pytest.fixture()
def db(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture(scope="session")
def retriever():
    return Retriever.from_dir(ROOT / "data" / "docs")


@pytest.fixture()
def settings(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AGENT_MODE", "rules")
    return Settings()


@pytest.fixture()
def agent(session_factory, retriever, settings):
    return Agent(session_factory, retriever, settings, clock=lambda: NOW)
