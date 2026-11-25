"""Input / output guardrails: prompt-injection screening, PII redaction, system-prompt leak detection."""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass, field

# ---------------------------------------------------------------- prompt injection
# (pattern, weight). Weight >= 2 on its own blocks; weaker signals add up.
_INJECTION_PATTERNS: list[tuple[re.Pattern, int]] = [
    (
        re.compile(
            r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|your|system)\b.{0,30}\b(instructions?|rules|prompts?|guidelines)\b",
            re.I | re.S,
        ),
        3,
    ),
    (
        re.compile(
            r"\b(reveal|print|show|repeat|output|leak)\b.{0,40}\b(system|hidden|initial|developer)\b.{0,20}\b(prompt|instructions?|message)\b",
            re.I | re.S,
        ),
        3,
    ),
    (re.compile(r"\byou are (now|no longer)\b", re.I), 2),
    (re.compile(r"\b(developer|god|jailbreak|dan)\s+mode\b", re.I), 3),
    (re.compile(r"\bdo anything now\b", re.I), 3),
    (
        re.compile(
            r"\bact as (an? )?(admin|administrator|root|support agent|staff|system)\b", re.I
        ),
        2,
    ),
    (re.compile(r"\bpretend (you are|to be)\b", re.I), 1),
    (re.compile(r"</?\s*(system|assistant|tool)\s*>", re.I), 2),
    (
        re.compile(
            r"\b(bypass|disable)\b.{0,30}\b(approval|guardrails?|safety|security|filters?)\b",
            re.I | re.S,
        ),
        2,
    ),
    (re.compile(r"\b(drop|truncate|delete from|alter)\s+table\b", re.I), 2),
    (
        re.compile(
            r"\ball (customers|users)('s)?\b.{0,30}\b(emails?|data|records|passwords?)\b",
            re.I | re.S,
        ),
        1,
    ),
]
INJECTION_BLOCK_THRESHOLD = 2


def injection_score(text: str) -> int:
    return sum(w for p, w in _INJECTION_PATTERNS if p.search(text))


# ---------------------------------------------------------------- PII
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")
_PASSWORD = re.compile(r"(?i)\b(password|passcode|pwd)\b\s*[:=]\s*\S+")
_CVV = re.compile(r"(?i)\b(cvv|cvc|security code)\b\s*[:=]?\s*\d{3,4}\b")


def _luhn_ok(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact payment cards (Luhn-checked), SSNs, IBANs, passwords and CVVs."""
    found: list[str] = []

    def card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            found.append("payment_card")
            return "[REDACTED_CARD]"
        return m.group(0)

    text = _CARD_CANDIDATE.sub(card, text)
    for name, pattern, repl in (
        ("ssn", _SSN, "[REDACTED_SSN]"),
        ("iban", _IBAN, "[REDACTED_IBAN]"),
        ("password", _PASSWORD, r"\1: [REDACTED]"),
        ("cvv", _CVV, r"\1 [REDACTED]"),
    ):
        text, n = pattern.subn(repl, text)
        if n:
            found.append(name)
    return text, found


# ---------------------------------------------------------------- results
@dataclass
class InputCheck:
    allowed: bool
    text: str
    flags: list[str] = field(default_factory=list)
    refusal: str | None = None


def check_input(text: str, max_chars: int) -> InputCheck:
    flags: list[str] = []
    if not text or not text.strip():
        return InputCheck(
            False, text, ["empty_input"], "It looks like your message was empty - how can I help?"
        )
    if len(text) > max_chars:
        return InputCheck(
            False,
            text,
            ["input_too_long"],
            f"Your message is too long ({len(text)} characters). Please keep it under {max_chars}.",
        )
    score = injection_score(text)
    if score >= INJECTION_BLOCK_THRESHOLD:
        return InputCheck(
            False,
            text,
            [f"prompt_injection(score={score})"],
            "I can't help with that request. I'm Acme Cloud's support assistant - I can help with "
            "billing, your account, tickets and product questions.",
        )
    if score:
        flags.append(f"injection_signal(score={score})")
    redacted, pii = redact_pii(text)
    flags += [f"pii_redacted:{p}" for p in pii]
    return InputCheck(True, redacted, flags)


@dataclass
class OutputCheck:
    text: str
    flags: list[str] = field(default_factory=list)


LEAK_REPLACEMENT = (
    "Sorry, I can't share that. Is there anything else I can help you with regarding "
    "your Acme Cloud account?"
)


def check_output(text: str, canary: str) -> OutputCheck:
    flags: list[str] = []
    if canary and canary in text:
        return OutputCheck(LEAK_REPLACEMENT, ["system_prompt_leak"])
    redacted, pii = redact_pii(text)
    flags += [f"output_pii_redacted:{p}" for p in pii]
    return OutputCheck(redacted, flags)


def new_canary() -> str:
    return f"CANARY-{secrets.token_hex(6)}"
