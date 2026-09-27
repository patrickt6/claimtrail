"""Verification log: append-only, hash-chained, independence-aware."""
from __future__ import annotations

import json
import sqlite3

import pytest
from click.testing import CliRunner

import claimtrail
from claimtrail import ledger
from claimtrail.cli import main
from claimtrail.verify import verify, verify_against


@claimtrail.tracked
def double(x):
    return 2 * x


def _external(outputs=None, recorded_by="analyst@bank.test"):
    return claimtrail.register_external(
        function_name="lending_audit", inputs={"sample": "national"},
        outputs=outputs or {"rows": 36734685}, code_sha="abc", recorded_by=recorded_by,
    )


def test_tracked_rows_record_their_author(monkeypatch):
    monkeypatch.setenv("CLAIMTRAIL_ACTOR", "author@lab.test")
    double(3)
    (comp,) = claimtrail.find(function="double")
    assert comp.recorded_by == "author@lab.test"


def test_verify_appends_and_self_check_is_not_independent(monkeypatch):
    monkeypatch.setenv("CLAIMTRAIL_ACTOR", "author@lab.test")
    double(4)
    (comp,) = claimtrail.find(function="double")
    result = verify(comp.id)
    assert result.ok and result.entry.verifier == "author@lab.test"
    assert ledger.standing(comp).status == "self-checked"
    monkeypatch.setenv("CLAIMTRAIL_ACTOR", "reviewer@lab.test")
    verify(comp.id)
    st = ledger.standing(comp)
    assert st.status == "independent" and st.matches == 2 and st.independent_matches == 1


def test_verify_against_for_external_computations(monkeypatch):
    comp_id = _external()
    monkeypatch.setenv("CLAIMTRAIL_ACTOR", "validator@bank.test")
    assert verify_against(comp_id, {"rows": 36734685}).ok
    comp = claimtrail.get(comp_id)
    assert ledger.standing(comp).status == "independent"
    bad = verify_against(comp_id, {"rows": 36734600})
    assert not bad.ok and bad.entry.result == "mismatch"
    assert ledger.standing(comp).status == "failed"


def test_plain_verify_on_external_explains_and_logs_nothing():
    comp_id = _external()
    result = verify(comp_id)
    assert not result.ok and "--against" in result.message and result.entry is None
    assert ledger.entries(comp_id) == []


def test_legacy_rows_without_author_are_never_independent(isolated_store, monkeypatch):
    comp_id = _external()
    with sqlite3.connect(isolated_store.db_path) as conn:
        conn.execute("UPDATE computations SET recorded_by = NULL")
    monkeypatch.setenv("CLAIMTRAIL_ACTOR", "someone@else.test")
    verify_against(comp_id, {"rows": 36734685})
    assert ledger.standing(claimtrail.get(comp_id)).status == "author-unknown"


def test_log_is_append_only(isolated_store):
    comp_id = _external()
    verify_against(comp_id, {"rows": 36734685})
    with sqlite3.connect(isolated_store.db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("UPDATE verifications SET result = 'match'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute("DELETE FROM verifications")


def test_chain_detects_edits_made_around_the_triggers(isolated_store):
    comp_id = _external()
    for _ in range(3):
        verify_against(comp_id, {"rows": 36734685})
    assert ledger.check_chain() == []
    with sqlite3.connect(isolated_store.db_path) as conn:
        conn.execute("DROP TRIGGER verifications_append_only_update")
        conn.execute("UPDATE verifications SET verifier = 'forged@x.test' WHERE seq = 2")
    problems = ledger.check_chain()
    assert problems == ["entry 2: contents changed after it was written"]


def test_gc_does_not_break_the_log(isolated_store):
    comp_id = _external()
    verify_against(comp_id, {"rows": 36734685})
    isolated_store.delete_computation(comp_id)
    assert len(ledger.entries(comp_id)) == 1 and ledger.check_chain() == []


def test_cli_verify_against_and_verifications(tmp_path, monkeypatch):
    comp_id = _external()
    fresh = tmp_path / "fresh.json"
    fresh.write_text(json.dumps({"results": {"rows": 36734685}}))
    monkeypatch.setenv("CLAIMTRAIL_ACTOR", "validator@bank.test")
    runner = CliRunner()
    ok = runner.invoke(main, ["verify", comp_id[:12], "--against", str(fresh), "--key", "results"])
    assert ok.exit_code == 0 and "logged #1 by validator@bank.test" in ok.output
    log = runner.invoke(main, ["verifications", comp_id[:12]])
    assert "standing: independent" in log.output
    chain = runner.invoke(main, ["verifications", "--check-chain"])
    assert chain.exit_code == 0 and "chain intact: 1 entries" in chain.output


def test_audit_report_shows_standing(tmp_path, monkeypatch):
    comp_id = _external()
    monkeypatch.setenv("CLAIMTRAIL_ACTOR", "validator@bank.test")
    verify_against(comp_id, {"rows": 36734685})
    report = tmp_path / "r.md"
    report.write_text(f"Rows: 36,734,685. <!-- ct:{comp_id[:12]} -->\n")
    (entry,) = claimtrail.audit_report(report, claimtrail.get_store()).entries
    assert entry.status == "MATCH" and entry.verified == "independent"
