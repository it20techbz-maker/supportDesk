import datetime
import os

import pytest

os.environ["SUPPORT_DESK_NO_APP"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app import classify as C  # noqa: E402
from app import priority  # noqa: E402
from app.connectors.email import FakeEmail  # noqa: E402
from app.connectors.ticketing import FakeTicketing  # noqa: E402
from app.connectors.whatsapp import FakeWhatsApp  # noqa: E402
from app.customers import Customers  # noqa: E402
from app.knowledge import Knowledge  # noqa: E402
from app.main import create_app  # noqa: E402
from app.masking import mask  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.store import Store  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANK = "+60120000001"
STOCK = "+60120000002"
UT67890 = (
    "Please check this date : 02/10/2026, production issue happen that cron job : UT67890 run long time\n"
    "Log in path on the share uploaded server:\n/A_Bank/Log/Prod/ UT67890.log"
)


class Clock:
    def __init__(self):
        self.now = datetime.datetime(2026, 10, 5, 10, 0, 0)  # Monday, working hours

    def __call__(self):
        return self.now


@pytest.fixture
def p(tmp_path):
    return Pipeline(
        store=Store(str(tmp_path / "test.db")),
        customers=Customers(os.path.join(ROOT, "data", "customers.json")),
        knowledge=Knowledge(os.path.join(ROOT, "knowledge")),
        whatsapp=FakeWhatsApp(),
        ticketing=FakeTicketing(),
        email=FakeEmail(),
        support_email="support@test",
        clock=Clock(),
    )


def test_masking_removes_sensitive_details():
    out = mask("Adam here, acct 1234567890, card 4111 1111 1111 1111, ic 900101-07-1234, a@b.com, +60 12-345 6789", ["Adam"])
    for secret in ["Adam", "1234567890", "4111", "900101", "a@b.com", "345 6789"]:
        assert secret not in out
    assert "UT67890" in mask("job UT67890 on 02/10/2026 at 2026-10-02 02:10:00")
    assert "2026-10-02 02:10:00" in mask("2026-10-02 02:10:00 INFO started")


def test_priority_rule():
    assert priority.detect("issue on PRD server")["priority"] == "URGENT"
    assert priority.detect("/A_Bank/Log/Prod/x.log")["settle_days"] == 5
    assert priority.detect("found in uat")["priority"] == "MODERATE"
    assert priority.detect("the product page is slow")["priority"] == "TO CONFIRM"


def test_classification_rules():
    assert C.classify(UT67890, True) == C.LOG_HANDOVER
    assert C.log_paths(UT67890) == ["/A_Bank/Log/Prod/UT67890.log"]
    assert C.classify("The UAT login page shows an error", True) == C.ISSUE
    assert C.classify("How do I export the settlement report?", True) == C.FEATURE_QUESTION
    assert C.classify("Any update on this?", True, "TCK-0001") == C.FOLLOW_UP
    assert C.classify("This is unacceptable, let me speak to a manager", True) == C.NEEDS_HUMAN
    assert C.classify("The UAT login page shows an error", False) == C.NEEDS_HUMAN
    assert C.classify("ok thanks", True) == C.NEEDS_HUMAN


def test_bank_log_handover_raises_urgent_ticket_and_acknowledges(p):
    result = p.handle_incoming(BANK, UT67890)
    assert result["type"] == C.LOG_HANDOVER
    ticket = p.store.one("select * from tickets")
    assert ticket["priority"] == "URGENT" and ticket["settle_by"] == "2026-10-10"
    assert "/A_Bank/Log/Prod/UT67890.log" in ticket["body"]
    ack = p.whatsapp.sent[0]["text"]
    assert "TCK-0001" in ack and "3-5 days" in ack and "UT67890.log" in ack
    assert "[URGENT] TCK-0001 A Bank" in p.email.sent[0]["subject"]


def test_stock_customer_gets_business_voice_and_name_is_masked(p):
    p.handle_incoming(STOCK, "Mei Ling here. The UAT month end report total is wrong.")
    ack = p.whatsapp.sent[0]["text"]
    assert "case TCK-0001" in ack and "7 days" in ack and "ticket" not in ack
    assert "Mei Ling" not in p.store.one("select * from tickets")["title"]
    assert "Mei Ling" not in p.email.sent[0]["body"]


def test_no_environment_means_priority_is_not_guessed(p):
    p.handle_incoming(BANK, "The transfer screen shows an error")
    assert p.store.one("select * from tickets")["priority"] == "TO CONFIRM"
    assert "days" not in p.whatsapp.sent[0]["text"]
    assert "PRIORITY NOT SET" in p.email.sent[0]["body"]


def test_follow_up_joins_open_ticket(p):
    p.handle_incoming(BANK, UT67890)
    result = p.handle_incoming(BANK, "Any update on TCK-0001? Job ran slow again last night")
    assert result["type"] == C.FOLLOW_UP
    assert p.store.one("select count(*) as n from tickets")["n"] == 1
    assert "FOLLOW-UP" in p.store.one("select * from tickets")["body"]
    assert p.ticketing.notes[0]["ticket"] == "TCK-0001"


def test_feature_answer_waits_for_approval(p):
    p.handle_incoming(BANK, "How do I export the daily settlement report to Excel?")
    assert len(p.whatsapp.sent) == 1 and "checking" in p.whatsapp.sent[0]["text"]
    draft = p.store.one("select * from outbox where status = 'pending_approval'")
    assert "Daily Settlement" in draft["body"]
    assert p.approve(draft["id"], "Yvonne") == "freeform"
    assert len(p.whatsapp.sent) == 2
    with pytest.raises(KeyError):
        p.approve(draft["id"], "Yvonne")


def test_unknown_feature_is_never_answered_from_guesswork(p):
    p.handle_incoming(BANK, "Can the dashboard show foreign currency positions?")
    draft = p.store.one("select * from outbox where status = 'pending_approval'")
    assert "confirming the details" in draft["body"]
    assert "not in the approved documentation" in p.email.sent[0]["subject"]


def test_unknown_number_and_upset_customer_go_to_a_person(p):
    p.handle_incoming("+60199999999", "PROD is down")
    p.handle_incoming(BANK, "This is unacceptable. Let me speak to a manager.")
    assert p.store.one("select count(*) as n from tickets")["n"] == 0
    assert all("colleague" in m["text"] for m in p.whatsapp.sent)
    assert len(p.email.sent) == 2


def test_voice_note_asks_for_text(p):
    assert p.handle_incoming(BANK, "", voice_note=True)["type"] == C.VOICE_NOTE
    assert "voice notes" in p.whatsapp.sent[0]["text"]


def test_ticket_system_down_never_claims_a_ticket_then_retries(p):
    p.ticketing.down = True
    p.handle_incoming(BANK, UT67890)
    assert "TCK" not in p.whatsapp.sent[0]["text"] and "ticket" not in p.whatsapp.sent[0]["text"]
    assert p.stats()["waiting_for_ticket_system"] == 1
    assert p.retry_pending() == 0 and len(p.whatsapp.sent) == 1
    p.ticketing.down = False
    assert p.retry_pending() == 1
    assert "TCK-0001" in p.whatsapp.sent[1]["text"]
    assert p.stats()["waiting_for_ticket_system"] == 0


def test_root_cause_is_drafted_approved_and_uses_template_after_24_hours(p):
    p.handle_incoming(BANK, UT67890)
    ticket = p.store.one("select * from tickets")
    draft_id = p.post_root_cause(ticket["id"], "A lock wait timeout on the settlement table delayed step 3.", "Devi")
    assert len(p.whatsapp.sent) == 1  # nothing sent before approval
    p.clock.now += datetime.timedelta(days=2)
    assert p.approve(draft_id, "Yvonne", "Edited reply") == "template"
    assert p.whatsapp.sent[-1] == {"phone": BANK, "text": "Edited reply", "template": True}
    assert p.store.one("select * from tickets")["status"] == "replied"
    assert p.store.one("select * from outbox where id = ?", (draft_id,))["approved_by"] == "Yvonne"


def test_rejected_draft_is_not_sent(p):
    p.handle_incoming(BANK, UT67890)
    draft_id = p.post_root_cause(p.store.one("select * from tickets")["id"], "cause", "Devi")
    p.reject(draft_id, "Yvonne")
    assert len(p.whatsapp.sent) == 1


def test_reminder_when_settle_date_is_close(p):
    p.handle_incoming(BANK, UT67890)
    assert p.reminders() == []
    p.clock.now += datetime.timedelta(days=4)
    assert [t["ext_id"] for t in p.reminders()] == ["TCK-0001"]


def test_after_hours_wording(p):
    p.clock.now = datetime.datetime(2026, 10, 3, 22, 0, 0)  # Saturday night
    p.handle_incoming(BANK, UT67890)
    assert "next working day" in p.whatsapp.sent[0]["text"]


def test_web_api_end_to_end(p):
    client = TestClient(create_app(p))
    assert client.get("/").status_code == 200
    assert client.post("/api/incoming", json={"phone": BANK, "text": UT67890}).json()["type"] == C.LOG_HANDOVER
    state = client.get("/api/state").json()
    ticket_id = state["tickets"][0]["id"]
    assert state["emails"] and state["stats"]["tickets_by_status"] == {"open": 1}
    draft_id = client.post("/api/tickets/%d/root-cause" % ticket_id, json={"cause": "Lock wait timeout"}).json()["outbox_id"]
    assert client.post("/api/outbox/%d/approve" % draft_id, json={"approver": ""}).status_code == 400
    assert client.post("/api/outbox/%d/approve" % draft_id, json={"approver": "Yvonne"}).json() == {"via": "freeform"}
    assert client.post("/api/outbox/999/approve", json={"approver": "Yvonne"}).status_code == 404


def test_whatsapp_failure_is_recorded_and_does_not_lose_the_ticket(p):
    def broken(phone, text, template=False):
        raise RuntimeError("bridge down")

    p.whatsapp.send = broken
    p.handle_incoming(BANK, UT67890)
    assert p.store.one("select * from tickets")["ext_id"] == "TCK-0001"
    assert p.store.one("select * from outbox")["status"] == "failed"
    assert len(p.email.sent) == 1


def test_baileys_connector_posts_to_bridge_and_reports_refusals():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from app.connectors.whatsapp import BaileysWhatsApp, WhatsAppUnavailable

    seen = []

    class Bridge(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers["X-Bridge-Token"], body))
            self.send_response(200 if body["phone"] == BANK else 403)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Bridge)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:%d" % server.server_port
        BaileysWhatsApp(url, "secret").send(BANK, "hello")
        assert seen == [("/send", "secret", {"phone": BANK, "text": "hello"})]
        with pytest.raises(WhatsAppUnavailable):
            BaileysWhatsApp(url, "secret").send(STOCK, "hello")
    finally:
        server.shutdown()
    with pytest.raises(WhatsAppUnavailable):
        BaileysWhatsApp(url, "secret", timeout=2).send(BANK, "hello")


def test_console_requires_sign_in_when_a_password_is_set(p, monkeypatch):
    client = TestClient(create_app(p, password="s3cret"))
    assert client.get("/").status_code == 401
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", auth=("admin", "wrong")).status_code == 401
    assert client.get("/api/state", auth=("admin", "s3cret")).status_code == 200
    body = {"phone": BANK, "text": UT67890}
    assert client.post("/api/incoming", json=body, headers={"X-Bridge-Token": "tok"}).status_code == 401
    monkeypatch.setenv("BRIDGE_TOKEN", "tok")
    assert client.post("/api/incoming", json=body, headers={"X-Bridge-Token": "bad"}).status_code == 401
    assert client.post("/api/incoming", json=body, headers={"X-Bridge-Token": "tok"}).status_code == 200
    assert client.get("/api/state", headers={"X-Bridge-Token": "tok"}).status_code == 401


def test_on_vercel_refuses_to_run_without_database_and_password(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CONSOLE_PASSWORD", raising=False)
    response = TestClient(create_app()).get("/api/state")
    assert response.status_code == 503
    assert "DATABASE_URL" in response.json()["detail"] and "CONSOLE_PASSWORD" in response.json()["detail"]


def test_vercel_entry_point_exposes_the_app():
    import importlib.util

    spec = importlib.util.spec_from_file_location("vercel_entry", os.path.join(ROOT, "api", "index.py"))
    module = importlib.util.module_from_spec(spec)
    os.environ["SUPPORT_DESK_NO_APP"] = "1"
    spec.loader.exec_module(module)
    assert hasattr(module, "app")


@pytest.fixture(scope="module")
def postgres_url(tmp_path_factory):
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    yield server.get_uri()
    server.cleanup()


def test_full_flow_on_postgres(postgres_url):
    from app.store import PostgresStore

    store = PostgresStore(postgres_url)
    PostgresStore(postgres_url)  # creating the tables twice must be harmless
    for table in ("inbound", "tickets", "outbox", "emails", "pending", "audit", "settings"):
        store.run("delete from " + table)
    pg = Pipeline(
        store=store,
        customers=Customers(os.path.join(ROOT, "data", "customers.json")),
        knowledge=Knowledge(os.path.join(ROOT, "knowledge")),
        whatsapp=FakeWhatsApp(),
        ticketing=FakeTicketing(store),
        email=FakeEmail(),
        support_email="support@test",
        clock=Clock(),
    )
    pg.ticketing.down = True
    pg.handle_incoming(BANK, UT67890)
    assert pg.stats()["waiting_for_ticket_system"] == 1
    pg.ticketing.down = False
    assert pg.retry_pending() == 1
    ticket = store.one("select * from tickets")
    assert ticket["ext_id"] == "TCK-0001" and ticket["priority"] == "URGENT"
    assert pg.handle_incoming(BANK, "Any update on TCK-0001?")["type"] == C.FOLLOW_UP
    assert "FOLLOW-UP" in store.one("select * from tickets")["body"]
    pg.handle_incoming(STOCK, "The UAT month end report total is wrong.")
    assert store.one("select * from tickets where phone = ?", (STOCK,))["ext_id"] == "TCK-0002"
    draft_id = pg.post_root_cause(ticket["id"], "Lock wait timeout", "Devi")
    assert pg.approve(draft_id, "Yvonne") == "freeform"
    assert store.one("select * from tickets where id = ?", (ticket["id"],))["status"] == "replied"
    assert [t["ext_id"] for t in pg.reminders()] == []
    assert pg.stats()["tickets_by_status"] == {"replied": 1, "open": 1}
    rls = store.all("select relname from pg_class where relrowsecurity and relname in ('tickets', 'inbound', 'outbox')")
    assert len(rls) == 3


def test_bad_database_url_is_explained_after_sign_in_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/postgres?connect_timeout=2")
    client = TestClient(create_app(password="s3cret"))
    assert client.get("/api/state").status_code == 401
    response = client.get("/api/state", auth=("admin", "s3cret"))
    assert response.status_code == 503
    assert "Could not connect to the database" in response.json()["detail"]
    assert "x@" not in response.json()["detail"]
