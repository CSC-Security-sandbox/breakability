#!/usr/bin/env python3
"""Cross-PR reconciliation and package-correctness guard for differential-probe.

Two invariants enforced here:

1. Package-correctness: behavioral evidence (call sites) for package A must NOT be
   blindly applied to the probe/oracle grading package B unless the import paths are
   explicitly related.  Within an otel release-train, for example, a call site that
   imports go.opentelemetry.io/otel/exporters/prometheus is NOT evidence for a PR
   upgrading go.opentelemetry.io/otel/trace — the packages are siblings, not the same
   module.  Using the wrong call site causes the oracle to reason about the prometheus
   exporter API when it should reason about trace spans.

2. Grade consistency: PRs in the same release-train group (identified by cross_pr_deps
   entries with merge_order == "merge together") that share *identical* changelog bullets
   MUST produce the same behavioral grade OR carry a cited explanation for the divergence.
   Same evidence → same grade is the baseline; divergence is only valid when the actual
   call sites genuinely differ (different packages expose the break differently), and even
   then the difference is annotated.

Usage (from differential-probe.py):

    from cross_pr_reconciler import (
        filter_package_correct_evidence,
        reconcile_release_train_grades,
    )

    # Evidence filtering (call before first_prod_site selects a site):
    surf_filtered = filter_package_correct_evidence(surf, pr.get("package"))

    # Post-grading reconciliation (call from main() after all PRs are graded):
    notes = reconcile_release_train_grades(prs, data.get("cross_pr_deps") or [])
    for num, note in notes.items():
        if num in prs and isinstance(prs[num].get("behavioral_grade"), dict):
            prs[num]["behavioral_grade"]["reconciliation_note"] = note
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Package-correctness helpers
# ---------------------------------------------------------------------------

def package_matches(site_import_path: str, pr_package: str) -> bool:
    """Return True if site_import_path is the same package or a direct ancestor/
    descendant of pr_package in the same import-path tree.

    Examples (True):
        package_matches("go.opentelemetry.io/otel/trace", "go.opentelemetry.io/otel/trace")
        package_matches("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/trace")
        package_matches("go.opentelemetry.io/otel/trace", "go.opentelemetry.io/otel")

    Examples (False — sibling subtrees):
        package_matches("go.opentelemetry.io/otel/exporters/prometheus",
                        "go.opentelemetry.io/otel/trace")
        package_matches("github.com/prometheus/client_golang",
                        "go.opentelemetry.io/otel/trace")
    """
    if not site_import_path or not pr_package:
        return False
    ip = site_import_path.rstrip("/")
    pkg = pr_package.rstrip("/")
    return ip == pkg or ip.startswith(pkg + "/") or pkg.startswith(ip + "/")


def filter_package_correct_evidence(
    surface_evidence: list,
    pr_package: str,
) -> list:
    """Return the subset of surface_evidence entries whose import path matches pr_package.

    Sorting is preserved (named entries first, as the caller expects).  If no entry
    matches, the original list is returned unchanged so existing behaviour is preserved
    when evidence is sparse — but every returned entry is tagged with
    ``_package_mismatch: True`` so callers can surface a warning.
    """
    if not surface_evidence or not pr_package:
        return surface_evidence or []

    matched = [
        e for e in surface_evidence
        if isinstance(e, dict) and package_matches(
            e.get("path") or e.get("import_path") or "", pr_package
        )
    ]
    if matched:
        return matched

    # No exact-package match found: flag all as mismatched and fall back so the
    # probe/oracle at least has *something* to reason about.
    return [{**e, "_package_mismatch": True} for e in surface_evidence if isinstance(e, dict)]


# ---------------------------------------------------------------------------
# Cross-PR grade reconciliation
# ---------------------------------------------------------------------------

_GRADE_RANK: Dict[str, int] = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _norm_bullets(bullets) -> frozenset:
    """Stable normalised set of bullet texts for equality comparison."""
    out = set()
    for b in (bullets or []):
        if isinstance(b, str):
            out.add(re.sub(r"\s+", " ", b.strip().lower())[:200])
    return frozenset(out)


def _union_find_groups(pairs: List[tuple]) -> Dict[str, List[str]]:
    """Union-find clustering: returns {root -> [member, ...]}."""
    parent: Dict[str, str] = {}

    def find(x: str) -> str:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent.get(x, x), parent.get(x, x))
            x = parent.get(x, x)
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for a, b in pairs:
        union(a, b)

    groups: Dict[str, List[str]] = {}
    seen: set = set(a for a, _ in pairs) | set(b for _, b in pairs)
    for node in seen:
        groups.setdefault(find(node), []).append(node)
    return groups


def reconcile_release_train_grades(
    prs: dict,
    cross_pr_deps: list,
) -> Dict[str, str]:
    """Inspect release-train PR groups for grade inconsistencies.

    Two checks per group:

    A. Package-mismatch: the behavioral_grade.call_site_import_path (stored by the
       driver when it selects a call site) does not match the PR's own package.
       This is the "trace PR reasoning about prometheus exporter" bug.

    B. Grade inconsistency: two PRs in the same train share identical changelog
       bullets but received different grades WITHOUT a differing call site to explain
       it.  Same evidence must produce the same grade or the difference must be cited.

    Args:
        prs:            {pr_num_str -> pr_record_dict} — PRs already graded.
        cross_pr_deps:  list of cross-PR dep dicts from build-results.json.

    Returns:
        {pr_num_str -> reconciliation_note_str}  — empty if no issues found.
    """
    train_pairs = [
        (str(d["pr_a"]), str(d["pr_b"]))
        for d in (cross_pr_deps or [])
        if (
            isinstance(d, dict)
            and str(d.get("merge_order", "")).lower() == "merge together"
            and d.get("pr_a") is not None
            and d.get("pr_b") is not None
        )
    ]
    if not train_pairs:
        return {}

    groups = _union_find_groups(train_pairs)
    notes: Dict[str, str] = {}

    for root, members in groups.items():
        if len(members) < 2:
            continue
        _check_group(members, prs, notes)

    return notes


def _check_group(
    members: List[str],
    prs: dict,
    notes: Dict[str, str],
) -> None:
    """Run package-mismatch and grade-consistency checks for one release-train group."""

    # ── Check A: package-mismatch (per PR) ──────────────────────────────────
    for num in members:
        pr = prs.get(num) or {}
        bg = pr.get("behavioral_grade") or {}
        pr_pkg = pr.get("package") or ""
        site_import = bg.get("call_site_import_path") or ""

        if site_import and pr_pkg and not package_matches(site_import, pr_pkg):
            notes[num] = (
                f"PACKAGE-MISMATCH: behavioral grade for PR #{num} "
                f"(package '{pr_pkg}') was derived using call-site import path "
                f"'{site_import}', which is NOT the same package or a direct ancestor/"
                f"descendant.  The oracle may have reasoned about the wrong package "
                f"(e.g. a prometheus exporter when the declared break is in the trace "
                f"package).  Grade should be re-evaluated with a call site that "
                f"directly imports '{pr_pkg}'."
            )

    # ── Check B: grade inconsistency (pairwise) ──────────────────────────────
    for i, na in enumerate(members):
        for nb in members[i + 1:]:
            pa = prs.get(na) or {}
            pb = prs.get(nb) or {}
            ba = pa.get("behavioral_grade") or {}
            bb = pb.get("behavioral_grade") or {}
            ga = str(ba.get("grade", "")).lower()
            gb = str(bb.get("grade", "")).lower()

            if ga not in _GRADE_RANK or gb not in _GRADE_RANK:
                continue  # One or both not yet graded; skip.
            if ga == gb:
                continue  # Same grade — consistent.

            buls_a = _norm_bullets(
                ((pa.get("deterministic") or {}).get("changelogSignal") or {}).get("bullets")
            )
            buls_b = _norm_bullets(
                ((pb.get("deterministic") or {}).get("changelogSignal") or {}).get("bullets")
            )
            if not (buls_a and buls_b and buls_a == buls_b):
                continue  # Different bullets — grade difference is expected.

            site_a = ba.get("call_site") or ""
            site_b = bb.get("call_site") or ""
            pkg_a = pa.get("package", "?")
            pkg_b = pb.get("package", "?")
            src_a = ba.get("source", "unknown")
            src_b = bb.get("source", "unknown")

            if site_a == site_b:
                # Identical bullets + identical call site + different grades:
                # this is an unexplained inconsistency.
                msg = (
                    f"GRADE-INCONSISTENCY: PR #{na} ('{pkg_a}') grade={ga} [{src_a}] "
                    f"vs PR #{nb} ('{pkg_b}') grade={gb} [{src_b}] — identical "
                    f"changelog bullets and identical call site '{site_a}' produced "
                    f"different grades with no cited explanation.  "
                    f"Re-run or manually reconcile to the more conservative grade."
                )
                notes.setdefault(na, msg)
                notes.setdefault(nb, msg)
            else:
                # Different call sites: the grade difference MAY be valid.
                # Annotate both so reviewers see the cross-sibling context.
                for num, pkg, g, other_pkg, og, other_site in [
                    (na, pkg_a, ga, pkg_b, gb, site_b),
                    (nb, pkg_b, gb, pkg_a, ga, site_a),
                ]:
                    if num not in notes:
                        notes[num] = (
                            f"GRADE-DIFFERS-FROM-SIBLING: '{pkg}' graded {g} while "
                            f"release-train sibling '{other_pkg}' graded {og} — "
                            f"same changelog bullets but different call sites "
                            f"(sibling site: '{other_site}').  "
                            f"Difference is cited by distinct call-site exposure; "
                            f"verify that the site chosen for this PR is "
                            f"package-correct."
                        )
