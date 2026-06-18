#!/usr/bin/env python3
"""Unit tests for deterministic npm runtime-shape probe classification."""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_dp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "differential-probe.py")
_spec = importlib.util.spec_from_file_location("differential_probe", _dp_path)
dp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp)


def _snapshot(keys=("a", "b"), *, main="index.js", require_ok=True, import_ok=True):
    return {
        "ok": True,
        "package": {
            "name": "example",
            "version": "1.0.0",
            "type": "",
            "main": main,
            "module": "",
            "browser": "",
            "types": "",
            "exports": None,
            "engines": None,
        },
        "load": {
            "require": {"ok": require_ok} if require_ok else {"ok": False, "error": {"code": "ERR_REQUIRE_ESM"}},
            "import": {"ok": import_ok} if import_ok else {"ok": False, "error": {"code": "ERR_MODULE_NOT_FOUND"}},
        },
        "surface": {
            "type": "object",
            "keys": list(keys),
            "props": {k: {"type": "function", "arity": 1} for k in keys},
        },
    }


def test_npm_same_shape_classifies_same_behavior():
    old = _snapshot()
    new = _snapshot()
    grade = dp.npm_grade_from_snapshots("is-odd", "1.0.0", "1.0.1", old, new)
    assert grade["source"] == "probe"
    assert grade["probe_kind"] == "npm_runtime_shape"
    assert grade["same_behavior"] is True
    assert grade["behavior_changed"] is False
    assert grade["grade"] == "low"
    assert grade["confidence"] == "high"


def test_npm_removed_export_classifies_changed_behavior():
    old = _snapshot(keys=("a", "b", "removed"))
    new = _snapshot(keys=("a", "b"))
    grade = dp.npm_grade_from_snapshots("pkg", "1.0.0", "2.0.0", old, new)
    assert grade["same_behavior"] is False
    assert grade["behavior_changed"] is True
    assert grade["grade"] == "medium"
    assert "removed_exports=removed" in grade["evidence"]


def test_npm_additive_package_export_does_not_block_same_behavior():
    old = _snapshot()
    new = _snapshot()
    old["package"]["exports"] = {".": "./index.js"}
    new["package"]["exports"] = {".": "./index.js", "./feature": "./feature.js"}
    grade = dp.npm_grade_from_snapshots("pkg", "1.0.0", "1.1.0", old, new)
    assert grade["same_behavior"] is True
    assert grade["behavior_changed"] is False


def test_npm_install_failure_is_unavailable_not_same():
    grade = dp.npm_grade_from_snapshots("pkg", "1.0.0", "2.0.0", error="npm install failed")
    assert grade["source"] == "probe"
    assert grade["grade"] == "medium"
    assert grade["behavior_changed"] == "unverified"
    assert grade["same_behavior"] is None
    assert "unavailable" in grade["rationale"]


def test_npm_both_entrypoints_throw_is_unavailable():
    old = _snapshot(require_ok=False, import_ok=False)
    new = _snapshot(require_ok=False, import_ok=False)
    grade = dp.npm_grade_from_snapshots("pkg", "1.0.0", "2.0.0", old, new)
    assert grade["grade"] == "medium"
    assert grade["same_behavior"] is None
    assert "entrypoint" in grade["rationale"]


def test_private_netapp_package_is_candidate_but_unavailable(monkeypatch):
    pr = {"ecosystem": "npm", "package": "@netapp-cloud-datamigrate/private", "from": "1.0.0", "to": "1.0.1"}
    assert dp.is_npm_probe_candidate(pr) is True
    grade = dp.run_npm_differential_probe("1", pr)
    assert grade["grade"] == "medium"
    assert grade["same_behavior"] is None
    assert "private" in grade["rationale"]
