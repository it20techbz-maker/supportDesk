"""SQLite storage for messages, tickets, replies, emails and the audit log."""
import sqlite3
import threading

_SCHEMA = """
create table if not exists inbound (
  id integer primary key, ts text, phone text, text text, masked text, type text, ticket_id integer);
create table if not exists tickets (
  id integer primary key, ext_id text, ts text, phone text, company text, title text, body text,
  priority text, environment text, settle_by text, status text, root_cause text);
create table if not exists outbox (
  id integer primary key, ts text, phone text, kind text, body text, status text,
  ticket_id integer, approved_by text, sent_ts text, via text);
create table if not exists emails (
  id integer primary key, ts text, recipient text, subject text, body text);
create table if not exists pending (
  id integer primary key, ts text, inbound_id integer, kind text, attachments text, attempts integer default 0);
create table if not exists audit (
  id integer primary key, ts text, event text, detail text);
"""


class Store:
    def __init__(self, path):
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)

    def run(self, sql, args=()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.lastrowid

    def all(self, sql, args=()):
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def audit(self, ts, event, detail=""):
        self.run("insert into audit (ts, event, detail) values (?, ?, ?)", (ts, event, detail))
