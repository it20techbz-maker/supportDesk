"""Remove sensitive details from customer text before it is stored in a ticket,
shown in a reply, or sent to an AI service."""
import re

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b\d{6}-\d{2}-\d{4}\b"), "[ID]"),
    (re.compile(r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b"), "[CARD]"),
    (re.compile(r"\+\d[\d -]{7,14}\d"), "[PHONE]"),
    (re.compile(r"\b\d{8,}\b"), "[ACCOUNT]"),
]


def mask(text, names=()):
    """Return text with emails, ID numbers, card, phone and account numbers
    replaced, plus any of the given personal names."""
    for pattern, label in _PATTERNS:
        text = pattern.sub(label, text)
    for name in names:
        if name and name.strip():
            text = re.sub(r"\b" + re.escape(name.strip()) + r"\b", "[NAME]", text, flags=re.IGNORECASE)
    return text
