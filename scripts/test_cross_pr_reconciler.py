#!/usr/bin/env python3
"""Tests for cross_pr_reconciler — package-correctness guard and grade reconciliation.

Run: python3 .github/scripts/test_cross_pr_reconciler.py
Exits non-zero on any failure.

Synthetic otel-like PR records mirror the #23/#27/#36 scenario where:
  - PR #23: go.opentelemetry.io/otel (core)
  - PR #27: go.opentelemetry.io/otel/trace
  - PR #36: go.opentelemetry.io/otel/sdk

All three are grouped by merge-results.sh as a release-train (merge_order=merge together).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cross_pr_reconciler import (
    package_matches,
    filter_package_correct_evidence,
    reconcile_release_train_grades,
)

# ── Shared otel changelog bullets (same across all three PRs as declared by the
#    release train — a real-world scenario where the maintainer's changelog applies
#    to the whole module group).
OTEL_BULLETS = [
    "default sampling rate configuration changed",
    "memory exporter cardinality limits increased from 0 to 2000",
    "span processor flush timeout changed from 5s to 30s",
]

# ── Cross-PR deps fixture: otel release train (#23, #27, #36 all go together)
OTEL_CROSS_DEPS = [
    {
        "pr_a": 23, "pr_b": 27,
        "reason": "OpenTelemetry release-train coordination",
        "merge_order": "merge together",
    },
    {
        "pr_a": 27, "pr_b": 36,
        "reason": "OpenTelemetry release-train coordination",
        "merge_order": "merge together",
    },
]


def _make_otel_pr(package, bullets, grade, source, call_site, call_site_import_path,
                  surface_evidence=None):
    """Build a minimal PR record with behavioral_grade pre-populated."""
    return {
        "package": package,
        "ecosystem": "gomod",
        "from": "1.20.0",
        "to": "1.21.0",
        "deterministic": {
            "changelogSignal": {"bullets": bullets},
        },
        "declared_break_reachability": {
            "reachability_kind": "import",
            "prod_reachable": True,
            "surface_evidence": surface_evidence or [],
        },
        "behavioral_grade": {
            "grade": grade,
            "source": source,
            "call_site": call_site,
            "call_site_import_path": call_site_import_path,
            "rationale": "test rationale",
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# package_matches tests
# ─────────────────────────────────────────────────────────────────────────────

def test_package_matches_exact():
    assert package_matches("go.opentelemetry.io/otel/trace", "go.opentelemetry.io/otel/trace")
    print("✓ test_package_matches_exact")


def test_package_matches_ancestor():
    """Core otel is an ancestor of trace — matches (same module tree)."""
    assert package_matches("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/trace")
    assert package_matches("go.opentelemetry.io/otel/trace", "go.opentelemetry.io/otel")
    print("✓ test_package_matches_ancestor")


def test_package_no_match_sibling():
    """trace and exporters/prometheus are siblings — no match."""
    assert not package_matches(
        "go.opentelemetry.io/otel/exporters/prometheus",
        "go.opentelemetry.io/otel/trace",
    )
    assert not package_matches(
        "go.opentelemetry.io/otel/trace",
        "go.opentelemetry.io/otel/exporters/prometheus",
    )
    print("✓ test_package_no_match_sibling")


def test_package_no_match_unrelated():
    """prometheus/client_golang is entirely unrelated to otel/trace."""
    assert not package_matches(
        "github.com/prometheus/client_golang/prometheus",
        "go.opentelemetry.io/otel/trace",
    )
    print("✓ test_package_no_match_unrelated")


def test_package_matches_empty():
    assert not package_matches("", "go.opentelemetry.io/otel/trace")
    assert not package_matches("go.opentelemetry.io/otel/trace", "")
    assert not package_matches("", "")
    print("✓ test_package_matches_empty")


# ─────────────────────────────────────────────────────────────────────────────
# filter_package_correct_evidence tests
# ─────────────────────────────────────────────────────────────────────────────

def test_filter_prefers_matching_package():
    """When evidence includes both a matching and a mismatching entry, only the
    matching entry is returned."""
    pr_package = "go.opentelemetry.io/otel/trace"
    evidence = [
        # This one is prometheus — sibling, should be filtered OUT
        {
            "named": True, "is_test": False,
            "path": "go.opentelemetry.io/otel/exporters/prometheus",
            "symbol": "New", "file": "internal/metrics.go", "line": 10,
        },
        # This one is trace — should be KEPT
        {
            "named": True, "is_test": False,
            "path": "go.opentelemetry.io/otel/trace",
            "symbol": "Start", "file": "internal/tracing.go", "line": 42,
        },
    ]
    filtered = filter_package_correct_evidence(evidence, pr_package)
    assert len(filtered) == 1
    assert filtered[0]["path"] == "go.opentelemetry.io/otel/trace"
    assert "_package_mismatch" not in filtered[0]
    print("✓ test_filter_prefers_matching_package")


def test_filter_falls_back_with_mismatch_flag():
    """When NO evidence matches the package, all entries are returned with
    _package_mismatch=True so the caller can surface a warning."""
    pr_package = "go.opentelemetry.io/otel/trace"
    evidence = [
        {
            "named": True, "is_test": False,
            "path": "go.opentelemetry.io/otel/exporters/prometheus",
            "symbol": "New", "file": "internal/metrics.go", "line": 10,
        },
    ]
    filtered = filter_package_correct_evidence(evidence, pr_package)
    assert len(filtered) == 1
    assert filtered[0]["_package_mismatch"] is True
    print("✓ test_filter_falls_back_with_mismatch_flag")


def test_filter_ancestor_package_is_accepted():
    """Core otel (ancestor) evidence is accepted for a trace PR."""
    pr_package = "go.opentelemetry.io/otel/trace"
    evidence = [
        {
            "named": True, "is_test": False,
            "path": "go.opentelemetry.io/otel",  # ancestor — acceptable
            "symbol": "Tracer", "file": "internal/tracing.go", "line": 5,
        },
    ]
    filtered = filter_package_correct_evidence(evidence, pr_package)
    assert len(filtered) == 1
    assert "_package_mismatch" not in filtered[0]
    print("✓ test_filter_ancestor_package_is_accepted")


def test_filter_skips_test_entries_unchanged():
    """filter_package_correct_evidence does not touch is_test gating (caller's job)."""
    pr_package = "go.opentelemetry.io/otel/trace"
    evidence = [
        {
            "named": True, "is_test": True,  # test entry — left as-is by filter
            "path": "go.opentelemetry.io/otel/trace",
            "symbol": "Start", "file": "internal/tracing_test.go", "line": 7,
        },
    ]
    filtered = filter_package_correct_evidence(evidence, pr_package)
    assert len(filtered) == 1
    assert "_package_mismatch" not in filtered[0]
    print("✓ test_filter_skips_test_entries_unchanged")


# ─────────────────────────────────────────────────────────────────────────────
# reconcile_release_train_grades tests
# ─────────────────────────────────────────────────────────────────────────────

def test_no_cross_pr_deps_produces_no_notes():
    """Without cross_pr_deps there are no release-train groups — no notes."""
    prs = {
        "23": _make_otel_pr("go.opentelemetry.io/otel", OTEL_BULLETS,
                            "medium", "reasoning", "internal/otel.go:5",
                            "go.opentelemetry.io/otel"),
    }
    notes = reconcile_release_train_grades(prs, [])
    assert notes == {}
    print("✓ test_no_cross_pr_deps_produces_no_notes")


def test_consistent_grades_produce_no_notes():
    """Three PRs in the same train with the same grade produce no reconciliation notes."""
    prs = {
        "23": _make_otel_pr("go.opentelemetry.io/otel", OTEL_BULLETS,
                            "medium", "reasoning", "internal/otel.go:5",
                            "go.opentelemetry.io/otel"),
        "27": _make_otel_pr("go.opentelemetry.io/otel/trace", OTEL_BULLETS,
                            "medium", "reasoning", "internal/tracing.go:42",
                            "go.opentelemetry.io/otel/trace"),
        "36": _make_otel_pr("go.opentelemetry.io/otel/sdk", OTEL_BULLETS,
                            "medium", "reasoning", "internal/sdk.go:15",
                            "go.opentelemetry.io/otel/sdk"),
    }
    notes = reconcile_release_train_grades(prs, OTEL_CROSS_DEPS)
    assert notes == {}, f"expected no notes, got: {notes}"
    print("✓ test_consistent_grades_produce_no_notes")


def test_package_mismatch_is_flagged():
    """A trace PR whose grade was derived using a prometheus exporter call site
    (import path mismatch) must be flagged as PACKAGE-MISMATCH."""
    prs = {
        "23": _make_otel_pr("go.opentelemetry.io/otel", OTEL_BULLETS,
                            "medium", "reasoning", "internal/otel.go:5",
                            "go.opentelemetry.io/otel"),
        # PR #27 is for otel/trace but its grade was derived from a prometheus site
        "27": _make_otel_pr("go.opentelemetry.io/otel/trace", OTEL_BULLETS,
                            "low", "reasoning", "internal/metrics.go:10",
                            "go.opentelemetry.io/otel/exporters/prometheus"),  # WRONG
        "36": _make_otel_pr("go.opentelemetry.io/otel/sdk", OTEL_BULLETS,
                            "medium", "reasoning", "internal/sdk.go:15",
                            "go.opentelemetry.io/otel/sdk"),
    }
    notes = reconcile_release_train_grades(prs, OTEL_CROSS_DEPS)
    assert "27" in notes, f"expected PR #27 to be flagged, got notes={notes}"
    assert "PACKAGE-MISMATCH" in notes["27"], notes["27"]
    assert "prometheus" in notes["27"], notes["27"]
    assert "trace" in notes["27"], notes["27"]
    print("✓ test_package_mismatch_is_flagged")


def test_same_bullets_same_site_different_grades_flagged():
    """Same bullets + same call site + different grades = unexplained inconsistency."""
    shared_site = "internal/otel_shared.go:20"
    prs = {
        "23": _make_otel_pr("go.opentelemetry.io/otel", OTEL_BULLETS,
                            "medium", "reasoning", shared_site,
                            "go.opentelemetry.io/otel"),
        "27": _make_otel_pr("go.opentelemetry.io/otel/trace", OTEL_BULLETS,
                            "low", "reasoning", shared_site,   # different grade, same site
                            "go.opentelemetry.io/otel/trace"),
    }
    # Create a single dep between 23 and 27
    deps = [{"pr_a": 23, "pr_b": 27, "reason": "otel train", "merge_order": "merge together"}]
    notes = reconcile_release_train_grades(prs, deps)
    # At least one of them must be flagged (the note is attached to both)
    flagged = [n for n in ("23", "27") if n in notes and "GRADE-INCONSISTENCY" in notes[n]]
    assert len(flagged) >= 1, f"expected GRADE-INCONSISTENCY, got notes={notes}"
    print("✓ test_same_bullets_same_site_different_grades_flagged")


def test_same_bullets_different_site_different_grades_annotated_not_error():
    """Same bullets, different grades, BUT different call sites: difference is valid
    (each package exposes the break differently).  Notes should say DIFFERS-FROM-SIBLING,
    not GRADE-INCONSISTENCY."""
    prs = {
        "23": _make_otel_pr("go.opentelemetry.io/otel", OTEL_BULLETS,
                            "medium", "reasoning", "internal/otel.go:5",
                            "go.opentelemetry.io/otel"),
        "27": _make_otel_pr("go.opentelemetry.io/otel/trace", OTEL_BULLETS,
                            "low", "reasoning", "internal/tracing.go:42",   # DIFFERENT site
                            "go.opentelemetry.io/otel/trace"),
    }
    deps = [{"pr_a": 23, "pr_b": 27, "reason": "otel train", "merge_order": "merge together"}]
    notes = reconcile_release_train_grades(prs, deps)
    for num in ("23", "27"):
        if num in notes:
            assert "GRADE-INCONSISTENCY" not in notes[num], (
                f"PR #{num} should not be flagged as INCONSISTENCY (different sites); "
                f"got: {notes[num]}"
            )
            assert "GRADE-DIFFERS-FROM-SIBLING" in notes[num], notes[num]
    print("✓ test_same_bullets_different_site_different_grades_annotated_not_error")


def test_non_release_train_deps_not_checked():
    """Dependencies with merge_order other than 'merge together' are NOT grouped into
    a release train and should not trigger reconciliation."""
    prs = {
        "23": _make_otel_pr("go.opentelemetry.io/otel", OTEL_BULLETS,
                            "medium", "reasoning", "internal/otel.go:5",
                            "go.opentelemetry.io/otel"),
        "27": _make_otel_pr("go.opentelemetry.io/otel/trace", OTEL_BULLETS,
                            "low", "reasoning", "internal/otel.go:5",   # same site, diff grade
                            "go.opentelemetry.io/otel/trace"),
    }
    # These are sequential dependencies, NOT a release train
    deps = [
        {"pr_a": 23, "pr_b": 27, "reason": "trace depends on core",
         "merge_order": "core first"},
    ]
    notes = reconcile_release_train_grades(prs, deps)
    assert notes == {}, f"non-train deps should not be checked; got notes={notes}"
    print("✓ test_non_release_train_deps_not_checked")


def test_full_otel_trio_package_mismatch_plus_inconsistency():
    """Full scenario mirroring otel PRs #23/#27/#36:

    - PR #23 (otel core):  grade=medium, correct site (otel)
    - PR #27 (otel/trace): grade=low, BUT site is a prometheus exporter (package-wrong)
    - PR #36 (otel/sdk):   grade=medium, correct site

    Expected:
    - PR #27 flagged PACKAGE-MISMATCH (prometheus site ≠ trace package)
    - PR #27 also flagged for inconsistency with #23 (same bullets, same site string,
      different grades — the site string being wrong is why the grade is wrong)
    """
    shared_prometheus_site = "internal/metrics.go:22"
    shared_otel_site = "internal/otel.go:5"

    prs = {
        "23": _make_otel_pr(
            "go.opentelemetry.io/otel", OTEL_BULLETS,
            "medium", "reasoning", shared_otel_site,
            "go.opentelemetry.io/otel",
        ),
        "27": _make_otel_pr(
            "go.opentelemetry.io/otel/trace", OTEL_BULLETS,
            "low", "reasoning", shared_prometheus_site,
            "go.opentelemetry.io/otel/exporters/prometheus",  # WRONG package
        ),
        "36": _make_otel_pr(
            "go.opentelemetry.io/otel/sdk", OTEL_BULLETS,
            "medium", "reasoning", "internal/sdk.go:15",
            "go.opentelemetry.io/otel/sdk",
        ),
    }
    notes = reconcile_release_train_grades(prs, OTEL_CROSS_DEPS)

    # PR #27 must be flagged for package mismatch
    assert "27" in notes, f"PR #27 should be flagged; notes={notes}"
    assert "PACKAGE-MISMATCH" in notes["27"], notes["27"]

    # PR #23 and #36 should be clean (or get DIFFERS-FROM-SIBLING for #27's mismatch)
    for num in ("23", "36"):
        if num in notes:
            assert "PACKAGE-MISMATCH" not in notes[num], (
                f"PR #{num} should not be flagged PACKAGE-MISMATCH; got: {notes[num]}"
            )

    print("✓ test_full_otel_trio_package_mismatch_plus_inconsistency")


def test_reconciler_safe_with_missing_pr_data():
    """Reconciler must not crash if a PR in the dep list is absent from prs dict."""
    prs = {
        "23": _make_otel_pr("go.opentelemetry.io/otel", OTEL_BULLETS,
                            "medium", "reasoning", "internal/otel.go:5",
                            "go.opentelemetry.io/otel"),
        # PR 27 is missing from prs (e.g. filtered out)
    }
    deps = [{"pr_a": 23, "pr_b": 27, "reason": "otel train", "merge_order": "merge together"}]
    try:
        notes = reconcile_release_train_grades(prs, deps)
        # Should not raise; may or may not produce notes for #23
    except Exception as e:
        raise AssertionError(f"reconciler crashed on missing PR data: {e}")
    print("✓ test_reconciler_safe_with_missing_pr_data")


def test_reconciler_safe_with_no_behavioral_grade():
    """Reconciler must not crash if a PR has no behavioral_grade (not yet graded)."""
    prs = {
        "23": {
            "package": "go.opentelemetry.io/otel",
            "deterministic": {"changelogSignal": {"bullets": OTEL_BULLETS}},
            # No behavioral_grade key
        },
        "27": _make_otel_pr("go.opentelemetry.io/otel/trace", OTEL_BULLETS,
                            "medium", "reasoning", "internal/tracing.go:42",
                            "go.opentelemetry.io/otel/trace"),
    }
    deps = [{"pr_a": 23, "pr_b": 27, "reason": "otel train", "merge_order": "merge together"}]
    try:
        notes = reconcile_release_train_grades(prs, deps)
    except Exception as e:
        raise AssertionError(f"reconciler crashed on missing behavioral_grade: {e}")
    print("✓ test_reconciler_safe_with_no_behavioral_grade")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all():
    tests = [
        test_package_matches_exact,
        test_package_matches_ancestor,
        test_package_no_match_sibling,
        test_package_no_match_unrelated,
        test_package_matches_empty,
        test_filter_prefers_matching_package,
        test_filter_falls_back_with_mismatch_flag,
        test_filter_ancestor_package_is_accepted,
        test_filter_skips_test_entries_unchanged,
        test_no_cross_pr_deps_produces_no_notes,
        test_consistent_grades_produce_no_notes,
        test_package_mismatch_is_flagged,
        test_same_bullets_same_site_different_grades_flagged,
        test_same_bullets_different_site_different_grades_annotated_not_error,
        test_non_release_train_deps_not_checked,
        test_full_otel_trio_package_mismatch_plus_inconsistency,
        test_reconciler_safe_with_missing_pr_data,
        test_reconciler_safe_with_no_behavioral_grade,
    ]
    failed = []
    for test in tests:
        try:
            test()
        except Exception as e:
            import traceback
            failed.append((test.__name__, e))
            print(f"✗ {test.__name__} FAILED: {e}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)

    if failed:
        print(f"\n{len(failed)} test(s) FAILED:", file=sys.stderr)
        for name, e in failed:
            print(f"  - {name}: {e}", file=sys.stderr)
        return 1

    print(f"\n✓ All {len(tests)} cross-PR reconciler tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
