"""Backend tools the agent can call.

Every rule that matters (identity checks, the 30-day return window, no data for other customers) is
enforced here in code. The language model can choose a tool, but it cannot skip these checks.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import Order, Product, ReturnRequest, Ticket
from .rag.retriever import Retriever

VERIFY_FAILED = "I couldn't verify that order number with that email address."
RETURN_FEE_KRW = 5000
MANUAL_REVIEW_KRW = 500_000
FREE_RETURN_REASONS = ("defect", "damag", "broken", "wrong", "faulty", "torn", "missing")

ORDER_ID_RE = re.compile(r"\bOP[-\s]?(\d{4})\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def normalize_order_id(raw: str | None) -> str | None:
    if not raw:
        return None
    m = ORDER_ID_RE.search(raw) or re.fullmatch(r"\s*(\d{4})\s*", raw)
    return f"OP-{m.group(1)}" if m else None


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _date(dt: datetime | None) -> str | None:
    dt = _as_utc(dt)
    return dt.date().isoformat() if dt else None


def _verified_order(session: Session, order_id: str | None, email: str | None) -> Order | None:
    oid = normalize_order_id(order_id)
    if not oid or not email:
        return None
    order = session.get(Order, oid)
    if order is None or order.customer.email.lower() != email.strip().lower():
        return None
    return order


def get_order_status(session: Session, order_id: str | None, email: str | None, now: datetime | None = None) -> dict:
    if not normalize_order_id(order_id) or not email:
        return {"ok": False, "error": "missing_details", "message": "I need your order number (for example OP-1001) and the email used for the order."}
    order = _verified_order(session, order_id, email)
    if order is None:
        return {"ok": False, "error": "verification_failed", "message": VERIFY_FAILED}
    now = now or datetime.now(timezone.utc)
    delivered = _as_utc(order.delivered_at)
    result: dict[str, Any] = {
        "ok": True,
        "order_id": order.id,
        "status": order.status,
        "items": [f"{i['qty']} x {i['name']}" for i in order.items],
        "ordered_on": _date(order.ordered_at),
        "shipped_on": _date(order.shipped_at),
        "delivered_on": _date(order.delivered_at),
        "carrier": order.carrier,
        "tracking_number": order.tracking_number,
    }
    if order.status == "delivered" and delivered:
        last_day = delivered + timedelta(days=30)
        result["return_eligible_until"] = last_day.date().isoformat()
        result["return_window_open"] = now <= last_day
    return result


def check_inventory(session: Session, query: str | None) -> dict:
    if not query or not query.strip():
        return {"ok": False, "error": "missing_details", "message": "Which product would you like me to check?"}
    q = query.strip()
    products = session.scalars(select(Product)).all()
    sku_hit = [p for p in products if p.sku.lower() in q.lower()]
    if sku_hit:
        matches = sku_hit
    else:
        words = {w for w in re.findall(r"[a-z0-9]+", q.lower()) if len(w) > 2}
        scored = []
        for p in products:
            name_words = set(re.findall(r"[a-z0-9]+", p.name.lower()))
            overlap = len(words & name_words)
            if overlap:
                scored.append((overlap, p))
        scored.sort(key=lambda x: x[0], reverse=True)
        best = scored[0][0] if scored else 0
        matches = [p for s, p in scored if s == best][:3]
    if not matches:
        return {"ok": False, "error": "not_found", "message": f"I couldn't find a product matching '{q}'."}
    out = []
    for p in matches:
        state = "out_of_stock" if p.stock == 0 else "low_stock" if p.stock <= 3 else "in_stock"
        out.append({"sku": p.sku, "name": p.name, "price_krw": int(p.price), "stock": p.stock,
                    "availability": state, "restock_date": p.restock_date})
    return {"ok": True, "products": out}


def create_return_request(session: Session, order_id: str | None, email: str | None, reason: str | None,
                          now: datetime | None = None) -> dict:
    if not normalize_order_id(order_id) or not email:
        return {"ok": False, "error": "missing_details", "message": "To start a return I need your order number and the email used for the order."}
    order = _verified_order(session, order_id, email)
    if order is None:
        return {"ok": False, "error": "verification_failed", "message": VERIFY_FAILED}
    if not reason or not reason.strip():
        return {"ok": False, "error": "missing_reason", "message": "What is the reason for the return?"}
    now = now or datetime.now(timezone.utc)
    existing = session.scalar(select(func.count()).select_from(ReturnRequest).where(ReturnRequest.order_id == order.id))
    if order.status == "returned" or existing:
        return {"ok": False, "error": "already_returned", "message": f"Order {order.id} already has a return on file."}
    if order.status != "delivered" or order.delivered_at is None:
        return {"ok": False, "error": "not_delivered",
                "message": f"Order {order.id} is '{order.status}', so it can't be returned yet. Returns start after delivery."}
    last_day = _as_utc(order.delivered_at) + timedelta(days=30)
    if now > last_day:
        return {"ok": False, "error": "window_closed",
                "message": f"The 30-day return window for {order.id} ended on {last_day.date().isoformat()}."}
    if order.total > MANUAL_REVIEW_KRW:
        return {"ok": False, "error": "needs_review", "message": "Refunds above 500,000 KRW need a review by our support team."}
    free = any(k in reason.lower() for k in FREE_RETURN_REASONS)
    rma = f"RMA-{order.id[-4:]}-{now:%y%m%d}"
    session.add(ReturnRequest(order_id=order.id, reason=reason.strip()[:500], rma_code=rma, created_at=now))
    session.commit()
    return {"ok": True, "order_id": order.id, "rma_code": rma,
            "return_fee_krw": 0 if free else RETURN_FEE_KRW,
            "refund_estimate_krw": int(order.total - (0 if free else RETURN_FEE_KRW)),
            "drop_off_by": (now + timedelta(days=7)).date().isoformat()}


def escalate_to_human(session: Session, session_id: str, reason: str, summary: str, priority: str = "normal") -> dict:
    priority = priority if priority in {"low", "normal", "high"} else "normal"
    ticket = Ticket(session_id=session_id[:64], reason=reason[:60], summary=summary[:1000], priority=priority)
    session.add(ticket)
    session.commit()
    return {"ok": True, "ticket_id": f"T-{ticket.id:05d}", "priority": priority,
            "message": "A support team member will reply within one business day (Mon–Fri, 9:00–18:00 KST)."}


def search_knowledge_base(retriever: Retriever, query: str, k: int = 3) -> dict:
    hits = retriever.search(query or "", k=k)
    return {"ok": bool(hits), "results": [
        {"source": h.chunk.source, "title": h.chunk.title, "section": h.chunk.section, "text": h.chunk.text, "score": h.score}
        for h in hits]}


# JSON schemas for LLM function calling (OpenAI-compatible "tools" format).
TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "search_knowledge_base",
        "description": "Search store policies, FAQs and product information. Use for any question about returns, refunds, shipping, payments, warranty or products.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_order_status",
        "description": "Look up an order. Requires BOTH the order number and the customer's email; never guess either.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string", "description": "Order number such as OP-1001"},
            "email": {"type": "string"}}, "required": ["order_id", "email"]}}},
    {"type": "function", "function": {
        "name": "check_inventory",
        "description": "Check price, stock and restock date for a product by name or SKU.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "create_return_request",
        "description": "Start a return for a delivered order. Requires order number, email and the customer's reason.",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "email": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["order_id", "email", "reason"]}}},
    {"type": "function", "function": {
        "name": "escalate_to_human",
        "description": "Hand the conversation to a human agent: the customer asks for a person, is very upset, mentions legal action, or the request is outside your permissions.",
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string"}, "summary": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "normal", "high"]}}, "required": ["reason", "summary"]}}},
]
