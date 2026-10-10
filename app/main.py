"""Web service and test console.

Phase 1: every outside connection is a fake. Nothing is sent to WhatsApp, the
ticketing system or email. Messages are typed into the test console instead.
"""
import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .connectors.email import FakeEmail
from .connectors.ticketing import FakeTicketing
from .connectors.whatsapp import BaileysWhatsApp, FakeWhatsApp, WhatsAppUnavailable
from .customers import Customers
from .knowledge import Knowledge
from .pipeline import Pipeline
from .store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_pipeline(db_path=None):
    ai = None
    if os.environ.get("AI_ENABLED", "false").lower() == "true":
        from .ai import ClaudeAI

        ai = ClaudeAI()
    return Pipeline(
        store=Store(db_path or os.environ.get("DB_PATH", os.path.join(ROOT, "data", "support.db"))),
        customers=Customers(os.environ.get("CUSTOMERS_FILE", os.path.join(ROOT, "data", "customers.json"))),
        knowledge=Knowledge(os.environ.get("KNOWLEDGE_DIR", os.path.join(ROOT, "knowledge"))),
        whatsapp=_whatsapp(),
        ticketing=FakeTicketing(),
        email=FakeEmail(),
        support_email=os.environ.get("SUPPORT_EMAIL", "support-team@example.com"),
        ai=ai,
    )


def _whatsapp():
    if os.environ.get("WHATSAPP_MODE", "fake").lower() != "baileys":
        return FakeWhatsApp()
    token = os.environ.get("BRIDGE_TOKEN", "")
    if not token:
        raise RuntimeError("WHATSAPP_MODE=baileys needs BRIDGE_TOKEN to be set")
    return BaileysWhatsApp(os.environ.get("BRIDGE_URL", "http://127.0.0.1:8766"), token)


def create_app(pipeline=None):
    app = FastAPI(title="Support Desk")
    p = pipeline or build_pipeline()
    # Continue ticket numbers after the ones already stored, so a restart does not reuse them.
    for row in p.store.all("select ext_id from tickets"):
        p.ticketing.tickets.setdefault(row["ext_id"], {})

    class Incoming(BaseModel):
        phone: str
        text: str = ""
        voice_note: bool = False
        attachments: List[str] = []

    class RootCause(BaseModel):
        cause: str
        posted_by: str = "support team"

    class Decision(BaseModel):
        approver: str
        body: Optional[str] = None

    class Switch(BaseModel):
        down: bool

    @app.get("/")
    def console():
        return FileResponse(os.path.join(ROOT, "app", "static", "console.html"))

    @app.post("/api/incoming")
    def incoming(msg: Incoming):
        return p.handle_incoming(msg.phone, msg.text, msg.voice_note, msg.attachments)

    @app.post("/api/tickets/{ticket_id}/root-cause")
    def root_cause(ticket_id: int, body: RootCause):
        if not body.cause.strip():
            raise HTTPException(400, "Root cause is empty")
        try:
            return {"outbox_id": p.post_root_cause(ticket_id, body.cause.strip(), body.posted_by)}
        except KeyError as exc:
            raise HTTPException(404, str(exc))

    @app.post("/api/outbox/{outbox_id}/approve")
    def approve(outbox_id: int, body: Decision):
        if not body.approver.strip():
            raise HTTPException(400, "Approver name is required")
        try:
            return {"via": p.approve(outbox_id, body.approver.strip(), body.body)}
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        except WhatsAppUnavailable as exc:
            raise HTTPException(502, "Not sent, the draft is still waiting: %s" % exc)

    @app.post("/api/outbox/{outbox_id}/reject")
    def reject(outbox_id: int, body: Decision):
        if not body.approver.strip():
            raise HTTPException(400, "Approver name is required")
        try:
            p.reject(outbox_id, body.approver.strip())
        except KeyError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    @app.post("/api/ticketing-down")
    def ticketing_down(body: Switch):
        p.ticketing.down = body.down
        return {"down": p.ticketing.down}

    @app.post("/api/retry")
    def retry():
        return {"raised": p.retry_pending()}

    @app.get("/api/state")
    def state():
        s = p.store
        return {
            "customers": [dict(v, phone=k) for k, v in p.customers._by_phone.items()],
            "inbound": s.all("select * from inbound order by id desc limit 50"),
            "outbox": s.all("select * from outbox order by id desc limit 100"),
            "tickets": s.all("select * from tickets order by id desc limit 50"),
            "emails": s.all("select * from emails order by id desc limit 50"),
            "audit": s.all("select * from audit order by id desc limit 50"),
            "reminders": p.reminders(),
            "stats": p.stats(),
            "ticketing_down": p.ticketing.down,
            "ai_enabled": p.ai is not None,
            "whatsapp_live": isinstance(p.whatsapp, BaileysWhatsApp),
        }

    return app


app = create_app() if os.environ.get("SUPPORT_DESK_NO_APP") != "1" else None
