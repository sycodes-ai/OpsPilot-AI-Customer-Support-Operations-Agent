"""Structured logging and metrics for every agent turn."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Event
from .guardrails import mask_pii

if TYPE_CHECKING:
    from .agent import AgentReply

logger = logging.getLogger("opspilot")


def setup_logging(level: str = "INFO") -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False


def log_turn(db: Session, reply: "AgentReply", session_id: str, message: str) -> None:
    tool_log = [{"name": t.name, "ok": t.ok, "ms": t.latency_ms,
                 **({"error": t.result.get("error")} if not t.ok and t.result.get("error") else {})}
                for t in reply.tool_calls]
    record = {
        "event": "agent_turn", "request_id": reply.request_id, "session_id": session_id, "mode": reply.mode,
        "intent": reply.intent, "tools": tool_log, "refused": reply.refused, "escalated": reply.escalated,
        "guardrail": reply.guardrail, "latency_ms": reply.latency_ms, "error": reply.error,
        "message": mask_pii(message)[:120],
    }
    logger.info(json.dumps(record, ensure_ascii=False))
    try:
        db.add(Event(request_id=reply.request_id, session_id=session_id[:64], mode=reply.mode, intent=reply.intent,
                     tools=tool_log, refused=reply.refused, escalated=reply.escalated, error=reply.error,
                     latency_ms=reply.latency_ms, message_preview=mask_pii(message)[:200]))
        db.commit()
    except Exception as exc:  # logging must never break a chat reply
        db.rollback()
        logger.warning(json.dumps({"event": "log_write_failed", "error": str(exc)[:200]}))


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    idx = min(len(v) - 1, max(0, int(round(p / 100 * (len(v) - 1)))))
    return round(v[idx], 1)


def compute_metrics(db: Session, hours: int = 24 * 7, recent: int = 15) -> dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = db.scalars(select(Event).where(Event.ts >= since).order_by(Event.ts.desc())).all()
    n = len(events)
    tool_counts: Counter = Counter()
    tool_fail: Counter = Counter()
    for e in events:
        for t in e.tools or []:
            tool_counts[t["name"]] += 1
            if not t.get("ok"):
                tool_fail[t["name"]] += 1
    lat = [e.latency_ms for e in events]
    return {
        "window_hours": hours,
        "total_turns": n,
        "intents": dict(Counter(e.intent for e in events)),
        "modes": dict(Counter(e.mode for e in events)),
        "tool_calls": dict(tool_counts),
        "tool_failures": dict(tool_fail),
        "refusal_rate": round(sum(e.refused for e in events) / n, 3) if n else 0.0,
        "escalation_rate": round(sum(e.escalated for e in events) / n, 3) if n else 0.0,
        "errors": sum(1 for e in events if e.error),
        "latency_ms": {"p50": _pct(lat, 50), "p95": _pct(lat, 95), "max": round(max(lat), 1) if lat else 0.0},
        "recent": [{"ts": e.ts.isoformat() if e.ts else None, "intent": e.intent, "mode": e.mode,
                    "tools": [t["name"] for t in (e.tools or [])], "refused": e.refused, "escalated": e.escalated,
                    "latency_ms": e.latency_ms, "error": bool(e.error), "message": e.message_preview}
                   for e in events[:recent]],
    }
