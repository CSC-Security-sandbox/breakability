"""Locks the deterministic-probe structural-break guard in reconcile_adjudication.

The npm runtime-shape probe can prove a STRUCTURAL module-system break: the package
changed its entrypoint (package.main), module type/format (package.module/type), its
exports map, or its require()/import() loadability (e.g. it went ESM-only). Such a
break hits EVERY consumer regardless of which symbols it imports -- a CommonJS project
cannot require() an ESM-only package -- so an AI reachability/grep clear ("the symbol I
use is unchanged / not imported") must NOT downgrade it to SAFE. This is the exact
false-green that uuid 10.0.0->14.0.0 produced against the CommonJS admin-service.

Symbol-level-only probe diffs remain AI-resolvable, and an executed-test pass re-enables
the clear.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reconcile_adjudication as R


def _pr(evidence, same_behavior=False, tests_ran=False, test_exit=None,
        pkg="uuid", frm="10.0.0", to="14.0.0"):
    return {
        "package": pkg, "from": frm, "to": to, "pkg_dir": "/",
        "ecosystem": "npm",
        "files_importing": ["services/admin-service/src/upgrade/upgrade.service.ts"],
        # Adjudicable REVIEW driven by a declared-breaking changelog so the AI-safe path
        # is actually reached (the guard sits inside `if final == 'safe'`).
        "verdict_v2": {"verdict": "REVIEW", "evidenceState": {"api_diff": "POSITIVE"}},
        "policy_lowering": {"decision": {"verdict": "REVIEW", "reason_code": "break-reachable"}},
        "deterministic": {"api_changes_detail": [{"name": "x", "changeType": "removed"}]},
        "behavioral_grade": {
            "probe_kind": "npm_runtime_shape",
            "same_behavior": same_behavior,
            "evidence": evidence,
        },
        "test": {"ran": tests_ran, "exit": test_exit, "main_test_exit": (0 if test_exit == 0 else -1)},
    }


SAFE_VERDICT = {"accepted": True, "final_verdict": "safe", "proof": "we use ESM; v4 unchanged", "citation": ""}


def _reconcile(pr):
    return R.reconcile_pr(pr, dict(SAFE_VERDICT), repo="/nonexistent-repo")


def test_structural_esm_break_blocks_ai_clear():
    # uuid-style: package.main/module/type changed -> ESM-only. AI must not clear.
    pr = _pr("package.main; package.module; package.type; removed_exports=__esModule")
    action, detail = _reconcile(pr)
    assert action == "kept", detail
    assert pr["verdict_v2"]["verdict"].upper() == "REVIEW"
    assert pr["ai_adjudication"]["source"] == "probe_structural_floor"
    assert pr["ai_adjudication"]["applied"] == "hold_review"


def test_exports_map_change_blocks_ai_clear():
    pr = _pr("removed_package_exports=.; changed_package_exports=./node")
    action, detail = _reconcile(pr)
    assert action == "kept", detail
    assert pr["verdict_v2"]["verdict"].upper() == "REVIEW"


def test_loader_change_blocks_ai_clear():
    pr = _pr("load.require; load.import")
    action, detail = _reconcile(pr)
    assert action == "kept", detail
    assert pr["verdict_v2"]["verdict"].upper() == "REVIEW"


def test_symbol_level_diff_is_ai_resolvable():
    # Only individual member shape changed -> reachability can legitimately clear it.
    pr = _pr("changed_exports=AxiosError,default")
    action, _ = _reconcile(pr)
    assert action == "downgraded_safe"
    assert pr["verdict_v2"]["verdict"].upper() == "SAFE"


def test_structural_break_clears_when_tests_executed():
    # An actually-run, passing test suite is execution evidence that backs the clear.
    pr = _pr("package.main; package.type", tests_ran=True, test_exit=0)
    action, _ = _reconcile(pr)
    assert action == "downgraded_safe"
    assert pr["verdict_v2"]["verdict"].upper() == "SAFE"


def test_probe_same_behavior_true_does_not_block():
    # Probe found the runtime surface matched -> nothing to protect; clear is honored.
    pr = _pr("", same_behavior=True)
    action, _ = _reconcile(pr)
    assert action == "downgraded_safe"
    assert pr["verdict_v2"]["verdict"].upper() == "SAFE"


def test_guard_unit_true_on_structural():
    blocked, why = R._probe_structural_break_unverified_clear(
        _pr("package.main; package.module"))
    assert blocked is True
    assert "STRUCTURAL" in why


def test_guard_unit_false_on_symbol_only():
    blocked, _ = R._probe_structural_break_unverified_clear(
        _pr("changed_exports=foo"))
    assert blocked is False
