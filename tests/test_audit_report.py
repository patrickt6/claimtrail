"""audit-report: numbers in Markdown and HTML reports against the store."""
from __future__ import annotations

import json

from click.testing import CliRunner

import claimtrail
from claimtrail.audit_report import audit_report, split_html, split_markdown
from claimtrail.cli import main


def _setup():
    comp = claimtrail.register_external(
        function_name="lending_audit",
        inputs={"sample": "national"},
        outputs={"rows": 36734685, "lenders": 5329, "approval_gap": 0.04237, "auc": 0.8114},
        code_sha="abc123",
        tags={"basis": "MEASURED"},
    )
    cid = claimtrail.claim(
        "Approval gap across the national file",
        computation_id=comp,
        expect=["outputs.approval_gap ~= 0.042 +- 0.0005"],
        tags={"basis": "MEASURED"},
    )
    return comp, cid


REPORT = """# Q3 lending review

Approval rates differ by 4.2% across 36,734,685 applications from 5,329 lenders.
<!-- ct:{cid} -->

- The model's AUC is 0.81. <!-- ct:{comp} -->
- The model's AUC is 0.84. <!-- ct:{comp} -->

| Metric | Value |
|---|---|
| Rows | 36,734,685 <!-- ct:{comp} --> |
| Lenders | 5,400 <!-- ct:{comp} --> |

A number with no marker: 17 branches.

```
code 999 is ignored <!-- ct:{comp} -->
```

See <!-- ct:deadbeef00 --> for more.
"""


def _write(tmp_path, text, name="report.md"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_markdown_statuses(tmp_path, isolated_store):
    comp, cid = _setup()
    path = _write(tmp_path, REPORT.format(cid=cid, comp=comp[:12]))
    audit = audit_report(path, isolated_store)
    by_line = {e.line: e for e in audit.entries}
    assert [e.status for e in audit.entries] == ["MATCH", "MATCH", "DRIFT", "MATCH", "DRIFT", "MISSING"]
    assert by_line[3].basis == "MEASURED"
    drift = next(e for e in audit.entries if e.status == "DRIFT")
    assert [q.raw_text for q in drift.unsupported] == ["0.84"]
    assert audit.failed()


def test_strict_flags_unmarked_numbers(tmp_path, isolated_store):
    comp, cid = _setup()
    path = _write(tmp_path, REPORT.format(cid=cid, comp=comp[:12]))
    audit = audit_report(path, isolated_store, strict=True)
    unbacked = [e for e in audit.entries if e.status == "UNBACKED"]
    assert len(unbacked) == 1 and "17 branches" in unbacked[0].text


def test_failed_assertion_is_fail(tmp_path, isolated_store):
    comp = claimtrail.register_external(
        function_name="gap", inputs={}, outputs={"approval_gap": 0.0424}, code_sha="x"
    )
    cid = claimtrail.claim("gap", computation_id=comp, expect=["outputs.approval_gap ~= 0.042 +- 0.0005"])
    import sqlite3
    with sqlite3.connect(isolated_store.db_path) as conn:
        conn.execute(
            "UPDATE claims SET assertions = ? WHERE id = ?",
            ('[{"op": "~=", "path": "outputs.approval_gap", "tol": 0.0001, "value": 0.05}]', cid),
        )
    path = _write(tmp_path, f"The gap is 4.2%. <!-- ct:{cid} -->\n")
    (entry,) = audit_report(path, isolated_store).entries
    assert entry.status == "FAIL"


def test_html_blocks_and_data_claim(tmp_path, isolated_store):
    comp, cid = _setup()
    page = f"""<html><head><style>.x{{width: 999px}}</style></head><body>
<p data-claim="{cid}">Approval rates differ by <b>4.2%</b> across 36,734,685 applications.</p>
<table><tr><th>Lenders</th><td>5,329</td><!-- ct:{comp[:12]} --></tr>
<tr><th>AUC</th><td>0.79</td><!-- ct:{comp[:12]} --></tr></table>
<script>var n = 12345;</script>
</body></html>"""
    path = _write(tmp_path, page, "report.html")
    audit = audit_report(path, isolated_store)
    assert [e.status for e in audit.entries] == ["MATCH", "MATCH", "DRIFT"]


def test_split_markdown_keeps_table_rows_and_list_items_apart():
    blocks = split_markdown("para one\nstill one\n\n- a 1\n- b 2\n\n| x | 3 |\n|---|---|\n| y | 4 |\n")
    assert [b.text for b in blocks] == ["para one\nstill one", "- a 1", "- b 2", "| x | 3 |", "| y | 4 |"]
    assert [b.line for b in blocks] == [1, 4, 5, 7, 9]


def test_split_html_drops_scripts():
    blocks = split_html("<p>a 1</p><script>2</script><p>b 3</p>")
    assert [b.text for b in blocks] == ["a 1", "b 3"]


def test_cli_exit_codes_and_json(tmp_path):
    comp, cid = _setup()
    good = _write(tmp_path, f"Rows: 36,734,685. <!-- ct:{comp[:12]} -->\n", "good.md")
    bad = _write(tmp_path, f"Rows: 36,734,686. <!-- ct:{comp[:12]} -->\n", "bad.md")
    runner = CliRunner()
    ok = runner.invoke(main, ["audit-report", str(good)])
    assert ok.exit_code == 0, ok.output
    fail = runner.invoke(main, ["audit-report", str(bad), "--format", "json"])
    assert fail.exit_code == 1
    body = json.loads(fail.output)
    assert body["summary"]["DRIFT"] == 1 and body["entries"][0]["unsupported"] == ["36,734,686"]


def test_html_line_numbers_follow_the_source():
    page = "<html>\n<body>\n<p>first 1</p>\n\n<ul><li>item 2</li>\n<li>item 3</li></ul>\n</body></html>\n"
    assert [(b.text, b.line) for b in split_html(page)] == [
        ("first 1", 3), ("item 2", 5), ("item 3", 6),
    ]
