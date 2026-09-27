"""Structured claim assertions: parsing, recording, re-checking, migration."""
from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

import claimtrail
from claimtrail import store as store_mod
from claimtrail.assertions import (
    ClaimAssertionError,
    Expectation,
    evaluate,
    parse_expectation,
    resolve_path,
)
from claimtrail.cli import main


PAYLOAD = {"outputs": {"rows": 36734685, "gap": 4.237, "table": [{"rate": 0.81}]}}


def _register(outputs=None) -> str:
    return claimtrail.register_external(
        function_name="lending_audit",
        inputs={"year": "2023-2025"},
        outputs=outputs if outputs is not None else PAYLOAD["outputs"],
        code_sha="abc123",
    )


@pytest.mark.parametrize("spec,expected", [
    ("outputs.rows == 36734685", Expectation("outputs.rows", "==", 36734685)),
    ("outputs.gap ~= 4.2 +- 0.05", Expectation("outputs.gap", "~=", 4.2, 0.05)),
    ("outputs.gap ~= 4.2 ± 0.05", Expectation("outputs.gap", "~=", 4.2, 0.05)),
    ("outputs.table[0].rate in [0.8, 0.82]", Expectation("outputs.table[0].rate", "in", [0.8, 0.82])),
    ("outputs.label == 'MEASURED'", Expectation("outputs.label", "==", "MEASURED")),
])
def test_parse(spec, expected):
    assert parse_expectation(spec) == expected


def test_parse_rejects_bad_specs():
    with pytest.raises(ValueError):
        parse_expectation("just words")
    with pytest.raises(ValueError):
        parse_expectation("outputs.gap ~= 4.2")


def test_resolve_and_evaluate():
    assert resolve_path(PAYLOAD, "outputs.table[0].rate") == 0.81
    assert evaluate(parse_expectation("outputs.gap ~= 4.2 +- 0.05"), PAYLOAD).ok
    assert not evaluate(parse_expectation("outputs.gap ~= 4.3 +- 0.05"), PAYLOAD).ok
    missing = evaluate(parse_expectation("outputs.nope == 1"), PAYLOAD)
    assert not missing.ok and "path not found" in missing.message


def test_claim_is_refused_when_its_assertion_is_false():
    comp = _register()
    with pytest.raises(ClaimAssertionError, match="not supported"):
        claimtrail.claim("Gap is 5 points", computation_id=comp, expect=["outputs.gap ~= 5 +- 0.05"])


def test_claim_with_assertions_needs_a_computation():
    with pytest.raises(ClaimAssertionError):
        claimtrail.claim("Gap is 4.2 points", expect=["outputs.gap ~= 4.2 +- 0.05"])


def test_check_claims_catches_a_relinked_payload(isolated_store):
    good = _register()
    cid = claimtrail.claim(
        "Gap is 4.2 points", computation_id=good, expect=["outputs.gap ~= 4.2 +- 0.05"]
    )
    assert all(c.ok for c in claimtrail.check_claims())
    # Point the claim at a different run whose number moved.
    moved = claimtrail.register_external(
        function_name="lending_audit", inputs={"year": "2026"},
        outputs={"rows": 36734685, "gap": 4.9}, code_sha="abc123",
    )
    with sqlite3.connect(isolated_store.db_path) as conn:
        conn.execute("UPDATE claims SET computation_id = ? WHERE id = ?", (moved, cid))
    (chk,) = claimtrail.check_claims()
    assert not chk.ok


def test_cli_claim_expect_and_check():
    comp = _register()
    runner = CliRunner()
    ok = runner.invoke(main, ["claim", "36,734,685 rows", "--link", comp[:12],
                              "--expect", "outputs.rows == 36734685"])
    assert ok.exit_code == 0, ok.output
    bad = runner.invoke(main, ["claim", "wrong", "--link", comp[:12],
                               "--expect", "outputs.rows == 1"])
    assert bad.exit_code != 0 and "not supported" in bad.output
    checked = runner.invoke(main, ["check"])
    assert checked.exit_code == 0 and "all assertions hold" in checked.output


def test_lint_reports_assertfail(isolated_store):
    comp = _register()
    cid = claimtrail.claim("rows", computation_id=comp, expect=["outputs.rows == 36734685"])
    with sqlite3.connect(isolated_store.db_path) as conn:
        conn.execute(
            "UPDATE claims SET assertions = ? WHERE id = ?",
            ('[{"op": "==", "path": "outputs.rows", "value": 1}]', cid),
        )
    result = CliRunner().invoke(main, ["lint"])
    assert result.exit_code == 1 and "ASSERTFAIL" in result.output


def test_v4_store_migrates_additively(tmp_path):
    root = tmp_path / "v4"
    store = store_mod.Store(root)
    store_mod.set_store_root(root)
    comp = _register()
    cid = claimtrail.claim("kept", computation_id=comp)
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("ALTER TABLE claims DROP COLUMN assertions")
        conn.execute("UPDATE schema_meta SET value = '4' WHERE key = 'version'")
    reopened = store_mod.Store(root)
    with sqlite3.connect(reopened.db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(claims)")}
        version = conn.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()[0]
    assert "assertions" in cols and version == "5"
    kept = reopened.get_claim(cid)
    assert kept.text == "kept" and kept.assertions is None
