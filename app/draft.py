"""Reply wording. Two voices:

bank:  technical and professional. They know coding, SQL and networking.
stock: business language for accounting people. Impact first, no jargon.

Replies never guess a cause and never promise anything faster than the settle time.
"""

AFTER_HOURS = " This message arrived outside our working hours, so the team will pick it up on the next working day."


def _join(*parts):
    return " ".join(p.strip() for p in parts if p and p.strip())


def acknowledgement(voice, ticket_id, priority, settle_text, log_path="", after_hours=False):
    if voice == "bank":
        log = " and received the log at " + log_path if log_path else ""
        level = " (" + priority.lower() + ")" if priority != "TO CONFIRM" else ""
        text = _join(
            "Hello, noted. I have raised ticket %s%s for this%s." % (ticket_id, level, log),
            "Our support team is checking and I will update you once we have the cause.",
            settle_text,
        )
    else:
        text = _join(
            "Hello, thank you for letting us know. We have logged this as case %s and our support team is looking into it." % ticket_id,
            settle_text,
            "We will update you as soon as we know more.",
        )
    return text + (AFTER_HOURS if after_hours else "")


def receipt(after_hours=False):
    """Sent when the ticket could not be created yet. Makes no claim about a ticket."""
    return "Hello, thank you for your message. We have received it and will come back to you shortly." + (
        AFTER_HOURS if after_hours else ""
    )


def follow_up(voice, ticket_id):
    if voice == "bank":
        return "Hello, noted. I have added this to ticket %s and the support team has been informed." % ticket_id
    return "Hello, thank you. We have added this to case %s and our support team has been informed." % ticket_id


def question_received(after_hours=False):
    return "Hello, thank you for your question. We are checking and will reply to you shortly." + (
        AFTER_HOURS if after_hours else ""
    )


def handoff():
    return "Hello, thank you for your message. A colleague from our support team will respond to you directly."


def voice_note():
    return "Hello, thank you for your message. We are unable to process voice notes. Could you please send the details as text?"


def feature_answer(entry):
    return "Hello, thank you for your question.\n\n%s\n\nPlease let us know if you need anything further." % entry["body"]


def feature_no_answer():
    return "Hello, thank you for your question. I am confirming the details with our team and will reply to you once I have the answer."


def root_cause(voice, ticket_id, title, cause):
    if voice == "bank":
        return (
            "Hello, update on ticket %s.\n\nRoot cause: %s\n\n"
            "Please let us know if you have any further questions." % (ticket_id, cause)
        )
    return (
        "Hello, we have an update on case %s.\n\nOur support team has completed the investigation. "
        "The reason was: %s\n\nPlease let us know if you have any further questions." % (ticket_id, cause)
    )


def support_email(ticket, customer, original_text):
    subject = "[%s] %s %s: %s" % (ticket["priority"], ticket["ext_id"], customer["company"], ticket["title"])
    body = (
        "A customer enquiry needs investigation.\n\n"
        "Ticket:      %s\nCustomer:    %s (%s customer)\nPriority:    %s\nEnvironment: %s\nSettle by:   %s\n\n"
        "Customer message (sensitive details masked):\n%s\n\n%s\n"
        "When the root cause is known, post it on the ticket. A reply to the customer will then be "
        "drafted for approval."
        % (
            ticket["ext_id"],
            customer["company"],
            customer["type"],
            ticket["priority"],
            ticket["environment"] or "not stated",
            ticket["settle_by"] or "to confirm",
            original_text,
            ticket["body"],
        )
    )
    return subject, body
