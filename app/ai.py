"""Optional AI wording and classification with Claude.

Off unless AI_ENABLED=true. Only masked text is ever passed in here. If a call
fails or is declined, the caller falls back to the fixed rules and templates.
"""
import json

from . import classify as rules

MODEL = "claude-opus-5-5"

_VOICE = {
    "bank": "The reader is a bank's technical team who know coding, SQL and networking. "
    "Write in a technical and professional way and do not explain basics.",
    "stock": "The reader works at a stock company, is not technical, and is strong in accounting. "
    "Write in plain business language: what happened and what it means for their work. "
    "No jargon, no class names, no error codes.",
}
_RULES = (
    "You write WhatsApp replies for a software company's customer support team. Be respectful and "
    "professional. Never guess a cause. Never state how a feature works unless it is in the material "
    "you are given. Never promise a time. Keep text in square brackets such as [NAME] exactly as it is. "
    "Reply in the same language the customer used. Return only the message text."
)


class AIUnavailable(Exception):
    pass


class ClaudeAI:
    def __init__(self):
        import anthropic

        self._client = anthropic.Anthropic()

    def _ask(self, system, user, schema=None):
        output_config = {"effort": "low"}
        if schema:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        try:
            response = self._client.beta.messages.create(
                model=MODEL,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config=output_config,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise AIUnavailable(str(exc))
        if response.stop_reason != "end_turn":
            raise AIUnavailable("stopped with " + str(response.stop_reason))
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if not text:
            raise AIUnavailable("empty reply")
        return text

    def classify(self, masked_text, has_open_ticket):
        schema = {
            "type": "object",
            "properties": {"type": {"type": "string", "enum": rules.TYPES}},
            "required": ["type"],
            "additionalProperties": False,
        }
        system = (
            "Classify one customer support message.\n"
            "ISSUE: something is broken or wrong.\n"
            "LOG_HANDOVER: an issue is raised and a log file path is attached.\n"
            "FEATURE_QUESTION: asking how the system works.\n"
            "FOLLOW_UP: more information or a chaser about a ticket that is already open. "
            "Only valid when an open ticket exists.\n"
            "NEEDS_HUMAN: upset customer, asks for a person, or unclear."
        )
        user = "Open ticket exists: %s\n\nMessage:\n%s" % ("yes" if has_open_ticket else "no", masked_text)
        try:
            return json.loads(self._ask(system, user, schema))["type"]
        except (ValueError, KeyError) as exc:
            raise AIUnavailable(str(exc))

    def feature_answer(self, voice, masked_question, entry):
        user = (
            "Customer question:\n%s\n\nApproved documentation (the only source you may use):\n## %s\n%s\n\n"
            "Answer the question from the documentation." % (masked_question, entry["title"], entry["body"])
        )
        return self._ask(_RULES + "\n\n" + _VOICE[voice], user)

    def root_cause(self, voice, ticket_id, title, cause):
        user = (
            "Ticket %s: %s\n\nRoot cause written by our support team:\n%s\n\n"
            "Write the update to the customer. State only what the support team wrote."
            % (ticket_id, title, cause)
        )
        return self._ask(_RULES + "\n\n" + _VOICE[voice], user)
