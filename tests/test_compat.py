"""Stores and code written under the old `qprov` name keep working."""
from __future__ import annotations

import sqlite3

import claimtrail
from claimtrail import store as store_mod


def _reset():
    store_mod._store_singleton = None
    store_mod._store_root_override = None


def test_qprov_import_alias_exposes_the_same_objects():
    import qprov
    from qprov.store import Store

    assert qprov.tracked is claimtrail.tracked
    assert Store is claimtrail.Store
    assert qprov.QprovCollisionError is claimtrail.ClaimtrailCollisionError


def test_legacy_qprov_home_env_var_is_honored(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAIMTRAIL_HOME", raising=False)
    monkeypatch.setenv("QPROV_HOME", str(tmp_path / "legacy"))
    _reset()
    assert store_mod.get_store().root == (tmp_path / "legacy").resolve()


def test_legacy_dot_qprov_dir_is_discovered_and_opened_in_place(tmp_path, monkeypatch):
    project = tmp_path / "project"
    legacy = project / ".qprov"
    legacy.mkdir(parents=True)
    sqlite3.connect(legacy / "qprov.sqlite").close()
    work = project / "sub"
    work.mkdir()
    monkeypatch.delenv("CLAIMTRAIL_HOME", raising=False)
    monkeypatch.delenv("QPROV_HOME", raising=False)
    monkeypatch.chdir(work)
    _reset()
    store = store_mod.get_store()
    assert store.root == legacy.resolve()
    assert store.db_path.name == "qprov.sqlite"
    assert not (legacy / "claimtrail.sqlite").exists()


def test_new_store_prefers_claimtrail_names(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAIMTRAIL_HOME", raising=False)
    monkeypatch.delenv("QPROV_HOME", raising=False)
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    monkeypatch.chdir(fresh)
    _reset()
    store = store_mod.get_store()
    assert store.root.name == ".claimtrail"
    assert store.db_path.name == "claimtrail.sqlite"


def test_payload_path_is_stored_relative_to_the_store(isolated_store):
    comp_id = claimtrail.register_external(function_name="f", inputs={}, outputs=1)
    assert claimtrail.get(comp_id).payload_path == f"payloads/{comp_id[:2]}/{comp_id}.json.gz"
