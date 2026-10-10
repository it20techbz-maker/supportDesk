"""Web service and test console.

Phase 1: the ticketing system and email are fakes, and WhatsApp is a fake unless
the Baileys bridge is switched on. Messages can be typed into the test console.

Runs on one machine with SQLite, or on Vercel with a Supabase (Postgres) database.
"""
import base64
import os
import secrets
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from .connectors.email import FakeEmail
from .connectors.ticketing import FakeTicketing
from .connectors.whatsapp import BaileysWhatsApp, FakeWhatsApp, WhatsAppUnavailable
from .customers import Customers
from .knowledge import Knowledge
from .pipeline import Pipeline
from .store import PostgresStore, Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_pipeline(db_path=None):
    ai = None
    if os.environ.get("AI_ENABLED", "false").lower() == "true":
        from .ai import ClaudeAI

        ai = ClaudeAI()
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url and not db_path:
        store = PostgresStore(database_url)
    else:
        store = Store(db_path or os.environ.get("DB_PATH", os.path.join(ROOT, "data", "support.db")))
    return Pipeline(
        store=store,
        customers=Customers(os.environ.get("CUSTOMERS_FILE", os.path.join(ROOT, "data", "customers.json"))),
        knowledge=Knowledge(os.environ.get("KNOWLEDGE_DIR", os.path.join(ROOT, "knowledge"))),
        whatsapp=_whatsapp(),
        ticketing=FakeTicketing(store),
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


def _missing_settings():
    """On Vercel the console is on the public internet, so it must have a database and a password."""
    if not os.environ.get("VERCEL"):
        return []
    return [name for name in ("DATABASE_URL", "CONSOLE_PASSWORD") if not os.environ.get(name)]


def _explain_startup(exc):
    reason = " ".join(str(exc).split())[:300]
    if os.environ.get("DATABASE_URL"):
        return (
            "Could not connect to the database. Check DATABASE_URL: copy it again from your own Supabase "
            "project (Connect > Transaction pooler, port 6543), replace [YOUR-PASSWORD] with the database "
            "password, save it in Vercel and redeploy. Database said: " + reason
        )
    return "Could not start: %s: %s" % (type(exc).__name__, reason)


def _authorised(request, user, password):
    bridge_token = os.environ.get("BRIDGE_TOKEN", "")
    if request.url.path == "/api/incoming" and bridge_token:
        if secrets.compare_digest(request.headers.get("x-bridge-token", ""), bridge_token):
            return True
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        given_user, _, given_password = base64.b64decode(encoded).decode("utf-8").partition(":")
    except ValueError:
        return False
    return secrets.compare_digest(given_user, user) and secrets.compare_digest(given_password, password)


def create_app(pipeline=None, password=None):
    app = FastAPI(title="Support Desk", docs_url=None, redoc_url=None, openapi_url=None)
    missing = [] if pipeline else _missing_settings()
    p, startup_error = pipeline, ""
    if not p and not missing:
        try:
            p = build_pipeline()
        except Exception as exc:
            # Keep serving, so the reason is shown on the page instead of a bare crash.
            startup_error = _explain_startup(exc)
    user = os.environ.get("CONSOLE_USER", "admin")
    password = password if password is not None else os.environ.get("CONSOLE_PASSWORD", "")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if missing:
            return JSONResponse(
                {"detail": "Not configured. Set these environment variables in Vercel and redeploy: " + ", ".join(missing)},
                status_code=503,
            )
        if password and not _authorised(request, user, password):
            return Response("Sign in required", status_code=401, headers={"WWW-Authenticate": 'Basic realm="Support Desk"'})
        if startup_error:
            return JSONResponse({"detail": startup_error}, status_code=503)
        return await call_next(request)

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
