"""The MCP tools read the same store as the hook and keep its trust rules."""
import json
import os
import sys
from pathlib import Path

import pytest

from claimtrail.watch import db, hook
from claimtrail.watch import mcp_server as ms


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMTRAIL_WATCH_DB", str(tmp_path / "w.sqlite"))
    return tmp_path


def _status(result):
    return {n["number"]: n["status"] for n in result["numbers"]}


def test_a_recorded_note_never_traces_a_number(store):
    """An agent cannot make an invented number traced by sending it in first."""
    ms.record_note("The model scored 93.4 on the test.", label="my notes", project_dir=str(store))
    doc = store / "r.md"
    doc.write_text("It scored 93.4.\n")
    assert _status(ms.check_document(str(doc), project_dir=str(store))) == {"93.4": "reported"}


def test_a_file_claimtrail_reads_itself_traces(store):
    (store / ".git").mkdir()
    (store / "results.json").write_text('{"auc": 0.8604, "rows": 20000}')
    assert ms.record_file(str(store / "results.json"))["numbers"] == 2
    doc = store / "r.md"
    doc.write_text("AUC was 86% on 19,500 rows, and recall was 71%.\n")
    result = ms.check_document(str(doc))
    assert _status(result) == {"86%": "traced", "19,500": "near", "71%": "unfound"}
    assert [n["number"] for n in result["open"]] == ["19,500", "71%"]
    assert result["numbers"][0]["source"]["label"] == str(store / "results.json")
    assert result["numbers"][1]["source"]["value"] == 20000


def test_mcp_check_keeps_the_scope_the_hook_gave_a_document(store):
    """A later MCP check must not narrow the hook's session scope and flip verdicts."""
    sub = store / "reports"
    sub.mkdir()
    hook.handle({"hook_event_name": "PostToolUse", "session_id": "S", "cwd": "/elsewhere", "tool_name": "Bash",
                 "tool_input": {"command": "python fit.py"}, "tool_response": {"stdout": "rmse 412.7"}})
    doc = sub / "fit.md"
    doc.write_text("RMSE was 412.7.\n")
    hook.handle({"hook_event_name": "PostToolUse", "session_id": "S", "cwd": "/elsewhere", "tool_name": "Write",
                 "tool_input": {"file_path": str(doc)}})
    stored = json.loads(db.connect().execute("SELECT report FROM docs").fetchone()[0])
    assert [h["status"] for h in stored] == ["traced"]
    assert _status(ms.check_document(str(doc))) == {"412.7": "traced"}
    assert _status(ms.check_document(str(doc), project_dir=str(sub))) == {"412.7": "traced"}
    row = db.connect().execute("SELECT session, cwd FROM docs").fetchone()
    assert tuple(row) == ("S", "/elsewhere")


def test_data_and_report_folders_share_the_project_root(store):
    """Without project_dir, a results file and a report in sibling folders of one repo agree."""
    (store / ".claimtrail-watch").touch()
    (store / "data").mkdir(), (store / "reports").mkdir()
    (store / "data" / "x.json").write_text('{"auc": 0.8604}')
    ms.record_file(str(store / "data" / "x.json"))
    (store / "reports" / "r.md").write_text("AUC was 0.8604.\n")
    assert _status(ms.check_document(str(store / "reports" / "r.md"))) == {"0.8604": "traced"}


def test_a_session_in_a_parent_folder_does_not_leak_into_a_project(store):
    hook.handle({"hook_event_name": "PostToolUse", "session_id": "S", "cwd": str(store), "tool_name": "Bash",
                 "tool_input": {"command": "x"}, "tool_response": {"stdout": "total 5,512"}})
    proj = store / "projB"
    (proj / ".git").mkdir(parents=True)
    (proj / "r.md").write_text("Total 5,512.\n")
    assert _status(ms.check_document(str(proj / "r.md"))) == {"5,512": "unfound"}


def test_no_project_folder_asks_for_one(store):
    (store / "r.md").write_text("Total 5,512.\n")
    with pytest.raises(ValueError, match="project_dir"):
        ms.check_document(str(store / "r.md"))


def test_projects_do_not_trace_each_other(store):
    a, b = store / "a", store / "b"
    a.mkdir(), b.mkdir()
    (a / "out.txt").write_text("total 5,512\n")
    ms.record_file(str(a / "out.txt"), project_dir=str(a))
    assert ms.trace_number("5,512", project_dir=str(a))["status"] == "traced"
    assert ms.trace_number("5,512", project_dir=str(b))["status"] == "unfound"


def test_check_text_saves_nothing_and_export_writes_a_page(store):
    (store / ".git").mkdir()
    (store / "out.txt").write_text("mean 41.25\n")
    ms.record_file(str(store / "out.txt"), project_dir=str(store))
    assert _status(ms.check_text("Mean was 41.25.", project_dir=str(store))) == {"41.25": "traced"}
    assert db.connect().execute("SELECT COUNT(*) FROM docs").fetchone()[0] == 0
    doc = store / "r.md"
    doc.write_text("Mean was 41.25.\n")
    out = ms.export_html(str(doc))
    page = Path(out["wrote"]).read_text()
    assert out["wrote"].endswith("r.trail.html") and 'class="n traced"' in page
    assert ms.list_documents()[0]["path"] == str(doc.resolve())


def test_server_speaks_mcp_over_stdio(store):
    """Spawn `claimtrail mcp` the way a client does: nothing but JSON-RPC on stdout."""
    pytest.importorskip("mcp")
    import anyio
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    (store / ".git").mkdir()
    (store / "out.txt").write_text("p95 latency 238 ms\n")
    params = StdioServerParameters(command=sys.executable, args=["-m", "claimtrail.cli", "mcp"],
                                   env={**os.environ, "CLAIMTRAIL_WATCH_DB": str(store / "w.sqlite")})

    async def run():
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            names = {t.name for t in (await s.list_tools()).tools}
            assert names == {f.__name__ for f in ms.TOOLS}
            await s.call_tool("record_file", {"path": str(store / "out.txt")})
            res = await s.call_tool("trace_number", {"number": "238", "project_dir": str(store)})
            assert not getattr(res, "is_error", getattr(res, "isError", False))
            return json.loads(res.content[0].text)

    assert anyio.run(run)["status"] == "traced"


def test_export_and_recheck_keep_the_folder_of_an_earlier_check(store):
    (store / "out.txt").write_text("mean 41.25\n")
    ms.record_file(str(store / "out.txt"), project_dir=str(store))
    doc = store / "r.md"
    doc.write_text("Mean was 41.25.\n")
    assert _status(ms.check_document(str(doc), project_dir=str(store))) == {"41.25": "traced"}
    assert _status(ms.check_document(str(doc))) == {"41.25": "traced"}
    assert ms.export_html(str(doc))["counts"]["traced"] == 1


def test_export_takes_project_dir(store):
    (store / "out.txt").write_text("mean 41.25\n")
    ms.record_file(str(store / "out.txt"), project_dir=str(store))
    doc = store / "r.md"
    doc.write_text("Mean was 41.25.\n")
    assert ms.export_html(str(doc), project_dir=str(store))["counts"]["traced"] == 1


def test_a_wrong_project_dir_once_does_not_stick(store):
    a, b = store / "a", store / "b"
    (a / ".git").mkdir(parents=True), (b / ".git").mkdir(parents=True)
    (a / "secret.json").write_text('{"x": 7731}')
    ms.record_file(str(a / "secret.json"))
    (b / "r.md").write_text("x is 7731.\n")
    assert _status(ms.check_document(str(b / "r.md"), project_dir=str(a))) == {"7731": "traced"}
    again = ms.check_document(str(b / "r.md"))
    assert again["project_dir"] == str(b) and _status(again) == {"7731": "unfound"}
