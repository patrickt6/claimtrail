"""The watch store: every tool output and user message, with the numbers in it.

One SQLite file for the whole machine, so no project needs any setup.
Default ``~/.claimtrail/watch.sqlite``; override with ``CLAIMTRAIL_WATCH_DB``.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from claimtrail.quantities import extract_quantities

MAX_TEXT = 200_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    session TEXT, cwd TEXT, ts TEXT,
    kind TEXT,          -- 'tool' or 'user'
    tool TEXT, label TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS nums (
    source_id INTEGER, value REAL, offset INTEGER, raw TEXT
);
CREATE INDEX IF NOT EXISTS nums_value ON nums(value);
CREATE INDEX IF NOT EXISTS sources_scope ON sources(cwd, ts);
CREATE TABLE IF NOT EXISTS docs (
    path TEXT PRIMARY KEY, session TEXT, cwd TEXT, ts TEXT, text TEXT, report TEXT
);
"""


def db_path() -> Path:
    return Path(os.environ.get("CLAIMTRAIL_WATCH_DB", Path.home() / ".claimtrail" / "watch.sqlite"))


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def add_source(conn, *, session, cwd, kind, tool, label, text) -> int | None:
    text = (text or "")[:MAX_TEXT]
    qs = extract_quantities(text, skip_years=False)
    if not qs:
        return None
    cur = conn.execute(
        "INSERT INTO sources(session, cwd, ts, kind, tool, label, text) VALUES (?,?,?,?,?,?,?)",
        (session, cwd, now(), kind, tool, (label or "")[:500], text),
    )
    sid = cur.lastrowid
    conn.executemany(
        "INSERT INTO nums(source_id, value, offset, raw) VALUES (?,?,?,?)",
        [(sid, q.value, q.offset, q.raw_text) for q in qs],
    )
    conn.commit()
    return sid
