#!/usr/bin/env python3
"""Slice breakability build-results.json to a selected PR subset."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any


PR_REF_KEYS = ("pr", "pr_number", "number", "pr_a", "pr_b")


def parse_pr_numbers(value: str) -> list[int]:
    """Parse a comma-separated PR list. Invalid tokens are rejected."""
    numbers: list[int] = []
    seen: set[int] = set()
    for token in (value or "").split(","):
        token = token.strip().lstrip("#")
        if not re.fullmatch(r"[0-9]+", token or ""):
            raise ValueError(f"invalid PR number token: {token!r}")
        number = int(token)
        if number not in seen:
            seen.add(number)
            numbers.append(number)
    if not numbers:
        raise ValueError("at least one PR number is required")
    return numbers


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        return int(value)
    return None


def _pr_refs(obj: Any) -> set[int]:
    refs: set[int] = set()
    if not isinstance(obj, dict):
        return refs
    for key in PR_REF_KEYS:
        number = _int_or_none(obj.get(key))
        if number is not None:
            refs.add(number)
    for key in ("prs", "pr_numbers", "related_prs"):
        values = obj.get(key)
        if isinstance(values, list):
            for value in values:
                number = _int_or_none(value)
                if number is not None:
                    refs.add(number)
    return refs


def _filter_pr_dict(values: Any, selected: set[int]) -> tuple[dict[str, Any], dict[str, Any]]:
    kept: dict[str, Any] = {}
    omitted: dict[str, Any] = {}
    if not isinstance(values, dict):
        return kept, omitted
    for key, value in values.items():
        number = _int_or_none(key)
        if number in selected:
            kept[str(key)] = value
        else:
            omitted[str(key)] = value
    return kept, omitted


def _filter_pr_ref_list(values: Any, selected: set[int]) -> tuple[list[Any], list[Any]]:
    kept: list[Any] = []
    omitted: list[Any] = []
    if not isinstance(values, list):
        return kept, omitted
    for value in values:
        refs = _pr_refs(value)
        if refs and refs <= selected:
            kept.append(value)
        else:
            omitted.append(value)
    return kept, omitted


def _filter_security_posture(security: Any, selected: set[int]) -> tuple[dict[str, Any], dict[str, int]]:
    if not isinstance(security, dict):
        return {}, {}
    filtered = copy.deepcopy(security)
    omitted_counts: dict[str, int] = {}
    omitted_due_to_subset: dict[str, Any] = {}

    for key in ("prs_fixing_alerts", "prs_with_cves"):
        kept, omitted = _filter_pr_dict(filtered.get(key), selected)
        filtered[key] = kept
        if omitted:
            omitted_counts[f"security_posture.{key}"] = len(omitted)
            omitted_due_to_subset[key] = omitted

    kept_fixes, omitted_fixes = _filter_pr_ref_list(filtered.get("cve_fixes"), selected)
    filtered["cve_fixes"] = kept_fixes
    if omitted_fixes:
        omitted_counts["security_posture.cve_fixes"] = len(omitted_fixes)
        omitted_due_to_subset["cve_fixes"] = omitted_fixes

    orphan_alerts = filtered.get("orphan_alerts")
    if isinstance(orphan_alerts, list) and orphan_alerts:
        # Orphans are repository-global, not attributable to selected PRs. Move them
        # aside so subset merge plans don't look like selected PRs caused/ignored them.
        omitted_counts["security_posture.orphan_alerts"] = len(orphan_alerts)
        omitted_due_to_subset["orphan_alerts"] = orphan_alerts
        filtered["orphan_alerts"] = []
        filtered["orphan_alerts_omitted_for_subset"] = len(orphan_alerts)

    filtered["scope"] = "subset"
    filtered["subset_note"] = (
        "PR-scoped security rows are limited to selected PRs; repository-global "
        "orphan alerts are moved to omitted_due_to_subset."
    )
    filtered["subset_pr_numbers"] = sorted(selected)
    filtered["alerts_fixable_by_merging"] = sum(
        int(v.get("alert_count", 0) or 0)
        for v in filtered.get("prs_fixing_alerts", {}).values()
        if isinstance(v, dict)
    )
    filtered["total_cves_in_prs"] = sum(
        len(v) if isinstance(v, list) else 1
        for v in filtered.get("prs_with_cves", {}).values()
    )
    if omitted_due_to_subset:
        filtered["omitted_due_to_subset"] = omitted_due_to_subset
    return filtered, omitted_counts


def _filter_govulncheck(govuln: Any, prs: dict[str, Any]) -> dict[str, Any]:
    filtered = copy.deepcopy(govuln) if isinstance(govuln, dict) else {}
    new_findings: set[str] = set()
    scanned = 0
    with_new = 0
    for pr in prs.values():
        if pr.get("vuln_status", "") in ("ok", "vulns_found", "ok_preexisting"):
            scanned += 1
        findings = pr.get("vuln_new_findings", [])
        if findings:
            with_new += 1
            for finding in findings:
                new_findings.add(str(finding))
    filtered.setdefault("main_baseline", {"status": "unknown", "findings": []})
    filtered["prs_scanned"] = scanned
    filtered["prs_with_new_vulns"] = with_new
    filtered["total_new_findings"] = sorted(new_findings)
    filtered["scope"] = "subset"
    return filtered


def filter_results(data: dict[str, Any], pr_numbers: list[int]) -> dict[str, Any]:
    selected = set(pr_numbers)
    result = copy.deepcopy(data)
    original_prs = data.get("prs", {}) if isinstance(data.get("prs"), dict) else {}
    prs = {str(num): copy.deepcopy(original_prs[str(num)]) for num in pr_numbers if str(num) in original_prs}
    missing = [num for num in pr_numbers if str(num) not in original_prs]

    result["prs"] = prs
    omitted_counts: dict[str, int] = {}
    omitted_prs = max(0, len(original_prs) - len(prs))
    if omitted_prs:
        omitted_counts["prs"] = omitted_prs

    cross_deps, omitted_cross = _filter_pr_ref_list(data.get("cross_pr_deps"), selected)
    result["cross_pr_deps"] = cross_deps
    if omitted_cross:
        omitted_counts["cross_pr_deps"] = len(omitted_cross)

    security, security_counts = _filter_security_posture(data.get("security_posture", {}), selected)
    if security:
        result["security_posture"] = security
        omitted_counts.update(security_counts)

    if "govulncheck" in data:
        result["govulncheck"] = _filter_govulncheck(data.get("govulncheck"), prs)

    meta = copy.deepcopy(data.get("metadata", {})) if isinstance(data.get("metadata"), dict) else {}
    meta["original_pr_count"] = meta.get("pr_count", len(original_prs))
    meta["pr_count"] = len(prs)
    meta["subset_requested"] = True
    meta["requested_pr_numbers"] = pr_numbers
    meta["selected_pr_numbers"] = [num for num in pr_numbers if str(num) in prs]
    meta["missing_pr_numbers"] = missing
    meta["subset_filtered_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    meta["subset_filter_version"] = 1
    if omitted_counts:
        meta["subset_omitted_counts"] = omitted_counts
    result["metadata"] = meta
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="source build-results.json")
    parser.add_argument("--prs", required=True, help="comma-separated PR numbers, e.g. 2,3,6")
    parser.add_argument("-o", "--output", help="output path (default: stdout)")
    args = parser.parse_args(argv)

    try:
        pr_numbers = parse_pr_numbers(args.prs)
        with open(args.input, encoding="utf-8") as f:
            data = json.load(f)
        filtered = filter_results(data, pr_numbers)
    except (OSError, ValueError, json.JSONDecodeError) as err:
        print(f"filter-build-results: {err}", file=sys.stderr)
        return 2

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(filtered, f, indent=2)
            f.write("\n")
    else:
        json.dump(filtered, sys.stdout, indent=2)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
