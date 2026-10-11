"""Input and output guardrails.

Input checks run before the agent (rules mode and LLM mode alike), so prompt-injection attempts and
requests the assistant is not allowed to fulfil never reach a tool. Output checks redact any email
address that the verified customer did not provide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CARD_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


@dataclass(frozen=True)
class GuardResult:
    action: str          # allow | refuse | escalate
    category: str = "none"
    message: str = ""
    priority: str = "normal"


INJECTION = [
    r"ignore (all |any |the )?(previous|prior|above|earlier|your) (instructions|rules|prompt)",
    r"disregard (all |the )?(previous|prior|above) ", r"\bsystem prompt\b", r"reveal (your|the) (prompt|instructions)",
    r"you are now\b", r"developer mode", r"\bjailbreak\b", r"pretend (you are|to be) (an? )?(admin|developer|manager)",
    r"act as (an? )?(admin|administrator|developer|root)", r"\bas an admin\b", r"admin (access|mode|override)",
]
EXFILTRATION = [
    r"\b(list|show|give|send|export|dump)\b.{0,25}\b(all|every|other)\b.{0,15}\b(orders|customers|users|emails)\b",
    r"\ball (the )?(orders|customers|users)\b", r"(someone|somebody|another (customer|person|user))('s| else's)? (order|email|address|details)",
    r"\b(email|address|phone|name) (address )?(of|for) (the )?(customer|person|buyer|owner)\b",
    r"\bwho (placed|ordered|bought)\b", r"\b(database|sql|select \*|drop table|union select)\b",
]
UNAUTHORIZED = [
    r"refund\b.{0,30}\bwithout\b.{0,20}\breturn", r"\b(100|full) ?% (discount|refund|off)",
    r"\b(give|create|generate|make)\b.{0,15}\b(discount|coupon|voucher|promo)",
    r"\bfree (order|item|product|jacket|tent|shoes)\b", r"\b(change|lower|reduce|edit)\b.{0,15}\bprice\b",
    r"\bmark\b.{0,25}\bas (delivered|refunded|returned)\b", r"\bwaive\b", r"\bextend\b.{0,20}\b(return )?window\b",
    r"\bcancel\b.{0,25}\b(someone|another|other customer)",
]
SENSITIVE = [r"\b(cvc|cvv|password|passcode|one[- ]time (code|password)|otp)\b"]
LEGAL = [r"\b(lawyer|attorney|legal action|sue|lawsuit|court|consumer (agency|board|protection)|police)\b"]
HUMAN = [r"\b(talk|speak|chat) (to|with) (a |an )?(human|person|agent|manager|representative|someone real)\b",
         r"\breal (person|human)\b", r"\bhuman (agent|support)\b", r"\bcall me\b"]
ANGER = [r"\b(scam|fraud|furious|disgusting|worst|unacceptable|ridiculous|useless|terrible service)\b"]


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


def check_input(message: str, max_chars: int = 1000) -> GuardResult:
    text = message.lower()
    if len(message) > max_chars:
        return GuardResult("refuse", "too_long", f"Please keep messages under {max_chars} characters.")
    if CARD_RE.search(message) or _any(SENSITIVE, text):
        return GuardResult("refuse", "sensitive_data",
                           "Please don't share card numbers, passwords or one-time codes here. "
                           "We never need them in chat. To look up an order I only need the order number and your email.")
    if _any(INJECTION, text):
        return GuardResult("refuse", "prompt_injection",
                           "I can't change how I work or share internal instructions. "
                           "I can help with orders, returns, stock and store policies.")
    if _any(EXFILTRATION, text):
        return GuardResult("refuse", "data_protection",
                           "I can't share other customers' information or bulk order data. "
                           "I can look up your own order with your order number and email.")
    if _any(LEGAL, text):
        return GuardResult("escalate", "legal", "I'm sorry this has happened. I'm passing your case to a member of our team.", "high")
    if _any(UNAUTHORIZED, text):
        return GuardResult("refuse", "unauthorized_action",
                           "I'm not able to do that: prices, discounts, refunds and return windows follow fixed store policy. "
                           "If you think your case needs an exception, I can pass it to a human agent.")
    if _any(HUMAN, text):
        return GuardResult("escalate", "human_requested", "Of course. I'm handing this conversation to a member of our support team.")
    if _any(ANGER, text):
        return GuardResult("escalate", "upset_customer", "I'm sorry for the trouble. I'm passing this to a member of our team so a person can help you directly.", "high")
    return GuardResult("allow")


def mask_pii(text: str) -> str:
    """For logs: keep the first letter and domain of emails, hide card-like numbers."""
    text = CARD_RE.sub("[card]", text)
    return EMAIL_RE.sub(lambda m: m.group(0)[0] + "***@" + m.group(0).split("@", 1)[1], text)


def redact_output(text: str, allowed_emails: set[str]) -> str:
    """Remove any email address from a reply unless the customer provided it."""
    allowed = {e.lower() for e in allowed_emails}
    return EMAIL_RE.sub(lambda m: m.group(0) if m.group(0).lower() in allowed else "[redacted]", text)
