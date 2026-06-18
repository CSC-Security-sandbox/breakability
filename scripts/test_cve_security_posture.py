#!/usr/bin/env python3
"""Safety tests for CVE/security-posture attribution invariants."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cve_security_posture import build_cve_attribution  # noqa: E402


def alert(package, fixed, cve="CVE-2025-0001", ghsa="GHSA-aaaa-bbbb-cccc", ecosystem="go", severity="high"):
    vuln = {}
    if fixed is not None:
        vuln["first_patched_version"] = fixed
    return {
        "state": "open",
        "dependency": {"package": package, "ecosystem": ecosystem},
        "security_advisory": {
            "ghsa_id": ghsa,
            "cve_id": cve,
            "severity": severity,
            "summary": f"{package} advisory",
        },
        "security_vulnerability": vuln,
    }


def check(name, condition, detail=""):
    if not condition:
        raise AssertionError(f"{name} failed: {detail}")


def test_direct_package_fix():
    fixes, orphans = build_cve_attribution(
        [alert("go.opentelemetry.io/otel/sdk", "1.40.0")],
        {"23": {"package": "go.opentelemetry.io/otel/sdk", "ecosystem": "gomod", "from": "1.39.0", "to": "1.42.0"}},
    )
    check("direct fix count", len(fixes) == 1, fixes)
    check("direct orphan absent", orphans == [], orphans)
    fix = fixes[0]
    check("direct via", fix["via"] == "primary", fix)
    check("direct versions", fix["from_version"] == "1.39.0" and fix["to_version"] == "1.42.0", fix)


def test_transitive_package_fix():
    fixes, orphans = build_cve_attribution(
        [alert("golang.org/x/crypto", "0.31.0")],
        {"44": {
            "package": "github.com/golang-migrate/migrate/v4",
            "ecosystem": "gomod",
            "from": "4.18.1",
            "to": "4.18.2",
            "bumped_modules": {"golang.org/x/crypto": "0.31.0"},
        }},
    )
    check("transitive fix count", len(fixes) == 1, fixes)
    check("transitive orphan absent", orphans == [], orphans)
    fix = fixes[0]
    check("transitive via", fix["via"] == "transitive", fix)
    check("transitive from blank", fix["from_version"] == "", fix)
    check("transitive primary package", fix["primary_package"] == "github.com/golang-migrate/migrate/v4", fix)


def test_below_fixed_in_is_orphan():
    fixes, orphans = build_cve_attribution(
        [alert("go.opentelemetry.io/otel/sdk", "1.43.0", cve="CVE-2026-39883")],
        {"23": {"package": "go.opentelemetry.io/otel/sdk", "ecosystem": "gomod", "from": "1.38.0", "to": "1.42.0"}},
    )
    check("below fixed no credit", fixes == [], fixes)
    check("below fixed orphan", len(orphans) == 1 and orphans[0]["cve_id"] == "CVE-2026-39883", orphans)


def test_otel_142_fixes_140_and_141_not_143():
    fixes, orphans = build_cve_attribution(
        [
            alert("go.opentelemetry.io/otel/sdk", "1.40.0", cve="CVE-2025-1400", ghsa="GHSA-otel-1400"),
            alert("go.opentelemetry.io/otel/sdk", "1.41.0", cve="CVE-2025-1410", ghsa="GHSA-otel-1410"),
            alert("go.opentelemetry.io/otel/sdk", "1.43.0", cve="CVE-2025-1430", ghsa="GHSA-otel-1430"),
        ],
        {"23": {"package": "go.opentelemetry.io/otel/sdk", "ecosystem": "gomod", "from": "1.38.0", "to": "1.42.0"}},
    )
    check("otel fixed subset", {f["cve_id"] for f in fixes} == {"CVE-2025-1400", "CVE-2025-1410"}, fixes)
    check("otel unfixed orphan only", [o["cve_id"] for o in orphans] == ["CVE-2025-1430"], orphans)


def test_same_version_fragment_different_package_does_not_match():
    fixes, orphans = build_cve_attribution(
        [alert("golang.org/x/crypto", "1.42.0")],
        {"23": {"package": "go.opentelemetry.io/otel/sdk", "ecosystem": "gomod", "from": "1.41.0", "to": "1.42.0"}},
    )
    check("different package no credit", fixes == [], fixes)
    check("different package orphan", len(orphans) == 1 and orphans[0]["package"] == "golang.org/x/crypto", orphans)


def test_ecosystem_must_match():
    fixes, orphans = build_cve_attribution(
        [alert("shared/name", "1.2.3", ecosystem="go")],
        {"8": {"package": "shared/name", "ecosystem": "npm", "from": "1.2.0", "to": "1.2.3"}},
    )
    check("ecosystem mismatch no credit", fixes == [], fixes)
    check("ecosystem mismatch orphan", len(orphans) == 1, orphans)


def test_multiple_prs_fix_same_advisory_keep_per_pr_rows():
    fixes, orphans = build_cve_attribution(
        [alert("go.opentelemetry.io/otel/sdk", "1.40.0", cve="CVE-2025-9999")],
        {
            "23": {"package": "go.opentelemetry.io/otel/sdk", "ecosystem": "gomod", "from": "1.38.0", "to": "1.42.0"},
            "27": {"package": "go.opentelemetry.io/otel/sdk", "ecosystem": "gomod", "from": "1.39.0", "to": "1.41.0"},
        },
    )
    check("multiple PR fix rows", {f["pr"] for f in fixes} == {23, 27}, fixes)
    check("multiple PR orphan absent", orphans == [], orphans)


def test_advisory_id_participates_in_dedup_key():
    fixes, orphans = build_cve_attribution(
        [
            alert("golang.org/x/net", "0.34.0", cve="CVE-2025-3333", ghsa="GHSA-one-one-one"),
            alert("golang.org/x/net", "0.34.0", cve="CVE-2025-3333", ghsa="GHSA-two-two-two"),
        ],
        {"33": {"package": "golang.org/x/net", "ecosystem": "gomod", "from": "0.33.0", "to": "0.34.0"}},
    )
    check("advisory id distinct rows", len(fixes) == 2, fixes)
    check("advisory id orphan absent", orphans == [], orphans)


def test_missing_or_invalid_first_patched_fail_closed():
    # Missing/invalid first_patched_version gives no version proof, so attribution fails closed.
    fixes, orphans = build_cve_attribution(
        [
            alert("golang.org/x/net", None, cve="CVE-2025-1111", ghsa="GHSA-missing-fpv"),
            alert("golang.org/x/text", "not-a-version", cve="CVE-2025-2222", ghsa="GHSA-invalid-fpv"),
        ],
        {
            "31": {"package": "golang.org/x/net", "ecosystem": "gomod", "from": "0.1.0", "to": "99.0.0"},
            "32": {"package": "golang.org/x/text", "ecosystem": "gomod", "from": "0.1.0", "to": "99.0.0"},
        },
    )
    check("invalid fpv no credits", fixes == [], fixes)
    check("invalid fpv orphans", {o["cve_id"] for o in orphans} == {"CVE-2025-1111", "CVE-2025-2222"}, orphans)


def test_transitive_otel_142_does_not_credit_fixedin_143():
    # PR bumps otel/sdk transitively to 1.42; CVE requires 1.43 → must be orphan, not fix.
    fixes, orphans = build_cve_attribution(
        [alert("go.opentelemetry.io/otel/sdk", "1.43.0", cve="CVE-2026-39883", ghsa="GHSA-otel-1430-trans")],
        {"23": {
            "package": "github.com/example/primary-pkg",
            "ecosystem": "gomod",
            "from": "2.0.0",
            "to": "2.1.0",
            "bumped_modules": {"go.opentelemetry.io/otel/sdk": "1.42.0"},
        }},
    )
    check("transitive 1.42 no credit for fixedin 1.43", fixes == [], fixes)
    check("transitive 1.42 orphan for fixedin 1.43", len(orphans) == 1 and orphans[0]["cve_id"] == "CVE-2026-39883", orphans)


def test_transitive_otel_142_credits_fixedin_141():
    # PR bumps otel/sdk transitively to 1.42; CVE fixed in 1.41 → credited via transitive.
    fixes, orphans = build_cve_attribution(
        [alert("go.opentelemetry.io/otel/sdk", "1.41.0", cve="CVE-2026-29181", ghsa="GHSA-otel-1410-trans")],
        {"23": {
            "package": "github.com/example/primary-pkg",
            "ecosystem": "gomod",
            "from": "2.0.0",
            "to": "2.1.0",
            "bumped_modules": {"go.opentelemetry.io/otel/sdk": "1.42.0"},
        }},
    )
    check("transitive 1.42 credits fixedin 1.41 fix count", len(fixes) == 1, fixes)
    check("transitive 1.42 credits fixedin 1.41 orphan absent", orphans == [], orphans)
    fix = fixes[0]
    check("transitive 1.42 via=transitive", fix["via"] == "transitive", fix)
    check("transitive 1.42 to_version", fix["to_version"] == "1.42.0", fix)
    check("transitive 1.42 primary_package", fix["primary_package"] == "github.com/example/primary-pkg", fix)


def test_transitive_same_version_fragment_different_package_no_match():
    # otel/sdk bumped transitively to 1.42; alert is for x/crypto with fpv 1.42.
    # Transitive bumped_modules dict only contains otel/sdk, so x/crypto must not match.
    fixes, orphans = build_cve_attribution(
        [alert("golang.org/x/crypto", "1.42.0", cve="CVE-2026-99999", ghsa="GHSA-crypto-trans")],
        {"23": {
            "package": "github.com/example/primary-pkg",
            "ecosystem": "gomod",
            "from": "2.0.0",
            "to": "2.1.0",
            "bumped_modules": {"go.opentelemetry.io/otel/sdk": "1.42.0"},
        }},
    )
    check("transitive cross-package no credit", fixes == [], fixes)
    check("transitive cross-package orphan", len(orphans) == 1 and orphans[0]["package"] == "golang.org/x/crypto", orphans)


def test_fixes_and_orphans_are_disjoint():
    # Invariant: no alert (identified by cve_id+package) can appear in both fixes and orphans.
    alerts = [
        alert("go.opentelemetry.io/otel/sdk", "1.40.0", cve="CVE-2025-FIX1", ghsa="GHSA-fix-1"),
        alert("go.opentelemetry.io/otel/sdk", "1.43.0", cve="CVE-2025-ORPHAN1", ghsa="GHSA-orphan-1"),
        alert("golang.org/x/crypto", "0.31.0", cve="CVE-2025-FIX2", ghsa="GHSA-fix-2"),
    ]
    prs = {
        "23": {
            "package": "go.opentelemetry.io/otel/sdk",
            "ecosystem": "gomod",
            "from": "1.38.0",
            "to": "1.42.0",
            "bumped_modules": {"golang.org/x/crypto": "0.31.0"},
        }
    }
    fixes, orphans = build_cve_attribution(alerts, prs)
    fix_keys = {(f["cve_id"], f["package"]) for f in fixes}
    orphan_keys = {(o["cve_id"], o["package"]) for o in orphans}
    overlap = fix_keys & orphan_keys
    check("fixes and orphans disjoint", overlap == set(), f"overlap={overlap}")
    check("expected fixes present", "CVE-2025-FIX1" in {f["cve_id"] for f in fixes}, fixes)
    check("expected orphan present", "CVE-2025-ORPHAN1" in {o["cve_id"] for o in orphans}, orphans)


TESTS = [
    test_direct_package_fix,
    test_transitive_package_fix,
    test_below_fixed_in_is_orphan,
    test_otel_142_fixes_140_and_141_not_143,
    test_same_version_fragment_different_package_does_not_match,
    test_ecosystem_must_match,
    test_multiple_prs_fix_same_advisory_keep_per_pr_rows,
    test_advisory_id_participates_in_dedup_key,
    test_missing_or_invalid_first_patched_fail_closed,
    test_transitive_otel_142_does_not_credit_fixedin_143,
    test_transitive_otel_142_credits_fixedin_141,
    test_transitive_same_version_fragment_different_package_no_match,
    test_fixes_and_orphans_are_disjoint,
]


def main():
    failures = 0
    for test in TESTS:
        try:
            test()
        except Exception as exc:
            failures += 1
            print(f"FAIL {test.__name__}: {exc}")
    if failures:
        print(f"\n{failures}/{len(TESTS)} FAILED")
        return 1
    print(f"OK: all {len(TESTS)} CVE attribution safety cases passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
