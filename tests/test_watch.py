"""The watch hook records sources and checks a written document against them."""
import json

from claimtrail.watch import db, hook


def _run(data):
    return hook.handle(data)


def test_numbers_trace_to_tool_output_and_user_message(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMTRAIL_WATCH_DB", str(tmp_path / "w.sqlite"))
    (tmp_path / ".claimtrail-watch").touch()
    cwd = str(tmp_path)
    _run({"hook_event_name": "UserPromptSubmit", "session_id": "s", "cwd": cwd,
          "prompt": "We have 14 analysts."})
    _run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": cwd, "tool_name": "Bash",
          "tool_input": {"command": "python screen.py"},
          "tool_response": {"stdout": "approval_a 0.4141\ngap 5.29\nrows 20000", "interrupted": False}})
    doc = tmp_path / "note.md"
    doc.write_text("We screened 20,000 rows. A was approved at 41.4%, a gap of 5.3 points.\n"
                   "The 14 analysts reviewed 312 files.\n")
    out = _run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": cwd, "tool_name": "Write",
                "tool_input": {"file_path": str(doc)}})
    hits = json.loads(db.connect().execute("SELECT report FROM docs").fetchone()[0])
    status = {h["raw"]: h["status"] for h in hits}
    assert status == {"20,000": "traced", "41.4%": "traced", "5.3 points": "traced",
                      "14": "stated", "312": "unfound"}
    msg = out["hookSpecificOutput"]["additionalContext"]
    assert "'312' (line 2, not found)" in msg and "Not found does not mean false" in msg


def test_reading_the_document_back_does_not_trace_it(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMTRAIL_WATCH_DB", str(tmp_path / "w.sqlite"))
    (tmp_path / ".claimtrail-watch").touch()
    doc = tmp_path / "memo.md"
    doc.write_text("Revenue grew 37.5% this year.\n")
    _run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(tmp_path), "tool_name": "Read",
          "tool_input": {"file_path": str(doc)}, "tool_response": doc.read_text()})
    out = _run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(tmp_path),
                "tool_name": "Edit", "tool_input": {"file_path": str(doc)}})
    assert "'37.5%' (line 1, not found)" in out["hookSpecificOutput"]["additionalContext"]


def test_booleans_in_tool_response_are_not_numbers():
    assert hook._text({"stdout": "x 3.5", "interrupted": False}) == "x 3.5"


def test_no_feedback_outside_an_opted_in_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMTRAIL_WATCH_DB", str(tmp_path / "w.sqlite"))
    doc = tmp_path / "lesson.md"
    doc.write_text("The answer is 42.5.\n")
    out = _run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(tmp_path),
                "tool_name": "Write", "tool_input": {"file_path": str(doc)}})
    assert out == {}
    assert db.connect().execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 1  # still shown on the page


def test_subagent_report_counts_as_reported_not_traced(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMTRAIL_WATCH_DB", str(tmp_path / "w.sqlite"))
    _run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(tmp_path), "tool_name": "Agent",
          "tool_input": {"prompt": "x"}, "tool_response": "The model scored 93.4 on the test."})
    doc = tmp_path / "r.md"
    doc.write_text("It scored 93.4.\n")
    _run({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(tmp_path), "tool_name": "Write",
          "tool_input": {"file_path": str(doc)}})
    hits = json.loads(db.connect().execute("SELECT report FROM docs").fetchone()[0])
    assert [h["status"] for h in hits] == ["reported"]
