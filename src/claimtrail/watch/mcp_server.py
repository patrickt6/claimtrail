"""`claimtrail mcp`: the watch store as an MCP server, for any MCP client.

The tools read the same ``~/.claimtrail/watch.sqlite`` that the Claude Code hook writes,
so a client with the hook (Claude Code) and a client without it (Cursor, Claude Desktop)
see the same sources and the same verdicts as the ``claimtrail watch`` page.

Trust rule: text that an agent passes in is stored as a report, the same as a subagent
report, and never makes a number traced. Only text that claimtrail reads itself (a file
it opens in ``record_file``, or a tool output the hook captured) can trace a number.

Stdio carries JSON-RPC, so nothing in this module may print to stdout.
"""
from __future__ import annotations

import json
from pathlib import Path

from claimtrail.watch import db, hook, match, server

# Checks from MCP have no hook session; scope them by folder only. This id matches no source.
NO_SESSION = "mcp:check"
MAX_HITS = 300

INSTRUCTIONS = """claimtrail checks where each number in a document came from.
After you write or edit a report that has numbers in it, call check_document on it.
Status meanings: traced = in a command output or a file that claimtrail read;
stated = the user typed it; reported = only in agent-written text; near = within 5 percent
of a source but not equal; unfound = no recorded source has it.
"unfound" does not mean false. Do not delete or change a number only because it is unfound:
compute it with a tool, or tell the user which numbers are still open.
If your client has no claimtrail hook, call record_file on each data or results file you
used, before you check the document. The project folder is the nearest one with .git or
.claimtrail-watch; otherwise pass project_dir as its absolute path. check_text,
trace_number and record_note always need project_dir."""


ROOT_MARKERS = (".git", hook.MARKER)


def _project(path: Path | None, project_dir: str | None) -> str:
    """The folder whose recorded work a document may draw on: project_dir if given, else the
    nearest folder above the file that holds .git or .claimtrail-watch."""
    if project_dir:
        return str(Path(project_dir).expanduser().resolve())
    if path is not None:
        for d in path.parents:
            if any((d / m).exists() for m in ROOT_MARKERS):
                return str(d)
    raise ValueError("cannot tell the project folder: pass project_dir as its absolute path")


def _excerpt(text: str, off: int, width: int = 160) -> str:
    a, b = max(0, off - width), min(len(text), off + width)
    return ("..." if a else "") + text[a:b] + ("..." if b < len(text) else "")


def _hit_rows(conn, hits: list[match.Hit]) -> list[dict]:
    ids = {h.source_id for h in hits if h.source_id}
    rows = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT id, kind, tool, label, ts, text FROM sources WHERE id IN ({marks})", list(ids)):
            rows[r["id"]] = r
    out = []
    for h in sorted(hits, key=lambda h: h.offset):
        if h.status == "ignored":
            continue
        item = {"number": h.raw, "line": h.line, "status": h.status}
        r = rows.get(h.source_id)
        if r:
            item["source"] = {
                "kind": r["kind"], "tool": r["tool"], "label": r["label"], "recorded_at": r["ts"],
                "value": h.source_value, "excerpt": _excerpt(r["text"], h.source_offset or 0),
            }
        out.append(item)
    return out


def _result(conn, hits: list[match.Hit], **extra) -> dict:
    rows = _hit_rows(conn, hits)
    counts = match.summary(hits)
    counts.pop("ignored", None)
    open_ = [r for r in rows if r["status"] in ("unfound", "near")]
    return {**extra, "counts": counts, "open": open_[:MAX_HITS], "numbers": rows[:MAX_HITS],
            "truncated": len(rows) > MAX_HITS}


# Tool bodies are plain functions so tests can call them without a client.

def check_document(path: str, project_dir: str | None = None) -> dict:
    """Check every number in a document file against recorded sources, and save the
    result so the claimtrail watch page shows it."""
    conn = db.connect()
    try:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"error": f"no such file: {p}"}
        row = conn.execute("SELECT session, cwd FROM docs WHERE path = ?", (str(p),)).fetchone()
        if row and row["session"] != NO_SESSION:
            session, cwd = row["session"], row["cwd"]  # the hook saw it: keep the hook's scope
        else:
            session, cwd = NO_SESSION, _project(p, project_dir)
        if p.suffix.lower() in hook.DOC_EXT:
            hook.check_doc(conn, str(p), session, cwd)
            hits = [match.Hit(**h) for h in json.loads(
                conn.execute("SELECT report FROM docs WHERE path = ?", (str(p),)).fetchone()["report"])]
        else:
            text = p.read_text(encoding="utf-8", errors="replace")
            hits = match.check(conn, text, doc_path=str(p), cwd=cwd, session=session)
        return _result(conn, hits, path=str(p), project_dir=cwd)
    finally:
        conn.close()


def check_text(text: str, project_dir: str) -> dict:
    """Check the numbers in a draft before it is written to a file. Nothing is saved."""
    conn = db.connect()
    try:
        cwd = _project(None, project_dir)
        hits = match.check(conn, text, doc_path="", cwd=cwd, session=NO_SESSION)
        return _result(conn, hits, project_dir=cwd)
    finally:
        conn.close()


def trace_number(number: str, project_dir: str) -> dict:
    """Find the source of one number as written, for example "86%", "0.8604" or "1,204"."""
    conn = db.connect()
    try:
        cwd = _project(None, project_dir)
        hits = [h for h in match.check(conn, number, doc_path="", cwd=cwd, session=NO_SESSION)
                if h.status != "ignored"]
        if not hits:
            return {"number": number, "status": "no number found, or a small integer (0 to 10) that is not checked"}
        return {**_hit_rows(conn, hits[:1])[0], "project_dir": cwd}
    finally:
        conn.close()


def record_file(path: str, project_dir: str | None = None) -> dict:
    """Read a data or results file and record its contents as a source. claimtrail reads
    the file itself, so its numbers count as traced."""
    conn = db.connect()
    try:
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return {"error": f"no such file: {p}"}
        text = p.read_text(encoding="utf-8", errors="replace")
        cwd = _project(p, project_dir)
        sid = db.add_source(conn, session="mcp:" + cwd, cwd=cwd, kind="tool",
                            tool="record_file", label=str(p), text=text)
        n = conn.execute("SELECT COUNT(*) FROM nums WHERE source_id = ?", (sid,)).fetchone()[0] if sid else 0
        return {"recorded": bool(sid), "path": str(p), "numbers": n}
    finally:
        conn.close()


def record_note(text: str, label: str, project_dir: str) -> dict:
    """Record text that you were given, such as a pasted table or another agent's answer.
    It counts as reported, never as traced, because claimtrail cannot see where it came from."""
    conn = db.connect()
    try:
        cwd = _project(None, project_dir)
        sid = db.add_source(conn, session="mcp:" + cwd, cwd=cwd, kind="report",
                            tool="record_note", label=label, text=text)
        n = conn.execute("SELECT COUNT(*) FROM nums WHERE source_id = ?", (sid,)).fetchone()[0] if sid else 0
        return {"recorded": bool(sid), "numbers": n, "counts_as": "reported"}
    finally:
        conn.close()


def list_documents(limit: int = 20) -> list[dict]:
    """The documents claimtrail has checked, newest first, with their counts."""
    conn = db.connect()
    try:
        return server.docs_list(conn)[:max(1, min(limit, 200))]
    finally:
        conn.close()


def export_html(path: str, out: str | None = None) -> dict:
    """Write one self-contained HTML page of a checked document: every number coloured by
    status, and a click shows its source. Good to send to a reviewer."""
    result = check_document(path)
    if "error" in result:
        return result
    conn = db.connect()
    try:
        page = server.doc_html(conn, result["path"])
    finally:
        conn.close()
    if page is None:
        return {"error": f"only {', '.join(sorted(hook.DOC_EXT))} files can be exported"}
    target = Path(out).expanduser() if out else Path(result["path"]).with_suffix(".trail.html")
    target.write_text(page, encoding="utf-8")
    return {"wrote": str(target), "counts": result["counts"]}


TOOLS = [check_document, check_text, trace_number, record_file, record_note, list_documents, export_html]
READ_ONLY = {"check_text", "trace_number", "list_documents"}


def build():
    try:
        from mcp.server.mcpserver import MCPServer as Server  # mcp >= 2
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as Server  # mcp 1.x
        except ImportError as e:
            raise SystemExit('the MCP server needs the mcp package: pip install "claimtrail[mcp]"') from e
    try:
        from mcp.types import ToolAnnotations
    except ImportError:  # older mcp releases have no tool annotations
        ToolAnnotations = None

    app = Server("claimtrail", instructions=INSTRUCTIONS)
    for fn in TOOLS:
        if ToolAnnotations is None:
            app.tool()(fn)
        else:
            ro = fn.__name__ in READ_ONLY
            app.tool(annotations=ToolAnnotations(readOnlyHint=ro, destructiveHint=False, openWorldHint=False))(fn)
    return app


def main() -> None:
    build().run("stdio")

