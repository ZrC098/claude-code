"""SQLite persistence for chains, their history, and processed e-mails."""

import os
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS chains (
    id TEXT PRIMARY KEY,
    wa_message_id TEXT UNIQUE,
    grp TEXT NOT NULL,
    sender TEXT NOT NULL,
    text TEXT NOT NULL,
    level INTEGER NOT NULL,
    status TEXT NOT NULL,          -- open, approved, rejected
    created_at REAL NOT NULL,
    level_sent_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    chain_id TEXT NOT NULL,
    at REAL NOT NULL,
    level INTEGER NOT NULL,
    kind TEXT NOT NULL,            -- sent, reply, timeout, ignored, closed
    actor TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS processed_mail (
    message_id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS seen_groups (
    group_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    last_seen REAL NOT NULL
);
"""


class Store:
    def __init__(self, path):
        if path != ":memory:" and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _exec(self, sql, args=()):
        with self._lock, self._db:
            return self._db.execute(sql, args).fetchall()

    def create_chain(self, wa_message_id, grp, sender, text, now=None):
        """Return the new chain id, or None if this WhatsApp message was already handled."""
        now = now or time.time()
        chain_id = uuid.uuid4().hex[:8]
        try:
            self._exec(
                "INSERT INTO chains VALUES (?,?,?,?,?,0,'open',?,?)",
                (chain_id, wa_message_id, grp, sender, text, now, now),
            )
        except sqlite3.IntegrityError:
            return None
        return chain_id

    def get(self, chain_id):
        rows = self._exec("SELECT * FROM chains WHERE id=?", (chain_id,))
        return rows[0] if rows else None

    def open_chains(self):
        return self._exec("SELECT * FROM chains WHERE status='open'")

    def set_level(self, chain_id, level, now=None):
        self._exec("UPDATE chains SET level=?, level_sent_at=? WHERE id=?",
                   (level, now or time.time(), chain_id))

    def close(self, chain_id, status):
        self._exec("UPDATE chains SET status=? WHERE id=?", (status, chain_id))

    def add_event(self, chain_id, level, kind, actor="", detail="", now=None):
        self._exec("INSERT INTO events VALUES (?,?,?,?,?,?)",
                   (chain_id, now or time.time(), level, kind, actor, detail))

    def events(self, chain_id):
        return self._exec("SELECT * FROM events WHERE chain_id=? ORDER BY at, rowid", (chain_id,))

    def see_group(self, group_id, name, now=None):
        """Remember a group the bot received a message from (see the `groups` command)."""
        self._exec(
            "INSERT INTO seen_groups VALUES (?,?,?) ON CONFLICT(group_id) DO UPDATE SET "
            "name=CASE WHEN excluded.name != '' THEN excluded.name ELSE name END, "
            "last_seen=excluded.last_seen",
            (group_id, name or "", now or time.time()))

    def seen_groups(self):
        return self._exec("SELECT * FROM seen_groups ORDER BY last_seen DESC")

    def mark_mail_processed(self, message_id):
        """Return False if this e-mail was already processed."""
        try:
            self._exec("INSERT INTO processed_mail VALUES (?)", (message_id,))
        except sqlite3.IntegrityError:
            return False
        return True
