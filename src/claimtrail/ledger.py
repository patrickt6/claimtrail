"""Append-only verification log: who re-checked a result, where, and when.

A recorded computation says what a run produced. It does not say whether
anyone has checked it since. Every ``claimtrail verify`` appends one entry
to this log with the verifier, the machine, the code version, and the
outcome. Entries are never edited or deleted.

Two properties make the log useful as evidence:

- **Independence.** Each computation records who ran it (``recorded_by``).
  A verification by the same actor is a self-check. A verification by a
  different actor is independent. Computations recorded before this field
  existed have an unknown author, so their checks are never counted as
  independent.
- **Tamper evidence.** Each entry stores the hash of the previous entry,
  so editing, deleting or reordering any entry breaks the chain, and
  ``claimtrail verifications --check-chain`` reports where. SQLite triggers
  also refuse UPDATE and DELETE through normal use.

Limits, stated plainly: the verifier name comes from configuration
(``CLAIMTRAIL_ACTOR``, else git ``user.email``, else ``user@host``). It is
attribution, not authentication. The hash chain makes changes evident; it
does not prevent someone with write access from rebuilding the whole
chain. Anchor the latest ``entry_hash`` somewhere you do not control alone
(a commit, a CI log, a ticket) when that matters.
"""
from __future__ import annotations

import dataclasses
import functools
import getpass
import hashlib
import json
import os
import platform
import socket
import subprocess
from typing import Literal, Optional

from .store import Computation, Store, get_store, utc_now_iso

Result = Literal["match", "mismatch", "error"]
GENESIS = "0" * 32

SCHEMA = """
CREATE TABLE IF NOT EXISTS verifications (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    computation_id  TEXT NOT NULL,
    verified_at     TEXT NOT NULL,
    verifier        TEXT NOT NULL,
    hostname        TEXT,
    python_version  TEXT,
    code_sha        TEXT,
    method          TEXT NOT NULL,
    result          TEXT NOT NULL CHECK (result IN ('match', 'mismatch', 'error')),
    expected_hash   TEXT,
    actual_hash     TEXT,
    message         TEXT,
    prev_hash       TEXT NOT NULL,
    entry_hash      TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_verifications_comp ON verifications(computation_id);
CREATE TRIGGER IF NOT EXISTS verifications_append_only_update
    BEFORE UPDATE ON verifications
    BEGIN SELECT RAISE(ABORT, 'verifications is append-only'); END;
CREATE TRIGGER IF NOT EXISTS verifications_append_only_delete
    BEFORE DELETE ON verifications
    BEGIN SELECT RAISE(ABORT, 'verifications is append-only'); END;
"""
# No foreign key to computations on purpose: `claimtrail gc` may delete a
# computation, and the history of checks against it must survive that.

_HASHED_FIELDS = (
    "computation_id", "verified_at", "verifier", "hostname", "python_version",
    "code_sha", "method", "result", "expected_hash", "actual_hash", "message",
)


@dataclasses.dataclass(frozen=True)
class Verification:
    seq: int
    computation_id: str
    verified_at: str
    verifier: str
    hostname: Optional[str]
    python_version: Optional[str]
    code_sha: Optional[str]
    method: str
    result: Result
    expected_hash: Optional[str]
    actual_hash: Optional[str]
    message: Optional[str]
    prev_hash: str
    entry_hash: str


@dataclasses.dataclass(frozen=True)
class Standing:
    """How far a computation's result has been checked."""

    status: Literal["independent", "self-checked", "author-unknown", "unverified", "failed"]
    matches: int
    independent_matches: int
    last: Optional[Verification]

    def __str__(self) -> str:
        return self.status


def current_actor() -> str:
    """Who is acting: ``CLAIMTRAIL_ACTOR``, else git user.email, else user@host."""
    env = os.environ.get("CLAIMTRAIL_ACTOR")
    if env:
        return env.strip()
    return _default_actor()


@functools.lru_cache(maxsize=1)
def _default_actor() -> str:
    try:
        out = subprocess.run(
            ["git", "config", "user.email"], capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        user = getpass.getuser()
    except Exception:
        user = "unknown"
    return f"{user}@{socket.gethostname()}"


def _entry_hash(prev_hash: str, fields: dict) -> str:
    body = json.dumps({k: fields.get(k) for k in _HASHED_FIELDS}, sort_keys=True, separators=(",", ":"))
    h = hashlib.blake2b(digest_size=16)
    h.update(prev_hash.encode())
    h.update(b"|")
    h.update(body.encode())
    return h.hexdigest()


def append(
    computation_id: str,
    *,
    result: Result,
    method: str,
    expected_hash: str | None = None,
    actual_hash: str | None = None,
    message: str | None = None,
    verifier: str | None = None,
    code_sha: str | None = None,
    store: Store | None = None,
) -> Verification:
    """Append one verification entry and return it."""
    store = store or get_store()
    fields = {
        "computation_id": computation_id,
        "verified_at": utc_now_iso(),
        "verifier": verifier or current_actor(),
        "hostname": socket.gethostname(),
        "python_version": platform.python_version(),
        "code_sha": code_sha,
        "method": method,
        "result": result,
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
        "message": message,
    }
    with store._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT entry_hash FROM verifications ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        prev = row["entry_hash"] if row else GENESIS
        entry = _entry_hash(prev, fields)
        cur = conn.execute(
            f"INSERT INTO verifications ({', '.join(_HASHED_FIELDS)}, prev_hash, entry_hash) "
            f"VALUES ({', '.join('?' * (len(_HASHED_FIELDS) + 2))})",
            [fields[k] for k in _HASHED_FIELDS] + [prev, entry],
        )
        seq = cur.lastrowid
    return Verification(seq=seq, prev_hash=prev, entry_hash=entry, **fields)


def entries(computation_id: str | None = None, *, store: Store | None = None) -> list[Verification]:
    store = store or get_store()
    sql = "SELECT * FROM verifications"
    params: tuple = ()
    if computation_id:
        sql += " WHERE computation_id = ?"
        params = (computation_id,)
    sql += " ORDER BY seq ASC"
    with store._connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [Verification(**{k: r[k] for k in r.keys()}) for r in rows]


def check_chain(store: Store | None = None) -> list[str]:
    """Return one message per broken link; an empty list means intact."""
    problems: list[str] = []
    prev = GENESIS
    for v in entries(store=store):
        if v.prev_hash != prev:
            problems.append(f"entry {v.seq}: prev_hash does not match entry {v.seq - 1}")
        expected = _entry_hash(v.prev_hash, dataclasses.asdict(v))
        if v.entry_hash != expected:
            problems.append(f"entry {v.seq}: contents changed after it was written")
        prev = v.entry_hash
    return problems


def standing(comp: Computation, *, store: Store | None = None) -> Standing:
    """Summarize the log for one computation.

    The latest entry decides ``failed``. Otherwise a match by someone other
    than ``recorded_by`` makes the result ``independent``.
    """
    log = entries(comp.id, store=store)
    matches = [v for v in log if v.result == "match"]
    independent = [
        v for v in matches if comp.recorded_by and v.verifier != comp.recorded_by
    ]
    last = log[-1] if log else None
    if last is not None and last.result != "match":
        status = "failed"
    elif independent:
        status = "independent"
    elif matches and comp.recorded_by is None:
        status = "author-unknown"
    elif matches:
        status = "self-checked"
    else:
        status = "unverified"
    return Standing(status, len(matches), len(independent), last)


__all__ = [
    "Verification",
    "Standing",
    "append",
    "entries",
    "check_chain",
    "standing",
    "current_actor",
]
