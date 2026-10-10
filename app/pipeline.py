"""The support flow: message in, ticket raised, team emailed, replies drafted and approved.

Only acknowledgements are sent automatically. Feature answers and root-cause
replies wait in the approval queue until a person approves them.
"""
import datetime
import json

from . import classify as C
from . import draft, priority
from .ai import AIUnavailable
from .masking import mask

SENT = "sent"
PENDING = "pending_approval"
REJECTED = "rejected"
FAILED = "failed"
WINDOW = datetime.timedelta(hours=24)


class Pipeline:
    def __init__(self, store, customers, knowledge, whatsapp, ticketing, email, support_email,
                 ai=None, clock=datetime.datetime.now, work_hours=(9, 18)):
        self.store = store
        self.customers = customers
        self.knowledge = knowledge
        self.whatsapp = whatsapp
        self.ticketing = ticketing
        self.email = email
        self.support_email = support_email
        self.ai = ai
        self.clock = clock
        self.work_hours = work_hours

    # ---- incoming customer message ----

    def handle_incoming(self, phone, text, voice_note=False, attachments=()):
        now = self.clock()
        customer = self.customers.get(phone)
        masked = mask(text, names=[customer["contact"]] if customer else [])
        open_ticket = self._open_ticket(phone)

        if customer and voice_note:
            kind = C.VOICE_NOTE
        else:
            kind = self._classify(masked, bool(customer), open_ticket)
        inbound_id = self.store.run(
            "insert into inbound (ts, phone, text, masked, type, ticket_id) values (?, ?, ?, ?, ?, ?)",
            (_iso(now), phone, text, masked, kind, open_ticket["id"] if kind == C.FOLLOW_UP else None),
        )
        self.store.audit(_iso(now), "message_received", "%s classified %s" % (phone, kind))

        if kind == C.VOICE_NOTE:
            self._send(phone, "voice_note", draft.voice_note(), None, now)
        elif kind == C.NEEDS_HUMAN:
            self._hand_off(phone, customer, masked, now)
        elif kind == C.FOLLOW_UP:
            self._follow_up(phone, customer, masked, open_ticket, attachments, now)
        elif kind == C.FEATURE_QUESTION:
            self._feature_question(phone, customer, masked, now)
        else:
            self._raise_ticket(inbound_id, phone, customer, masked, list(attachments), now, retry=False)
        return {"inbound_id": inbound_id, "type": kind}

    def _classify(self, masked, known, open_ticket):
        open_id = open_ticket["ext_id"] if open_ticket else None
        if self.ai and known:
            try:
                kind = self.ai.classify(masked, bool(open_ticket))
                if kind != C.FOLLOW_UP or open_ticket:
                    return kind
            except AIUnavailable as exc:
                self.store.audit(_iso(self.clock()), "ai_fallback", "classify: %s" % exc)
        return C.classify(masked, known, open_id)

    def _open_ticket(self, phone):
        return self.store.one(
            "select * from tickets where phone = ? and status in ('open', 'root_cause_received') "
            "order by id desc limit 1",
            (phone,),
        )

    def _hand_off(self, phone, customer, masked, now):
        self._send(phone, "handoff", draft.handoff(), None, now)
        who = customer["company"] if customer else "Unknown number " + phone
        self._email(
            "[ACTION] Customer needs a person: " + who,
            "This message was not handled automatically. Please reply to the customer directly.\n\n"
            "From: %s\n\nMessage (sensitive details masked):\n%s" % (who, masked),
            now,
        )

    def _follow_up(self, phone, customer, masked, ticket, attachments, now):
        note = masked + ("\nAttachments: " + ", ".join(attachments) if attachments else "")
        try:
            self.ticketing.add_note(ticket["ext_id"], note)
        except Exception as exc:
            self.store.audit(_iso(now), "ticket_note_failed", "%s: %s" % (ticket["ext_id"], exc))
        self.store.run("update tickets set body = body || ? where id = ?", ("\n\nFOLLOW-UP " + _iso(now) + ":\n" + note, ticket["id"]))
        self._send(phone, "follow_up", draft.follow_up(customer["type"], ticket["ext_id"]), ticket["id"], now)
        self._email(
            "[UPDATE] %s %s: customer sent more information" % (ticket["ext_id"], customer["company"]),
            "The customer added to ticket %s.\n\n%s" % (ticket["ext_id"], note),
            now,
        )

    def _feature_question(self, phone, customer, masked, now):
        self._send(phone, "question_received", draft.question_received(self._after_hours(now)), None, now)
        entry = self.knowledge.search(masked)
        if entry:
            body = draft.feature_answer(entry)
            if self.ai:
                try:
                    body = self.ai.feature_answer(customer["type"], masked, entry)
                except AIUnavailable as exc:
                    self.store.audit(_iso(now), "ai_fallback", "feature_answer: %s" % exc)
            self._queue(phone, "feature_answer", body, None, now)
        else:
            self._queue(phone, "feature_answer", draft.feature_no_answer(), None, now)
            self._email(
                "[QUESTION] %s asked about a feature that is not in the approved documentation" % customer["company"],
                "No approved answer was found. Please advise, then edit the draft reply in the approval queue.\n\n"
                "Question (sensitive details masked):\n" + masked,
                now,
            )

    def _raise_ticket(self, inbound_id, phone, customer, masked, attachments, now, retry):
        level = priority.detect(masked)
        paths = C.log_paths(masked)
        errors = C.exceptions(masked)
        title = " ".join(masked.split())[:90]
        settle_by = ""
        if level["settle_days"]:
            settle_by = (now + datetime.timedelta(days=level["settle_days"])).date().isoformat()
        body = "\n".join(
            [
                "SYMPTOM: " + title,
                "ERROR / CLASS: " + (", ".join(errors) or "not given by customer"),
                "LOG PATH: " + (", ".join(paths) or "none"),
                "ATTACHMENTS: " + (", ".join(attachments) or "none"),
            ]
        )
        ticket = {
            "company": customer["company"], "title": title, "body": body, "priority": level["priority"],
            "environment": level["environment"], "settle_by": settle_by,
        }
        try:
            ext_id = self.ticketing.create(ticket)
        except Exception as exc:
            self.store.audit(_iso(now), "ticket_failed", "inbound %s: %s" % (inbound_id, exc))
            if not retry:
                # Never tell the customer a ticket exists when it does not.
                self._send(phone, "receipt", draft.receipt(self._after_hours(now)), None, now)
                self.store.run(
                    "insert into pending (ts, inbound_id, kind, attachments) values (?, ?, ?, ?)",
                    (_iso(now), inbound_id, "ticket", json.dumps(attachments)),
                )
            return None
        ticket_id = self.store.run(
            "insert into tickets (ext_id, ts, phone, company, title, body, priority, environment, settle_by, status) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')",
            (ext_id, _iso(now), phone, ticket["company"], title, body, ticket["priority"], ticket["environment"], settle_by),
        )
        self.store.run("update inbound set ticket_id = ? where id = ?", (ticket_id, inbound_id))
        self.store.audit(_iso(now), "ticket_created", "%s %s" % (ext_id, ticket["priority"]))
        self._send(
            phone, "ack",
            draft.acknowledgement(customer["type"], ext_id, level["priority"], level["settle_text"],
                                  paths[0] if paths else "", self._after_hours(now)),
            ticket_id, now,
        )
        subject, text = draft.support_email(dict(ticket, ext_id=ext_id), customer, masked)
        if level["priority"] == "TO CONFIRM":
            text = "PRIORITY NOT SET: the message does not say PROD or UAT. Please set it on the ticket.\n\n" + text
        self._email(subject, text, now)
        return ticket_id

    # ---- support team and approvers ----

    def post_root_cause(self, ticket_id, cause, posted_by):
        """The support team has found the cause. Draft the customer reply for approval."""
        now = self.clock()
        ticket = self.store.one("select * from tickets where id = ?", (ticket_id,))
        if not ticket:
            raise KeyError("no such ticket")
        customer = self.customers.get(ticket["phone"])
        self.store.run("update tickets set status = 'root_cause_received', root_cause = ? where id = ?", (cause, ticket_id))
        self.store.audit(_iso(now), "root_cause_posted", "%s by %s" % (ticket["ext_id"], posted_by))
        body = draft.root_cause(customer["type"], ticket["ext_id"], ticket["title"], cause)
        if self.ai:
            try:
                body = self.ai.root_cause(customer["type"], ticket["ext_id"], ticket["title"], mask(cause))
            except AIUnavailable as exc:
                self.store.audit(_iso(now), "ai_fallback", "root_cause: %s" % exc)
        return self._queue(ticket["phone"], "root_cause", body, ticket_id, now)

    def approve(self, outbox_id, approver, body=None):
        """Send a queued draft, optionally edited. Returns how it was sent."""
        now = self.clock()
        row = self._pending_draft(outbox_id)
        text = body.strip() if body and body.strip() else row["body"]
        last = self.store.one("select max(ts) as ts from inbound where phone = ?", (row["phone"],))
        # WhatsApp only allows free text within 24 hours of the customer's last message.
        template = not last["ts"] or now - datetime.datetime.fromisoformat(last["ts"]) > WINDOW
        self.whatsapp.send(row["phone"], text, template=template)
        via = "template" if template else "freeform"
        self.store.run(
            "update outbox set body = ?, status = ?, approved_by = ?, sent_ts = ?, via = ? where id = ?",
            (text, SENT, approver, _iso(now), via, outbox_id),
        )
        if row["kind"] == "root_cause":
            self.store.run("update tickets set status = 'replied' where id = ?", (row["ticket_id"],))
        self.store.audit(_iso(now), "draft_approved", "outbox %s by %s via %s" % (outbox_id, approver, via))
        return via

    def reject(self, outbox_id, approver):
        now = self.clock()
        self._pending_draft(outbox_id)
        self.store.run("update outbox set status = ?, approved_by = ? where id = ?", (REJECTED, approver, outbox_id))
        self.store.audit(_iso(now), "draft_rejected", "outbox %s by %s" % (outbox_id, approver))

    def _pending_draft(self, outbox_id):
        row = self.store.one("select * from outbox where id = ?", (outbox_id,))
        if not row or row["status"] != PENDING:
            raise KeyError("no draft waiting for approval with that id")
        return row

    def retry_pending(self):
        """Try again to raise tickets that failed. Returns how many succeeded."""
        now = self.clock()
        done = 0
        for item in self.store.all("select * from pending order by id"):
            inbound = self.store.one("select * from inbound where id = ?", (item["inbound_id"],))
            customer = self.customers.get(inbound["phone"])
            ticket_id = self._raise_ticket(
                inbound["id"], inbound["phone"], customer, inbound["masked"], json.loads(item["attachments"]), now, retry=True
            )
            if ticket_id:
                self.store.run("delete from pending where id = ?", (item["id"],))
                done += 1
            else:
                self.store.run("update pending set attempts = attempts + 1 where id = ?", (item["id"],))
        return done

    def reminders(self):
        """Open tickets with no root cause that are due within a day, or overdue."""
        limit = (self.clock() + datetime.timedelta(days=1)).date().isoformat()
        return self.store.all(
            "select * from tickets where status = 'open' and settle_by != '' and settle_by <= ? order by settle_by",
            (limit,),
        )

    def stats(self):
        by_type = self.store.all("select type, count(*) as n from inbound group by type")
        by_status = self.store.all("select status, count(*) as n from tickets group by status")
        return {
            "messages_by_type": {r["type"]: r["n"] for r in by_type},
            "tickets_by_status": {r["status"]: r["n"] for r in by_status},
            "awaiting_approval": self.store.one("select count(*) as n from outbox where status = ?", (PENDING,))["n"],
            "waiting_for_ticket_system": self.store.one("select count(*) as n from pending")["n"],
        }

    # ---- helpers ----

    def _after_hours(self, now):
        start, end = self.work_hours
        return now.weekday() >= 5 or not (start <= now.hour < end)

    def _send(self, phone, kind, body, ticket_id, now):
        status = SENT
        try:
            self.whatsapp.send(phone, body)
        except Exception as exc:
            status = FAILED
            self.store.audit(_iso(now), "whatsapp_failed", "%s to %s: %s" % (kind, phone, exc))
        return self.store.run(
            "insert into outbox (ts, phone, kind, body, status, ticket_id, approved_by, sent_ts, via) "
            "values (?, ?, ?, ?, ?, ?, 'automatic', ?, 'freeform')",
            (_iso(now), phone, kind, body, status, ticket_id, _iso(now)),
        )

    def _queue(self, phone, kind, body, ticket_id, now):
        return self.store.run(
            "insert into outbox (ts, phone, kind, body, status, ticket_id) values (?, ?, ?, ?, ?, ?)",
            (_iso(now), phone, kind, body, PENDING, ticket_id),
        )

    def _email(self, subject, body, now):
        try:
            self.email.send(self.support_email, subject, body)
        except Exception as exc:
            self.store.audit(_iso(now), "email_failed", "%s: %s" % (subject, exc))
            return
        self.store.run(
            "insert into emails (ts, recipient, subject, body) values (?, ?, ?, ?)",
            (_iso(now), self.support_email, subject, body),
        )


def _iso(moment):
    return moment.isoformat(timespec="seconds")
