"""The OpsPilot agent: guardrails -> intent -> tools -> grounded reply -> logging.

Two modes share the same tools and guardrails:
* "llm"   - an OpenAI-compatible model chooses tools through function calling.
* "rules" - a deterministic router used when no API key is set, in CI, and as a fallback when the
            LLM call fails. It keeps the public demo working without paid API access.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session, sessionmaker

from . import guardrails, tools
from .config import Settings
from .llm import LLMClient, LLMError
from .observability import log_turn
from .rag.retriever import Retriever

STORE_EMAILS = {"support@nimbus-outfitters.example"}

SYSTEM_PROMPT = """You are OpsPilot, the customer support assistant for Nimbus Outfitters, an outdoor gear store in Korea.
You help with order status, returns, product stock and store policies. Rules:
1. For any policy, shipping, payment, warranty or product question, call search_knowledge_base and answer ONLY from the results. If the results do not answer the question, say you don't know and offer a human agent.
2. Order details require BOTH the order number and the email the customer gives you. Never guess or reuse an email or order number the customer did not write. Never reveal another customer's data.
3. Use create_return_request only when the customer clearly asks to return an order and has given a reason. Report the tool result exactly; never promise anything the tool did not confirm.
4. You cannot change prices, create discounts, refund outside policy or extend the return window. Offer escalate_to_human for exceptions.
5. Call escalate_to_human if the customer asks for a person, is very upset, or mentions legal action.
6. Be brief and friendly (2-4 sentences). Prices are in KRW. Reply in the customer's language."""

INTENT_BY_TOOL = {
    "get_order_status": "order_status", "create_return_request": "return_request",
    "check_inventory": "inventory", "search_knowledge_base": "policy_question", "escalate_to_human": "escalation",
}

RETURN_REASONS = [
    ("too small", "Too small"), ("too big", "Too big"), ("too large", "Too big"), ("doesn't fit", "Doesn't fit"),
    ("does not fit", "Doesn't fit"), ("wrong size", "Wrong size"), ("defect", "Defective item"),
    ("broken", "Defective item"), ("damaged", "Damaged item"), ("faulty", "Defective item"),
    ("wrong item", "Wrong item received"), ("changed my mind", "Changed my mind"), ("don't need", "No longer needed"),
    ("do not need", "No longer needed"), ("don't like", "Did not like it"), ("not as described", "Not as described"),
]


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    ok: bool
    result: dict[str, Any]
    latency_ms: float


@dataclass
class AgentReply:
    reply: str
    intent: str
    mode: str
    request_id: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    refused: bool = False
    escalated: bool = False
    ticket_id: str | None = None
    guardrail: str | None = None
    latency_ms: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for tc in d["tool_calls"]:
            tc["args"] = {k: guardrails.mask_pii(str(v)) for k, v in tc["args"].items()}
        return d


# --------------------------------------------------------------------------- helpers

def _user_texts(history: list[dict], message: str) -> list[str]:
    """User messages, newest first."""
    texts = [message] + [m.get("content", "") for m in reversed(history) if m.get("role") == "user"]
    return [t for t in texts if t][:6]


def _find_order_id(texts: list[str]) -> str | None:
    for t in texts:
        m = tools.ORDER_ID_RE.search(t)
        if m:
            return f"OP-{m.group(1)}"
    return None


def _find_email(texts: list[str]) -> str | None:
    for t in texts:
        m = tools.EMAIL_RE.search(t)
        if m:
            return m.group(0)
    return None


def _find_reason(texts: list[str]) -> str | None:
    for t in texts[:3]:
        low = t.lower()
        for key, label in RETURN_REASONS:
            if key in low:
                return label
        m = re.search(r"\b(?:because|reason(?: is)?:?|as)\s+(?:it(?:'s| is)\s+)?(.{4,80})", low)
        if m:
            return m.group(1).strip(" .!")
    return None


def _has(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text) for p in patterns)


def _mentions_product(retriever_products: list[str], text: str) -> bool:
    low = text.lower()
    return any(p in low for p in retriever_products)


def classify(message: str, product_words: list[str], prev_texts: list[str] | None = None) -> str:
    """Deterministic intent classifier used by rules mode (and for logging)."""
    low = message.lower().strip()
    has_order = bool(tools.ORDER_ID_RE.search(message))
    has_email = bool(tools.EMAIL_RE.search(message))
    wants = _has(low, [r"\b(i want|i'd like|i would like|i need|please|start|help me|can you|could you|let me)\b"])
    return_word = _has(low, [r"\breturn\b", r"\bsend (it )?back\b", r"\brefund\b", r"\bexchange\b"])
    if return_word and (has_order or (wants and _has(low, [r"\b(my|this|the) (order|item|jacket|shoes|tent|bag|boots|purchase)\b"]))):
        return "return_request"
    general_question = bool(re.match(r"^(can|could|do|does|how|what|which|is|are|why)\b", low)) and not (has_order or has_email)
    status_patterns = [r"where('s| is) my (order|package|parcel)", r"\border status\b", r"\btrack(ing)?\b", r"status of"]
    if not general_question:
        status_patterns += [r"has my order", r"when will my order", r"\bmy order\b.{0,30}\b(arrive|ship|come)", r"\bcheck (my )?order\b"]
    if _has(low, status_patterns):
        return "order_status"
    stock_words = _has(low, [r"\bin stock\b", r"\bstock\b", r"\bavailab", r"\brestock", r"\bback in\b", r"\bdo you have\b",
                             r"\bsold out\b", r"\bhow many\b.{0,20}\bleft\b", r"\bprice of\b"])
    if stock_words and (_mentions_product(product_words, low) or re.search(r"\bnb-[a-z]{3}-\d{2}\b", low)):
        return "inventory"
    if has_order and not return_word:
        return "order_status"
    if _has(low, [r"^(hi|hello|hey|good (morning|afternoon|evening)|안녕)\b"]) and len(low) < 40:
        return "greeting"
    gives_reason = any(key in low for key, _ in RETURN_REASONS)
    if (has_email or has_order or gives_reason) and prev_texts:
        for prev in prev_texts:
            prev_intent = classify(prev, product_words)
            if prev_intent in {"order_status", "return_request"}:
                return prev_intent
    return "policy_question"


def _best_sentences(text: str, query: str, n: int = 2) -> str:
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", text) if s and not s.endswith("?")] or [text]
    q = set(tools_tokens(query))
    scored = sorted(((len(q & set(tools_tokens(s))), i, s) for i, s in enumerate(sentences)), key=lambda x: (-x[0], x[1]))
    keep = sorted(scored[:n], key=lambda x: x[1])
    return " ".join(s for _, _, s in keep if s.strip()) or sentences[0]


def tools_tokens(text: str) -> list[str]:
    from .rag.retriever import tokenize
    return tokenize(text)


def _fmt_krw(n: int | float) -> str:
    return f"{int(n):,} KRW"


# --------------------------------------------------------------------------- agent

class Agent:
    def __init__(self, session_factory: sessionmaker, retriever: Retriever, settings: Settings,
                 llm: LLMClient | None = None, clock: Callable[[], Any] | None = None):
        self.session_factory = session_factory
        self.retriever = retriever
        self.settings = settings
        self.llm = llm
        self.clock = clock  # injectable "now" for tests
        with session_factory() as s:
            from .db import Product
            names = [p.name.lower() for p in s.query(Product).all()]
        words = set()
        for n in names:
            words.add(n)
            for w in re.findall(r"[a-z]+", n):
                if len(w) > 3 and w not in {"pair", "person", "compact"}:
                    words.add(w)
        self.product_words = sorted(words, key=len, reverse=True)

    @property
    def mode(self) -> str:
        return "llm" if (self.settings.resolved_mode == "llm" and self.llm is not None) else "rules"

    # ---- public entry point
    def handle(self, message: str, history: list[dict] | None = None, session_id: str | None = None) -> AgentReply:
        history = history or []
        session_id = session_id or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        reply = AgentReply(reply="", intent="unknown", mode=self.mode, request_id=uuid.uuid4().hex[:12])
        texts = _user_texts(history, message)
        with self.session_factory() as db:
            try:
                guard = guardrails.check_input(message, self.settings.max_message_chars)
                if guard.action == "refuse":
                    reply.intent, reply.refused, reply.guardrail, reply.reply = "guardrail", True, guard.category, guard.message
                elif guard.action == "escalate":
                    self._escalate(db, reply, session_id, guard.category, message, guard.priority, guard.message)
                elif self.mode == "llm":
                    try:
                        self._llm_flow(db, reply, message, history, session_id, texts)
                    except LLMError as exc:
                        reply.error = str(exc)[:300]
                        reply.mode = "rules-fallback"
                        reply.tool_calls.clear()
                        reply.sources.clear()
                        self._rules_flow(db, reply, message, session_id, texts)
                else:
                    self._rules_flow(db, reply, message, session_id, texts)
            except Exception as exc:  # never crash the chat; log and apologise
                reply.error = f"{type(exc).__name__}: {exc}"[:300]
                reply.reply = "Sorry, something went wrong on our side. Please try again, or ask for a human agent."
            allowed = {e for t in texts for e in tools.EMAIL_RE.findall(t)} | STORE_EMAILS
            reply.reply = guardrails.redact_output(reply.reply, allowed)
            reply.latency_ms = round((time.perf_counter() - start) * 1000, 1)
            log_turn(db, reply, session_id, message)
        return reply

    # ---- tool execution with timing
    def _run_tool(self, db: Session, reply: AgentReply, name: str, args: dict, session_id: str) -> dict:
        t0 = time.perf_counter()
        now = self.clock() if self.clock else None
        if name == "get_order_status":
            result = tools.get_order_status(db, args.get("order_id"), args.get("email"), now=now)
        elif name == "check_inventory":
            result = tools.check_inventory(db, args.get("query"))
        elif name == "create_return_request":
            result = tools.create_return_request(db, args.get("order_id"), args.get("email"), args.get("reason"), now=now)
        elif name == "search_knowledge_base":
            result = tools.search_knowledge_base(self.retriever, args.get("query", ""))
            for r in result.get("results", []):
                if r["source"] not in reply.sources:
                    reply.sources.append(r["source"])
        elif name == "escalate_to_human":
            result = tools.escalate_to_human(db, session_id, args.get("reason", "other"), args.get("summary", ""),
                                             args.get("priority", "normal"))
            reply.escalated, reply.ticket_id = True, result.get("ticket_id")
        else:
            result = {"ok": False, "error": "unknown_tool"}
        reply.tool_calls.append(ToolCall(name, dict(args), bool(result.get("ok")), result,
                                         round((time.perf_counter() - t0) * 1000, 2)))
        return result

    def _escalate(self, db: Session, reply: AgentReply, session_id: str, reason: str, message: str,
                  priority: str, text: str) -> None:
        reply.intent = "escalation"
        res = self._run_tool(db, reply, "escalate_to_human",
                             {"reason": reason, "summary": guardrails.mask_pii(message)[:500], "priority": priority}, session_id)
        reply.reply = f"{text} Your ticket number is {res['ticket_id']}. {res['message']}"

    # ---- deterministic flow
    def _rules_flow(self, db: Session, reply: AgentReply, message: str, session_id: str, texts: list[str]) -> None:
        intent = classify(message, self.product_words, texts[1:])
        reply.intent = intent
        order_id, email = _find_order_id(texts), _find_email(texts)

        if intent == "greeting":
            reply.reply = ("Hello! I'm OpsPilot, the Nimbus Outfitters assistant. I can check an order, start a return, "
                           "check stock or answer questions about shipping, returns and warranty.")
            return

        if intent == "order_status":
            if not order_id or not email:
                missing = " and ".join(x for x, v in [("your order number (for example OP-1001)", order_id), ("the email used for the order", email)] if not v)
                reply.reply = f"I can check that for you. Please share {missing}."
                return
            r = self._run_tool(db, reply, "get_order_status", {"order_id": order_id, "email": email}, session_id)
            reply.reply = self._order_text(r)
            return

        if intent == "return_request":
            reason = _find_reason(texts)
            if not order_id or not email:
                missing = " and ".join(x for x, v in [("your order number", order_id), ("the email used for the order", email)] if not v)
                reply.reply = f"I can help with a return. Please share {missing}" + ("" if reason else ", and the reason for the return") + "."
                return
            if not reason:
                reply.reply = f"What is the reason for returning order {order_id}? (For example: too small, defective, changed my mind.)"
                return
            r = self._run_tool(db, reply, "create_return_request", {"order_id": order_id, "email": email, "reason": reason}, session_id)
            reply.reply = self._return_text(r)
            if not r.get("ok") and r.get("error") in {"window_closed", "not_delivered", "already_returned"}:
                reply.sources.append("returns-and-refunds.md#Return window")
            return

        if intent == "inventory":
            r = self._run_tool(db, reply, "check_inventory", {"query": message}, session_id)
            reply.reply = self._inventory_text(r)
            return

        # policy question / anything else: answer only from the knowledge base
        r = self._run_tool(db, reply, "search_knowledge_base", {"query": message}, session_id)
        results = r.get("results", [])
        if not results or results[0]["score"] < 2.0:
            reply.intent = "out_of_scope"
            reply.sources.clear()
            reply.reply = ("I can only help with Nimbus Outfitters orders, returns, stock and store policies, "
                           "and I couldn't find an answer to that in our help documents. Would you like me to connect you with a person?")
            return
        top = results[0]
        reply.sources = [top["source"]]
        reply.reply = f"{_best_sentences(top['text'], message)} (Source: {top['title']} › {top['section']})"

    def _order_text(self, r: dict) -> str:
        if not r.get("ok"):
            return r["message"] + (" Please check both and try again." if r.get("error") == "verification_failed" else "")
        items = ", ".join(r["items"])
        s = r["status"]
        if s == "processing":
            return f"Order {r['order_id']} ({items}) is being packed and hasn't shipped yet. You can still cancel or change it."
        if s == "shipped":
            return (f"Order {r['order_id']} ({items}) shipped on {r['shipped_on']} with {r['carrier']}. "
                    f"Tracking number: {r['tracking_number']}. Standard delivery takes 2-3 business days.")
        if s == "delivered":
            window = (f"You can return it until {r['return_eligible_until']}." if r.get("return_window_open")
                      else f"The 30-day return window ended on {r['return_eligible_until']}.")
            return f"Order {r['order_id']} ({items}) was delivered on {r['delivered_on']}. {window}"
        if s == "cancelled":
            return f"Order {r['order_id']} ({items}) was cancelled, and any payment was refunded."
        if s == "returned":
            return f"Order {r['order_id']} ({items}) has been returned and refunded."
        return f"Order {r['order_id']} is {s}."

    def _return_text(self, r: dict) -> str:
        if r.get("ok"):
            fee = "no return fee" if r["return_fee_krw"] == 0 else f"a {_fmt_krw(r['return_fee_krw'])} return fee is deducted"
            return (f"Your return for {r['order_id']} is approved. Return code: {r['rma_code']}. Please drop the parcel at any "
                    f"SwiftPost point by {r['drop_off_by']}. Refund estimate: {_fmt_krw(r['refund_estimate_krw'])} ({fee}), "
                    f"paid 5-7 business days after the item passes inspection.")
        msg = r["message"]
        if r.get("error") == "verification_failed":
            return msg + " Please check both and try again."
        if r.get("error") in {"window_closed", "not_delivered", "already_returned", "needs_review"}:
            return msg + " If you think this is a mistake, I can pass your case to a human agent."
        return msg

    def _inventory_text(self, r: dict) -> str:
        if not r.get("ok"):
            return r["message"] + " You can ask about a product by name or SKU (for example NB-TNT-01)."
        parts = []
        for p in r["products"]:
            head = f"{p['name']} ({p['sku']}, {_fmt_krw(p['price_krw'])})"
            if p["availability"] == "in_stock":
                parts.append(f"{head} is in stock ({p['stock']} available).")
            elif p["availability"] == "low_stock":
                parts.append(f"{head} is low on stock: only {p['stock']} left.")
            else:
                when = f" We expect it back on {p['restock_date']}." if p["restock_date"] else " There is no restock date yet."
                parts.append(f"{head} is out of stock.{when}")
        return " ".join(parts)

    # ---- LLM flow
    def _llm_flow(self, db: Session, reply: AgentReply, message: str, history: list[dict], session_id: str,
                  texts: list[str]) -> None:
        assert self.llm is not None
        user_blob = " ".join(texts).lower()
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        for m in history[-10:]:
            if m.get("role") in {"user", "assistant"} and m.get("content"):
                messages.append({"role": m["role"], "content": str(m["content"])[:1000]})
        messages.append({"role": "user", "content": message})

        for _ in range(self.settings.max_tool_steps):
            msg = self.llm.chat(messages, tools.TOOL_SPECS)
            calls = msg.get("tool_calls") or []
            if not calls:
                reply.reply = (msg.get("content") or "").strip() or "Sorry, I couldn't produce an answer. Would you like a human agent?"
                break
            messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
            for call in calls:
                name = call.get("function", {}).get("name", "")
                try:
                    args = json.loads(call.get("function", {}).get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                blocked = self._block_unprovided_identifiers(name, args, user_blob)
                result = blocked or self._run_tool(db, reply, name, args, session_id)
                if blocked:
                    reply.tool_calls.append(ToolCall(name, dict(args), False, blocked, 0.0))
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(result, default=str)[:4000]})
        else:
            reply.reply = "Sorry, I couldn't finish that request. I can connect you with a human agent."

        used = [tc.name for tc in reply.tool_calls]
        reply.intent = next((INTENT_BY_TOOL[n] for n in reversed(used) if n in INTENT_BY_TOOL and n != "search_knowledge_base"), None) \
            or ("policy_question" if "search_knowledge_base" in used else classify(message, self.product_words, texts[1:]))
        if not used and reply.intent == "policy_question":
            reply.intent = "out_of_scope"

    @staticmethod
    def _block_unprovided_identifiers(name: str, args: dict, user_blob: str) -> dict | None:
        """The model may only use an order number or email that the customer actually typed."""
        if name not in {"get_order_status", "create_return_request"}:
            return None
        email = str(args.get("email", "")).lower().strip()
        oid = tools.normalize_order_id(str(args.get("order_id", "")))
        provided_ids = {f"OP-{n}" for n in tools.ORDER_ID_RE.findall(user_blob)}
        if not email or email not in user_blob or not oid or oid not in provided_ids:
            return {"ok": False, "error": "identifier_not_provided",
                    "message": "Ask the customer for their order number and email; do not guess them."}
        return None
