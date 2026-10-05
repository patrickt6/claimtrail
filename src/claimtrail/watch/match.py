"""Match each number in a document to the tool output or user message it came from.

Statuses:
  traced   the number is in a tool output (a command, a file read, a fetch)
  stated   the number is in something the user typed
  near     no exact source, but a source has a value within 5 percent
  unfound  no source has it. That means "not found", not "false"
  ignored  small integers (0 to 10), which match by chance too often
"""
from __future__ import annotations

import bisect
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from claimtrail.quantities import extract_quantities

WINDOW_DAYS = 14
NEAR = 0.05


@dataclass
class Hit:
    raw: str
    value: float
    offset: int
    line: int
    status: str
    source_id: int | None = None
    source_value: float | None = None
    source_offset: int | None = None


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope(conn, doc_path: str, cwd: str, session: str):
    """Sources this document may draw on: this session, plus recent work in the same folder,
    plus anything the user typed recently. A read of the document itself never counts."""
    rows = conn.execute(
        """SELECT id, kind, label FROM sources
           WHERE session = ? OR (cwd = ? AND ts >= ?) OR (kind = 'user' AND ts >= ?)""",
        (session, cwd, _since(WINDOW_DAYS), _since(WINDOW_DAYS * 2)),
    ).fetchall()
    name = Path(doc_path).name
    keep = {r["id"]: r["kind"] for r in rows if not (r["kind"] == "tool" and name and name in (r["label"] or ""))}
    if not keep:
        return [], {}
    marks = ",".join("?" * len(keep))
    nums = conn.execute(
        f"SELECT source_id, value, offset FROM nums WHERE source_id IN ({marks})", list(keep)
    ).fetchall()
    table = sorted((n["value"], -n["source_id"], n["offset"]) for n in nums)  # newest source first on ties
    return table, keep


def check(conn, text: str, *, doc_path: str, cwd: str, session: str) -> list[Hit]:
    table, kinds = _scope(conn, doc_path, cwd, session)
    values = [t[0] for t in table]
    hits: list[Hit] = []
    for q in extract_quantities(text):
        line = text.count("\n", 0, q.offset) + 1
        hit = Hit(q.raw_text, q.value, q.offset, line, "unfound")
        if q.kind == "int" and abs(q.value) <= 10:
            hit.status = "ignored"
            hits.append(hit)
            continue
        best = None  # (rank, source) where rank 0 = tool exact, 1 = user exact, 2 = near
        for target in q.candidates():
            scale = 1.0 if target == q.value else 0.01
            tol = q.tolerance * scale + 1e-12
            lo = bisect.bisect_left(values, target - max(tol, abs(target) * NEAR))
            hi = bisect.bisect_right(values, target + max(tol, abs(target) * NEAR))
            for v, neg_sid, off in table[lo:hi]:
                sid = -neg_sid
                exact = abs(v - target) <= tol
                rank = (0 if kinds[sid] == "tool" else 1) if exact else 2
                dist = abs(v - target)
                if best is None or (rank, dist) < best[0]:
                    best = ((rank, dist), (sid, v, off))
        if best:
            rank = best[0][0]
            hit.status = ("traced", "stated", "near")[rank]
            hit.source_id, hit.source_value, hit.source_offset = best[1]
        hits.append(hit)
    return hits


def summary(hits: list[Hit]) -> dict:
    out = {"traced": 0, "stated": 0, "near": 0, "unfound": 0, "ignored": 0}
    for h in hits:
        out[h.status] += 1
    return out


def to_json(hits: list[Hit]) -> str:
    return json.dumps([asdict(h) for h in hits])
