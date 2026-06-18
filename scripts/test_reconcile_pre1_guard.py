"""Locks the pre-1.0 unverified-reachability clear guard in reconcile_adjudication.

A pre-1.0 (0.x) dependency carries no semver minor-stability guarantee, so an AI SAFE
downgrade based on reachability/grep alone (no executed test suite, no probe) must NOT
clear a multi-version 0.x jump -- it is the exact false-green that #32 (go-openapi/strfmt
0.23.0->0.26.1) produced. Stable (>=1.0) deps and 0.x patch bumps are unaffected, and an
executed-test pass re-enables the clear.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reconcile_adjudication as R


def _pr(pkg, frm, to, tests_ran=False, test_exit=None):
    return {
        "package": pkg, "from": frm, "to": to, "pkg_dir": "/",
        "files_importing": ["database/x.go"],
        "verdict_v2": {"verdict": "REVIEW", "evidenceState": {"api_diff": "POSITIVE"}},
        "policy_lowering": {"decision": {"verdict": "REVIEW", "reason_code": "break-reachable"}},
        "test": {"ran": tests_ran, "exit": test_exit, "main_test_exit": (0 if test_exit == 0 else -1)},
    }


SAFE_VERDICT = {"accepted": True, "final_verdict": "safe", "proof": "grepped, not called", "citation": ""}


def _reconcile(pr):
    return R.reconcile_pr(pr, dict(SAFE_VERDICT), repo="/nonexistent-repo")


def test_pre1_multiminor_grep_clear_is_blocked():
    pr = _pr("github.com/go-openapi/strfmt", "0.23.0", "0.26.1")
    action, detail = _reconcile(pr)
    assert action == "kept", detail
    assert (pr["verdict_v2"]["verdict"]).upper() == "REVIEW"


def test_pre1_minor_jump_blocked():
    pr = _pr("example.com/lib", "0.4.0", "0.5.0")
    action, _ = _reconcile(pr)
    assert action == "kept"
    assert pr["verdict_v2"]["verdict"].upper() == "REVIEW"


def test_stable_dep_minor_jump_clears():
    # >=1.0 dep: semver protects minors; reachability clear is honored.
    pr = _pr("github.com/jackc/pgx/v5", "5.7.4", "5.9.1")
    action, _ = _reconcile(pr)
    assert action == "downgraded_safe"
    assert pr["verdict_v2"]["verdict"].upper() == "SAFE"


def test_pre1_patch_bump_clears():
    # 0.x patch bump is low-risk; the clear is allowed.
    pr = _pr("example.com/lib", "0.23.0", "0.23.4")
    action, _ = _reconcile(pr)
    assert action == "downgraded_safe"
    assert pr["verdict_v2"]["verdict"].upper() == "SAFE"


def test_pre1_multiminor_clears_when_tests_executed():
    # Execution evidence (suite ran + passed) backs the clear even for a 0.x jump.
    pr = _pr("github.com/go-openapi/strfmt", "0.23.0", "0.26.1", tests_ran=True, test_exit=0)
    action, _ = _reconcile(pr)
    assert action == "downgraded_safe"
    assert pr["verdict_v2"]["verdict"].upper() == "SAFE"


def test_semver_tuple_parsing():
    assert R._semver_tuple("v0.26.1") == (0, 26)
    assert R._semver_tuple("5.7.4") == (5, 7)
    assert R._semver_tuple("1") == (1, 0)
    assert R._semver_tuple("garbage") is None
