"""Decide what kind of message came in, using fixed rules (no AI)."""
import re

ISSUE = "ISSUE"
LOG_HANDOVER = "LOG_HANDOVER"
FEATURE_QUESTION = "FEATURE_QUESTION"
FOLLOW_UP = "FOLLOW_UP"
NEEDS_HUMAN = "NEEDS_HUMAN"
VOICE_NOTE = "VOICE_NOTE"
TYPES = [ISSUE, LOG_HANDOVER, FEATURE_QUESTION, FOLLOW_UP, NEEDS_HUMAN]

_HUMAN = re.compile(
    r"(speak|talk) (to|with) (a |some)?(person|someone|human|manager|agent)|call me|complain|"
    r"unacceptable|escalate|lawyer|legal action",
    re.IGNORECASE,
)
_FOLLOW = re.compile(
    r"any update|update on|follow(ing)? up|still waiting|any news|status of|as requested|here is the|attached is",
    re.IGNORECASE,
)
_ISSUE = re.compile(
    r"error|exception|fail|issue|problem|cannot|can't|unable|stuck|slow|long time|\bdown\b|"
    r"not working|timeout|timed out|wrong|incorrect|missing|crash|hang",
    re.IGNORECASE,
)
_FEATURE = re.compile(
    r"^\s*(how|can|could|does|do|is there|what|where|which|may i)\b|how to|is it possible|\?",
    re.IGNORECASE,
)
_LOG_PATH = re.compile(r"(?:[A-Za-z]:)?[\\/][\w.\\/ -]*?\.(?:log|txt|zip|gz)\b")
_EXCEPTION = re.compile(r"\b((?:[a-zA-Z_][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error))\b")


def log_paths(text):
    """File paths in the message, with stray spaces after a slash removed."""
    return [re.sub(r"([\\/])\s+", r"\1", p) for p in _LOG_PATH.findall(text)]


def exceptions(text):
    return sorted(set(_EXCEPTION.findall(text)))


def classify(text, known_customer, open_ticket_id=None):
    if not known_customer or not text.strip() or _HUMAN.search(text):
        return NEEDS_HUMAN
    if open_ticket_id and (open_ticket_id.lower() in text.lower() or _FOLLOW.search(text)):
        return FOLLOW_UP
    if log_paths(text):
        return LOG_HANDOVER
    if _ISSUE.search(text):
        return ISSUE
    if _FEATURE.search(text):
        return FEATURE_QUESTION
    return NEEDS_HUMAN
