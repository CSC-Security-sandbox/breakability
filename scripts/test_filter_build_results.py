#!/usr/bin/env python3
"""Unit tests for filter-build-results.py (no pytest dependency)."""

from __future__ import annotations

import importlib.util
import pathlib
import sys


SCRIPT = pathlib.Path(__file__).with_name("filter-build-results.py")
SPEC = importlib.util.spec_from_file_location("filter_build_results", SCRIPT)
assert SPEC and SPEC.loader
FILTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FILTER)


def fixture():
    return {
        "metadata": {"repo": "owner/repo", "pr_count": 4},
        "prs": {
            "2": {"package": "alpha", "vuln_status": "ok", "vuln_new_findings": []},
            "3": {"package": "beta", "vuln_status": "vulns_found", "vuln_new_findings": ["CVE-1"]},
            "6": {"package": "gamma"},
            "9": {"package": "delta", "vuln_status": "vulns_found", "vuln_new_findings": ["CVE-9"]},
        },
        "cross_pr_deps": [
            {"pr_a": 2, "pr_b": 3, "reason": "keep"},
            {"pr_a": 2, "pr_b": 9, "reason": "omit omitted PR"},
            {"pr_a": 9, "pr_b": 10, "reason": "omit both"},
        ],
        "security_posture": {
            "total_open_alerts": 7,
            "severity_counts": {"high": 2},
            "prs_fixing_alerts": {
                "2": {"alert_count": 2, "severities": ["high"]},
                "9": {"alert_count": 3, "severities": ["medium"]},
            },
            "prs_with_cves": {"3": ["CVE-1"], "9": ["CVE-9"]},
            "alerts_fixable_by_merging": 5,
            "total_cves_in_prs": 2,
            "cve_fixes": [
                {"pr": 2, "package": "alpha", "cve_id": "CVE-2"},
                {"pr": 9, "package": "delta", "cve_id": "CVE-9"},
            ],
            "orphan_alerts": [{"package": "orphan", "cve_id": "GHSA-x"}],
        },
        "govulncheck": {
            "main_baseline": {"status": "ok", "findings": []},
            "prs_scanned": 2,
            "prs_with_new_vulns": 2,
            "total_new_findings": ["CVE-1", "CVE-9"],
        },
    }


def test_selected_prs_remain():
    out = FILTER.filter_results(fixture(), [2, 3, 6])
    assert list(out["prs"].keys()) == ["2", "3", "6"]
    assert out["metadata"]["pr_count"] == 3
    assert out["metadata"]["original_pr_count"] == 4
    assert out["metadata"]["missing_pr_numbers"] == []


def test_cross_pr_deps_do_not_reference_omitted_prs():
    out = FILTER.filter_results(fixture(), [2, 3, 6])
    assert out["cross_pr_deps"] == [{"pr_a": 2, "pr_b": 3, "reason": "keep"}]
    assert out["metadata"]["subset_omitted_counts"]["cross_pr_deps"] == 2
    selected = set(out["metadata"]["selected_pr_numbers"])
    for dep in out["cross_pr_deps"]:
        assert {dep["pr_a"], dep["pr_b"]} <= selected


def test_security_posture_is_filtered_and_marked_subset():
    out = FILTER.filter_results(fixture(), [2, 3, 6])
    sec = out["security_posture"]
    assert sec["scope"] == "subset"
    assert set(sec["prs_fixing_alerts"]) == {"2"}
    assert set(sec["prs_with_cves"]) == {"3"}
    assert sec["alerts_fixable_by_merging"] == 2
    assert sec["total_cves_in_prs"] == 1
    assert sec["cve_fixes"] == [{"pr": 2, "package": "alpha", "cve_id": "CVE-2"}]
    assert sec["orphan_alerts"] == []
    assert sec["orphan_alerts_omitted_for_subset"] == 1
    assert "orphan_alerts" in sec["omitted_due_to_subset"]
    assert out["metadata"]["subset_omitted_counts"]["security_posture.prs_fixing_alerts"] == 1


def test_missing_requested_prs_are_recorded():
    out = FILTER.filter_results(fixture(), [2, 99])
    assert list(out["prs"].keys()) == ["2"]
    assert out["metadata"]["missing_pr_numbers"] == [99]
    assert out["metadata"]["requested_pr_numbers"] == [2, 99]


def test_parse_pr_numbers_is_strict():
    assert FILTER.parse_pr_numbers("2, #3,6") == [2, 3, 6]
    try:
        FILTER.parse_pr_numbers("2,not-a-number")
    except ValueError:
        return
    raise AssertionError("invalid PR token should fail")


def main() -> int:
    tests = [
        test_selected_prs_remain,
        test_cross_pr_deps_do_not_reference_omitted_prs,
        test_security_posture_is_filtered_and_marked_subset,
        test_missing_requested_prs_are_recorded,
        test_parse_pr_numbers_is_strict,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as err:
            failures += 1
            print(f"FAIL {test.__name__}: {err}")
    if failures:
        print(f"{failures}/{len(tests)} FAILED")
        return 1
    print(f"OK: all {len(tests)} filter-build-results cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
