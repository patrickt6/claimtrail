"""Claude Code hook: record what the agent saw, check what the agent wrote.

Wire it to PostToolUse (all tools) and UserPromptSubmit. It reads the hook JSON on
stdin, never blocks, and never fails the tool call: any error goes to
~/.claimtrail/hook.log and the hook exits 0.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from claimtrail.watch import db, match

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
DOC_EXT = {".md", ".markdown", ".html", ".htm", ".txt", ".tex", ".rst"}
SKIP_TOOLS = {"TodoWrite", "AskUserQuestion", "ExitPlanMode", "EnterPlanMode", "ToolSearch", "Skill"}
# Text another model wrote: a subagent report or a summarized web page. Kept, but never counts as traced.
REPORT_TOOLS = {"Agent", "Task", "WebFetch", "WebSearch"}
MARKER = ".claimtrail-watch"


def opted_in(path: Path) -> bool:
    """Feedback goes to the agent only under a folder that holds a .claimtrail-watch file,
    and never for Claude's own config and memory."""
    p = path.resolve()
    if Path.home() / ".claude" in p.parents:
        return False
    return any((d / MARKER).exists() for d in p.parents)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(t for t in (_text(v) for v in value.values()) if t)
    if isinstance(value, (list, tuple)):
        return "\n".join(t for t in (_text(v) for v in value) if t)
    return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else ""


def _label(tool: str, inp: dict) -> str:
    for key in ("command", "file_path", "url", "pattern", "query", "path", "notebook_path"):
        if isinstance(inp.get(key), str):
            return inp[key]
    return f"{tool} {_text(inp)[:200]}"


def check_doc(conn, path: str, session: str, cwd: str) -> dict | None:
    p = Path(path)
    if p.suffix.lower() not in DOC_EXT or not p.is_file():
        return None
    text = p.read_text(encoding="utf-8", errors="replace")
    hits = match.check(conn, text, doc_path=str(p), cwd=cwd, session=session)
    conn.execute(
        "INSERT OR REPLACE INTO docs(path, session, cwd, ts, text, report) VALUES (?,?,?,?,?,?)",
        (str(p), session, cwd, db.now(), text, match.to_json(hits)),
    )
    conn.commit()
    return {"hits": hits, "summary": match.summary(hits)}


def feedback(path: str, result: dict) -> str | None:
    bad = [h for h in result["hits"] if h.status in ("unfound", "near")]
    if not bad:
        return None
    parts = []
    for h in bad[:8]:
        if h.status == "near":
            parts.append(f"'{h.raw}' (line {h.line}, nearest source value {h.source_value:g})")
        else:
            parts.append(f"'{h.raw}' (line {h.line}, not found)")
    more = f" and {len(bad) - 8} more" if len(bad) > 8 else ""
    return (
        f"claimtrail: {len(bad)} number(s) in {Path(path).name} do not match any tool output or user "
        f"message from this work: {'; '.join(parts)}{more}. Not found does not mean false. If you computed "
        "one without a tool, compute it with a tool now. Do not delete or change a number only because it "
        "is not found; tell the user which ones are open."
    )


def handle(data: dict) -> dict:
    event = data.get("hook_event_name")
    session = data.get("session_id") or ""
    cwd = data.get("cwd") or ""
    conn = db.connect()
    if event == "UserPromptSubmit":
        db.add_source(conn, session=session, cwd=cwd, kind="user", tool="user",
                      label="user message", text=data.get("prompt") or "")
        return {}
    if event != "PostToolUse":
        return {}
    tool = data.get("tool_name") or ""
    inp = data.get("tool_input") or {}
    if tool in WRITE_TOOLS:
        path = inp.get("file_path") or inp.get("notebook_path")
        result = check_doc(conn, path, session, cwd) if path else None
        msg = feedback(path, result) if result and opted_in(Path(path)) else None
        if msg:
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}
        return {}
    if tool in SKIP_TOOLS:
        return {}
    output = data["tool_output"] if "tool_output" in data else data.get("tool_response")
    if output is None:
        _log(f"no output field for {tool}; keys={sorted(data)}")
    kind = "report" if tool in REPORT_TOOLS else "tool"
    db.add_source(conn, session=session, cwd=cwd, kind=kind, tool=tool,
                  label=_label(tool, inp), text=_text(output))
    return {}


def _log(msg: str) -> None:
    log = db.db_path().parent / "hook.log"
    with log.open("a") as f:
        f.write(f"{db.now()} {msg}\n")


def main() -> None:
    try:
        out = handle(json.load(sys.stdin))
    except Exception:
        _log(traceback.format_exc())
        out = {}
    sys.stdout.write(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
