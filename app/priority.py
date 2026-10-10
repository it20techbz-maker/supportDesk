"""Priority comes from the environment named in the message.

PROD or PRD means URGENT and has to settle in 3-5 days.
UAT means MODERATE and has to settle in 7 days.
Anything else is not guessed: a person sets it.
"""
import re

_PROD = re.compile(r"\b(prod|prd|production)\b", re.IGNORECASE)
_UAT = re.compile(r"\buat\b", re.IGNORECASE)

UNKNOWN = {"priority": "TO CONFIRM", "environment": "", "settle_days": None, "settle_text": ""}


def detect(text):
    if _PROD.search(text):
        return {
            "priority": "URGENT",
            "environment": "PROD",
            "settle_days": 5,
            "settle_text": "We target to settle production issues within 3-5 days.",
        }
    if _UAT.search(text):
        return {
            "priority": "MODERATE",
            "environment": "UAT",
            "settle_days": 7,
            "settle_text": "We target to settle UAT issues within 7 days.",
        }
    return dict(UNKNOWN)
