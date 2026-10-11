"""FastAPI application: chat endpoint, read-only tool endpoints, metrics and the static React build."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import tools
from .agent import Agent
from .config import Settings, get_settings
from .db import init_db, make_engine, make_session_factory
from .llm import LLMClient
from .observability import compute_metrics, setup_logging
from .rag.retriever import Retriever
from .seed import seed


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    session_id: str | None = Field(default=None, max_length=64)


class RateLimiter:
    """Small in-memory sliding-window limiter to protect the public demo and the LLM budget."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return False
        q.append(now)
        return True


def create_app(settings: Settings | None = None, llm: LLMClient | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_db(engine)
        if settings.seed_on_startup:
            with session_factory() as s:
                seed(s)
        client = llm
        if client is None and settings.resolved_mode == "llm":
            client = LLMClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model, settings.llm_timeout_s)
        app.state.retriever = Retriever.from_dir(settings.docs_dir)
        app.state.agent = Agent(session_factory, app.state.retriever, settings, llm=client)
        yield
        engine.dispose()

    app = FastAPI(title="OpsPilot API", version="1.0.0", lifespan=lifespan,
                  description="AI customer support and operations agent (demo with synthetic data).")
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.session_factory = session_factory
    app.state.settings = settings

    def get_db():
        with session_factory() as s:
            yield s

    @app.get("/api/health")
    def health():
        agent: Agent = app.state.agent
        return {"status": "ok", "mode": agent.mode, "model": settings.llm_model if agent.mode == "llm" else None,
                "documents": len(app.state.retriever.chunks)}

    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request):
        ip = request.client.host if request.client else "unknown"
        if not limiter.allow(ip):
            return JSONResponse({"detail": "Too many messages. Please wait a minute."}, status_code=429)
        agent: Agent = app.state.agent
        reply = agent.handle(body.message, [t.model_dump() for t in body.history], body.session_id)
        return reply.to_dict()

    @app.get("/api/orders/{order_id}")
    def order_status(order_id: str, email: str = Query(..., min_length=3, max_length=200), db=Depends(get_db)):
        result = tools.get_order_status(db, order_id, email)
        if not result["ok"]:
            # Same answer for "no such order" and "wrong email", so order numbers can't be probed.
            raise HTTPException(status_code=404, detail=tools.VERIFY_FAILED)
        return result

    @app.get("/api/inventory")
    def inventory(q: str = Query(..., min_length=2, max_length=100), db=Depends(get_db)):
        return tools.check_inventory(db, q)

    @app.get("/api/knowledge")
    def knowledge():
        return [{"source": c.source, "title": c.title, "section": c.section} for c in app.state.retriever.chunks]

    @app.get("/api/metrics")
    def metrics(hours: int = Query(168, ge=1, le=24 * 90), db=Depends(get_db)):
        return compute_metrics(db, hours=hours)

    if settings.static_dir.joinpath("index.html").exists():
        app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="web")

    return app


app = create_app()
