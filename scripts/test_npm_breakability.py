#!/usr/bin/env python3
"""Locks the npm/TypeScript breakability extensions.

Three Node-specific safety behaviours, each guarding the ZERO-false-green invariant:

1. policy_lowering._security_record — a completed ``npm audit`` (baseline vs PR) that
   introduces no NEW advisories is a clean SECURITY signal (npm has no OSV vuln_status),
   so it must not abstain the whole verdict on "vuln unavailable".
2. evidence_contract major-bump guard — a semver-MAJOR bump cleared ONLY by a semantic
   apidiff (no passing test, no clean changelog) must stay REVIEW. Barrel/re-export
   packages (e.g. @nestjs/common) report a shallow ``compatible=true`` that cannot prove
   a major is safe.
3. reconcile_adjudication._usage_scan_conclusive — for npm, an EMPTY usage scan is not
   proof of non-use (import specifier vs package name mismatch, unbuilt workspaces), so
   the Tier-0 "not imported in the bumped module -> SAFE" clear must not fire.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import policy_lowering as PL  # noqa: E402
import reconcile_adjudication as R  # noqa: E402
from evidence_contract import (  # noqa: E402
    Confidence,
    EvidenceBundle,
    EvidenceRecord,
    SignalName,
    SignalStatus,
    VerdictAction,
    decide,
)


# ── 1. npm audit clean security signal ───────────────────────────────────────
def test_npm_audit_no_new_findings_is_pass():
    pr = {
        "ecosystem": "npm",
        "vuln_status": "unknown",
        "vuln_new_findings": [],
        "npm_audit": {"critical": 1, "high": 6},
    }
    rec = PL._security_record(pr)
    assert rec.status == SignalStatus.PASS, rec.status


def test_npm_audit_new_findings_is_fail():
    pr = {
        "ecosystem": "npm",
        "vuln_status": "unknown",
        "vuln_new_findings": [{"id": "GHSA-xxxx"}],
        "npm_audit": {"critical": 1},
    }
    rec = PL._security_record(pr)
    assert rec.status == SignalStatus.FAIL, rec.status


def test_non_npm_missing_vuln_still_unavailable():
    # Go PRs carry no npm_audit key: an unknown vuln_status stays UNAVAILABLE (unchanged).
    pr = {"ecosystem": "go", "vuln_status": "unknown", "vuln_new_findings": []}
    rec = PL._security_record(pr)
    assert rec.status == SignalStatus.UNAVAILABLE, rec.status


# ── 2. major-bump apidiff guard ───────────────────────────────────────────────
def _semantic_clean_bundle(from_v, to_v, is_major, test_status=SignalStatus.UNAVAILABLE,
                           rn_relevant=None):
    signals = {
        SignalName.BUILD: EvidenceRecord(SignalName.BUILD, SignalStatus.PASS, confidence=Confidence.MEDIUM),
        SignalName.TEST: EvidenceRecord(SignalName.TEST, test_status, confidence=Confidence.LOW),
        SignalName.API_DIFF: EvidenceRecord(SignalName.API_DIFF, SignalStatus.PASS, confidence=Confidence.HIGH),
        SignalName.SECURITY: EvidenceRecord(SignalName.SECURITY, SignalStatus.PASS, confidence=Confidence.MEDIUM),
    }
    if rn_relevant is not None:
        signals[SignalName.RELEASE_NOTES] = EvidenceRecord(
            SignalName.RELEASE_NOTES, SignalStatus.PASS, confidence=Confidence.HIGH, relevant=rn_relevant)
    return EvidenceBundle(
        package="@nestjs/common", ecosystem="npm",
        from_version=from_v, to_version=to_v, is_major=is_major,
        signals=signals, confidence=Confidence.HIGH,
    )


def test_minor_semantic_clean_merges():
    d = decide(_semantic_clean_bundle("1.13.5", "1.17.0", is_major=False))
    assert d.verdict == VerdictAction.MERGE, d.reason_code


def test_major_semantic_clean_held_for_review():
    # Shallow compatible=true on a major (barrel re-exports) must NOT auto-clear.
    d = decide(_semantic_clean_bundle("10.4.22", "11.1.26", is_major=True))
    assert d.verdict == VerdictAction.REVIEW, d.reason_code


def test_major_with_passing_test_merges():
    d = decide(_semantic_clean_bundle("10.0.0", "11.0.0", is_major=True, test_status=SignalStatus.PASS))
    assert d.verdict == VerdictAction.MERGE, d.reason_code


def test_major_with_clean_irrelevant_changelog_merges():
    d = decide(_semantic_clean_bundle("10.0.0", "11.0.0", is_major=True, rn_relevant=False))
    assert d.verdict == VerdictAction.MERGE, d.reason_code


# ── 3. reconcile Tier-0 usage-scan conclusiveness ─────────────────────────────
def _review_pr(ecosystem, files_importing):
    return {
        "package": "react-router", "from": "7.12.0", "to": "7.16.0",
        "ecosystem": ecosystem, "pkg_dir": "services/datamigrator-ui",
        "files_importing": files_importing,
        "verdict_v2": {"verdict": "REVIEW", "evidenceState": {"api_diff": "POSITIVE"}},
        "policy_lowering": {"decision": {"verdict": "REVIEW", "reason_code": "break-reachable"}},
        "deterministic": {"api_changes_detail": [{"name": "X", "changeType": "removed"}]},
    }


def test_npm_empty_scan_not_conclusive_keeps_review():
    pr = _review_pr("npm", [])  # react-router-dom imports unseen by react-router matcher
    action, detail = R.reconcile_pr(pr, {"accepted": False}, repo="/nonexistent-repo")
    assert action == "kept", detail
    assert pr["verdict_v2"]["verdict"].upper() == "REVIEW"


def test_npm_nonempty_out_of_module_scan_clears():
    # Scanner DID see the dep (just in another module) -> trustworthy -> Tier0 SAFE.
    pr = _review_pr("npm", ["services/other-svc/src/app.ts"])
    action, detail = R.reconcile_pr(pr, {"accepted": False}, repo="/nonexistent-repo")
    assert action == "downgraded_safe", detail


def test_go_empty_scan_still_conclusive():
    pr = _review_pr("go", [])
    assert R._usage_scan_conclusive(pr) is True
