#!/usr/bin/env python3
"""
breakability_analyst.py - Compact PR comment renderer for breakability analysis.

Reads build-results.json and produces ~40-line comments per PR with collapsible
evidence details. Called by breakability-agent.yml workflow (line 346).
"""
import json
import sys
import os
from typing import Dict, Any, List, Optional
from verdict_contract import authoritative_verdict as _authoritative_verdict


# ── Normalizers ───────────────────────────────────────────────────────────────

def _normalize_verdict(pr: Dict) -> Dict[str, str]:
    v = _authoritative_verdict(pr)
    return {
        "verdict": v.get("verdict", "REVIEW"),
        "confidence": v.get("confidence", "MEDIUM"),
        "severity": v.get("severity", "medium"),
        "priority": v.get("priority", "P2"),
    }


def _normalize_changelog(det: Dict) -> Dict[str, Any]:
    cl = det.get("changelogSignal")

    if not cl:
        return {"status": "missing", "bullets": [], "is_breaking": False, "available": False}

    if isinstance(cl, str):
        return {
            "status": cl,
            "bullets": [],
            "is_breaking": cl == "breaking",
            "available": cl != "missing"
        }

    if not isinstance(cl, dict):
        return {"status": "missing", "bullets": [], "is_breaking": False, "available": False}

    status = cl.get("status", "unknown")
    bullets = cl.get("bullets", [])

    if bullets is None:
        bullets = []
    elif isinstance(bullets, str):
        bullets = [bullets] if bullets else []
    elif not isinstance(bullets, list):
        bullets = []

    has_breaking_in_bullets = any(
        "BREAKING" in str(bullet).upper() or "BREAK" in str(bullet).upper()
        for bullet in bullets
    )

    _negation_patterns = ["no api change", "no breaking change", "bug fix and maintenance"]
    all_bullets_negated = (
        status == "breaking" and bullets and
        all(any(neg in str(b).lower() for neg in _negation_patterns) for b in bullets)
    )
    if all_bullets_negated:
        status = "clean"
        has_breaking_in_bullets = False

    is_breaking = status == "breaking" or has_breaking_in_bullets
    available = status != "missing" or len(bullets) > 0

    return {
        "status": status,
        "bullets": bullets,
        "is_breaking": is_breaking,
        "available": available
    }


def _normalize_test(test: Dict) -> Dict[str, Any]:
    if not test:
        return {"verdict": "skip", "exit_code": -1, "ran": False, "reason": "No test data"}

    if "ran" in test:
        ran = test.get("ran", False)
        exit_code = test.get("exit")
        if exit_code is None:
            exit_code = test.get("main_test_exit", -1)

        if not ran:
            verdict = "skip"
            reason = test.get("reason", "Tests not executed")
        elif exit_code == 0:
            verdict = "pass"
            reason = "All tests passed"
        elif exit_code is None:
            verdict = "skip"
            reason = "Test execution status unknown"
        else:
            output = test.get("output_tail", "")
            if "no test specified" in output or "Error: no test specified" in output:
                verdict = "skip"
                reason = "No test suite configured"
            else:
                verdict = "fail"
                reason = f"Tests failed with exit code {exit_code}"

        return {"verdict": verdict, "exit_code": exit_code, "ran": ran, "reason": reason}

    verdict = test.get("verdict", "skip")
    exit_code = test.get("exit_code", -1)
    reason = test.get("reason", "Test execution status")
    ran = verdict == "pass" or verdict == "fail"

    return {"verdict": verdict, "exit_code": exit_code, "ran": ran, "reason": reason}


def _normalize_probe(pr: Dict) -> Dict[str, Any]:
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})

    if not probe:
        return {"state": "NOT_RUN", "same_behavior": None, "evidence": {}}

    build_verdict = (pr.get("build") or {}).get("verdict", "")
    if build_verdict in ("fail", "pre_existing_plus_new"):
        return {"state": "PROBE_FAILED", "same_behavior": None, "evidence": probe}

    same_behavior = probe.get("same_behavior")

    if same_behavior is None:
        behavior_changed = probe.get("behavior_changed") or probe.get("changed_behavior")
        if behavior_changed is True:
            same_behavior = False
        elif behavior_changed is False:
            same_behavior = True
        elif behavior_changed == "unverified":
            same_behavior = None

    if same_behavior is None and "different" in probe:
        different = probe.get("different")
        if different is True:
            same_behavior = False
        elif different is False:
            same_behavior = True

    if same_behavior is True:
        state = "SAME"
    elif same_behavior is False:
        state = "DIFFERENT"
    else:
        old_sha = probe.get("old_sha256", "")[:16]
        new_sha = probe.get("new_sha256", "")[:16]
        if old_sha and new_sha:
            if old_sha == new_sha:
                state = "SAME"
                same_behavior = True
            else:
                state = "DIFFERENT"
                same_behavior = False
        else:
            state = "NOT_RUN"

    return {
        "state": state,
        "same_behavior": same_behavior,
        "evidence": probe
    }


def _normalize_reachability(pr: Dict) -> Dict[str, Any]:
    det = pr.get("deterministic") or {}
    usages = det.get("usages")
    if not isinstance(usages, list):
        usages = []
    import_files = pr.get("files_importing")
    if not isinstance(import_files, list):
        import_files = det.get("files_importing")
        if not isinstance(import_files, list):
            import_files = []
    reached = len(import_files) > 0 or len(usages) > 0
    pkg = pr.get("package", "")
    if not reached and pkg.startswith("@types/"):
        dep_type = pr.get("dep_type", "")
        if dep_type in ("production", "dependency", "dependencies"):
            reached = True
            import_files = ["(ambient type declarations — all TypeScript files)"]
    return {"usages": usages, "import_files": import_files, "reached": reached}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _merge_risk_tag(pr: Dict[str, Any]) -> str:
    warning_count = 0
    signals = []
    probe = _normalize_probe(pr)
    reach = _normalize_reachability(pr)
    build = pr.get("build", {})
    test_norm = _normalize_test(pr.get("test", {}))
    det = pr.get("deterministic", {})
    changelog_norm = _normalize_changelog(det)

    if build.get("verdict") == "fail":
        warning_count += 1
        signals.append("build fail")
    if test_norm["verdict"] == "fail":
        warning_count += 1
        signals.append("test fail")
    if probe["state"] == "DIFFERENT":
        warning_count += 1
        signals.append("probe DIFFERENT")
    if reach.get("reached"):
        warning_count += 1
        signals.append("reachable")
    if changelog_norm["is_breaking"]:
        warning_count += 1
        signals.append("changelog breaking")

    evidence_layers = _count_evidence_layers(pr)
    ecosystem = pr.get("ecosystem", "")

    if warning_count >= 3:
        risk = "High"
        conf = "RC-High"
    elif warning_count >= 1:
        risk = "Medium"
        conf = "RC-Med"
    else:
        risk = "Low"
        conf = "RC-Low"

    if signals:
        evidence_str = " + ".join(signals)
    elif evidence_layers <= 1 and ecosystem != "actions":
        evidence_str = "limited evidence gathered"
    elif ecosystem == "actions":
        evidence_str = "CI-only action — no runtime impact"
    else:
        evidence_str = "all signals clean"
    return f"**Merge Risk:** {risk} (Evidence: {evidence_str} · Confidence: {conf})"


def _get_recommendation(pr: Dict) -> str:
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    pkg = pr.get("package", "unknown")
    dep_type = pr.get("dep_type", "dependency")
    probe = _normalize_probe(pr)
    reach_norm = _normalize_reachability(pr)
    reached = reach_norm["reached"]
    files = reach_norm["import_files"]
    det = pr.get("deterministic", {})
    changelog_norm = _normalize_changelog(det)
    test_norm = _normalize_test(pr.get("test", {}))

    if verdict in ("BUILD_FAILS", "BLOCKED"):
        build = pr.get("build", {})
        if build.get("verdict") == "pre_existing":
            return "Build has pre-existing failures (not caused by this upgrade). Review build infra separately."
        if test_norm["verdict"] == "fail":
            return "Fix build and test failures before merging."
        return "Fix build errors before merging."

    if test_norm["verdict"] == "fail":
        return f"Tests fail (exit {test_norm['exit_code']}). Investigate test failures before merging."

    if verdict == "SAFE":
        if dep_type in ("dev", "devDependency", "devDependencies"):
            return "Safe to merge — dev dependency with no production impact."
        if not reached:
            return "Safe to merge — not imported by production code."
        if probe["state"] == "SAME":
            return "Safe to merge — behavioral probe confirms identical runtime behavior."
        if changelog_norm["is_breaking"]:
            return "Changelog lists breaking changes. Review callsites, then merge."
        return "Safe to merge. Build passes and no breaking changes detected."

    parts = []
    if changelog_norm["is_breaking"]:
        bullets = changelog_norm["bullets"]
        if bullets:
            parts.append(f"Review changelog breaking changes ({bullets[0][:80]})")
        else:
            parts.append("Review the changelog for breaking changes")

    if probe["state"] == "DIFFERENT":
        parts.append("verify behavioral changes are compatible with your usage")

    if reached and files:
        file_ref = (f"`{files[0]}`" if len(files) == 1
                    else f"`{files[0]}` and {len(files)-1} other file(s)")
        parts.append(f"check callsites in {file_ref}")
    elif reached:
        parts.append("verify affected callsites are compatible")

    if not parts:
        parts.append(f"Review the changelog for {pkg}")

    return ". ".join(parts).rstrip(".") + ", then merge."


def _count_evidence_layers(pr: Dict) -> int:
    count = 0
    if pr.get("build", {}).get("verdict"):
        count += 1
    test_norm = _normalize_test(pr.get("test", {}))
    if test_norm["verdict"] not in [None, "skip"]:
        count += 1
    if (pr.get("deterministic", {}).get("api_changes") or 0) > 0:
        count += 1
    if pr.get("deterministic", {}).get("changelogSignal"):
        count += 1
    if pr.get("files_importing") or pr.get("deterministic", {}).get("files_importing"):
        count += 1
    if pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe"):
        count += 1
    if pr.get("ai_adjudication"):
        count += 1
    return count


# ── Compact renderer ─────────────────────────────────────────────────────────

def _synthesize_explanation(pr: Dict) -> str:
    """Generate plain-English explanation from signal data.
    Deterministic replacement for the AI arbiter layer."""
    parts = []
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    build = pr.get("build", {})
    probe = _normalize_probe(pr)
    reach = _normalize_reachability(pr)
    det = pr.get("deterministic", {})
    changelog_norm = _normalize_changelog(det)
    dep_type = pr.get("dep_type", "dependency")

    test_norm = _normalize_test(pr.get("test", {}))

    if build.get("verdict") == "pass":
        parts.append("Build passes with all dependencies resolving.")
    elif build.get("verdict") == "fail":
        parts.append("Build fails — fix build errors before merging.")
    elif build.get("verdict") == "pre_existing":
        parts.append("Build has pre-existing failures not caused by this upgrade.")
    elif build.get("verdict") == "pre_existing_plus_new":
        parts.append("Build has new errors introduced by this upgrade on top of pre-existing failures.")

    if test_norm["verdict"] == "fail":
        parts.append(f"Tests fail (exit {test_norm['exit_code']}) — investigate before merging.")

    if verdict == "SAFE":
        if dep_type in ("dev", "devDependency", "devDependencies"):
            parts.append("Dev dependency with no production impact.")
        elif not reach["reached"]:
            parts.append("Package is not imported by production code.")
        elif probe["state"] == "SAME":
            parts.append("Behavioral probe confirms runtime exports are identical.")
        else:
            if changelog_norm["is_breaking"]:
                parts.append("Breaking changes listed but assessed safe at current usage.")
            else:
                parts.append("No breaking changes detected.")
        if changelog_norm["is_breaking"] and changelog_norm["bullets"]:
            bullet = changelog_norm["bullets"][0]
            if len(bullet) > 100:
                bullet = bullet[:97] + "..."
            parts.append(f"Changelog notes: {bullet}")
            if not reach["reached"]:
                parts.append("Package is unreachable so this has no production impact.")
    elif verdict == "REVIEW":
        if probe["state"] == "DIFFERENT":
            parts.append("Behavioral probe confirms runtime behavior has changed.")
        if changelog_norm["is_breaking"] and changelog_norm["bullets"]:
            bullet = changelog_norm["bullets"][0]
            if len(bullet) > 100:
                bullet = bullet[:97] + "..."
            parts.append(f"Changelog: {bullet}")
        if reach["reached"]:
            files = reach["import_files"]
            if files:
                parts.append(f"Package is imported by {len(files)} production file(s) — verify callsite compatibility.")
    elif verdict in ("BUILD_FAILS", "BLOCKED"):
        parts.append("Resolve build issues before this upgrade can proceed.")

    build_output = build.get("output_tail", "") or build.get("stdout", "")
    import re
    vuln_match = re.search(r'(\d+)\s+(high|critical)\s+severity\s+vulnerabilit', build_output, re.IGNORECASE)
    if vuln_match:
        parts.append(f"⚠️ npm audit: {vuln_match.group(0)}ies found.")

    return " ".join(parts) if parts else "Review required for this upgrade."


def _render_compact(pr: Dict, cross_deps: Optional[List[Dict]] = None) -> str:
    """Render a compact PR comment (~40 lines)."""
    from datetime import date

    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    pkg = pr.get("package", "unknown")
    from_ver = pr.get("from", "?")
    to_ver = pr.get("to", "?")
    bump = pr.get("bump", "unknown")
    dep_type = pr.get("dep_type", "dependency")

    emoji = {"SAFE": "✅", "REVIEW": "🟠", "BUILD_FAILS": "❌", "BLOCKED": "🔴"}.get(verdict, "⚠️")
    verification_level = pr.get("verification_level", -1)
    vlevel_labels = {0: "L0 Unresolved", 1: "L1 Dep-resolved", 2: "L2 Type-checked",
                     3: "L3 Symbols-verified", 4: "L4 Tests-pass", 5: "L5 Fully-verified"}
    vlevel_str = vlevel_labels.get(verification_level, "")
    merge_risk = _merge_risk_tag(pr)

    build = pr.get("build", {})
    build_v = build.get("verdict", "unknown")
    build_icon = {"pass": "✅", "fail": "❌", "pre_existing": "⚠️"}.get(build_v, "⬜")

    test_norm = _normalize_test(pr.get("test", {}))
    test_icon = {"pass": "✅", "fail": "❌", "skip": "⬜"}.get(test_norm["verdict"], "⬜")
    test_suffix = f" (exit {test_norm['exit_code']})" if test_norm["verdict"] == "fail" else ""

    probe = _normalize_probe(pr)
    probe_state_display = probe["state"].lower().replace("_", " ")
    probe_icon = {"SAME": "✅", "DIFFERENT": "⚠️"}.get(probe["state"], "⬜")

    det = pr.get("deterministic", {})
    reach = _normalize_reachability(pr)
    changelog_norm = _normalize_changelog(det)
    api_changes = det.get("api_changes") or 0

    is_ambient = any("ambient" in str(f).lower() for f in reach["import_files"])
    reach_file_count = len(reach["import_files"]) or len(set(u.get("file", "") for u in reach["usages"]))
    if is_ambient:
        reach_text = "all TS files (ambient)"
    elif reach["reached"]:
        reach_text = f"{reach_file_count} files"
    else:
        reach_text = "not reached"
    cl_icon = "⚠️" if changelog_norm["is_breaking"] else "✅" if changelog_norm["available"] else "⬜"
    cl_text = "breaking" if changelog_norm["is_breaking"] else "clean" if changelog_norm["available"] else "n/a"

    explanation = _synthesize_explanation(pr)
    recommendation = _get_recommendation(pr)

    vlevel_badge = f" · Verification: {vlevel_str}" if vlevel_str else ""
    lines = [
        f"## {emoji} {verdict} — `{pkg}` {from_ver} → {to_ver} · {dep_type} · {bump}",
        merge_risk + vlevel_badge,
        "",
        f"**Build:** {build_icon} {build_v} · **Tests:** {test_icon} {test_norm['verdict']}{test_suffix} · **Probe:** {probe_icon} {probe_state_display}",
        f"**Reachability:** {reach_text} · **Changelog:** {cl_icon} {cl_text} · **API Diff:** {api_changes} changes",
        "",
        "### What this means",
        explanation,
        "",
        f"**Recommendation:** {recommendation}",
        "",
    ]

    cl_detail = changelog_norm["bullets"][0][:80] if changelog_norm["bullets"] else changelog_norm["status"]
    probe_ev = probe["evidence"]
    if probe["state"] == "DIFFERENT":
        probe_detail = probe_ev.get("changed_behavior", "") or probe_ev.get("rationale", "") or "behavior changed"
        probe_detail = probe_detail[:120]
    elif probe["state"] == "SAME":
        probe_detail = "behavior unchanged"
    else:
        probe_detail = "—"
    test_detail = test_norm["reason"] if test_norm["verdict"] != "pass" else f"exit {test_norm['exit_code']}"

    lines += [
        "<details><summary>📋 Evidence layers</summary>",
        "",
        "| Layer | Signal | Detail |",
        "|-------|--------|--------|",
        f"| Build | {build_icon} {build_v} | exit {build.get('pr_exit', build.get('main_exit', '?'))} |",
        f"| Tests | {test_icon} {test_norm['verdict']} | {test_detail} |",
        f"| API Diff | {'⚠️ breaking' if api_changes > 0 else '✅ clean'} | {api_changes} symbol(s) |",
        f"| Changelog | {cl_icon} {cl_text} | {cl_detail} |",
        f"| Reachability | {'⚠️ reached' if reach['reached'] else '✅ not reached'} | {reach_file_count} imports |",
        f"| Probe | {probe_icon} {probe_state_display} | {probe_detail} |",
        "",
        "</details>",
        "",
    ]

    build_output = build.get("output_tail", "")
    if build_output:
        lines += [
            "<details><summary>🔨 Build output</summary>",
            "",
            "```",
            build_output[:500],
            "```",
            "",
            "</details>",
            "",
        ]

    test_data = pr.get("test", {})
    test_output = (test_data.get("output_tail") or test_data.get("stdout") or test_data.get("output") or "").strip()
    if test_norm["verdict"] == "fail" and test_output:
        lines += [
            "<details><summary>🧪 Test output</summary>",
            "",
            "```",
            test_output[:500],
            "```",
            "",
            "</details>",
            "",
        ]

    if probe["state"] == "DIFFERENT":
        old_out = probe_ev.get("observed_from", "") or probe_ev.get("old_output", "")
        new_out = probe_ev.get("observed_to", "") or probe_ev.get("new_output", "")
        changed_summary = probe_ev.get("changed_behavior", "") or probe_ev.get("summary", "") or probe_ev.get("rationale", "")
        old_hash = probe_ev.get("old_hash", "")
        new_hash = probe_ev.get("new_hash", "")
        if not old_out and not new_out and old_hash and new_hash:
            old_out = f"hash:{old_hash[:12]}"
            new_out = f"hash:{new_hash[:12]}"
        if old_out or new_out or changed_summary:
            lines.append("<details><summary>🔬 Probe diff — what changed</summary>")
            lines.append("")
            if changed_summary:
                lines.append(f"**Change:** {changed_summary[:300]}")
                lines.append("")
            if old_out:
                lines.append(f"**Before:** `{old_out[:200]}`")
            if new_out:
                lines.append(f"**After:** `{new_out[:200]}`")
            evidence_text = probe_ev.get("evidence", "")
            if evidence_text:
                lines.append("")
                lines.append(f"**Evidence:** {evidence_text[:300]}")
            lines += ["", "</details>", ""]

    import_list = reach["import_files"]
    if not import_list and reach["usages"]:
        import_list = sorted(set(u.get("file", "") for u in reach["usages"] if u.get("file")))
    if import_list:
        lines.append(f"<details><summary>📁 Files importing this package ({len(import_list)})</summary>")
        lines.append("")
        for f in import_list[:10]:
            lines.append(f"- `{f}`")
        if len(import_list) > 10:
            lines.append(f"- ... and {len(import_list) - 10} more")
        lines += ["", "</details>", ""]

    if changelog_norm["is_breaking"] and changelog_norm["bullets"]:
        lines.append("<details><summary>📋 Changelog breaking changes</summary>")
        lines.append("")
        for b in changelog_norm["bullets"][:5]:
            lines.append(f"- {b}")
        lines += ["", "</details>", ""]

    fixes_cves = pr.get("fixes_cves") or []
    cve_details = pr.get("cve_details") or []
    all_cves = []
    seen_cve_ids = set()
    for cve in fixes_cves:
        cid = cve.get("cve_id") or ""
        if cid and cid not in seen_cve_ids:
            seen_cve_ids.add(cid)
            sev = cve.get("severity", "unknown")
            score = cve.get("cvss_score", "")
            url = cve.get("advisory_url", "")
            patched = cve.get("first_patched_version", "")
            all_cves.append({"id": cid, "severity": sev, "score": score, "url": url, "patched": patched, "fixes": True})
    for cve in cve_details:
        cid = cve.get("cve_id") or cve.get("ghsa_id") or ""
        if cid and cid not in seen_cve_ids:
            seen_cve_ids.add(cid)
            sev = cve.get("severity", "unknown")
            score = cve.get("cvss_score", "")
            url = cve.get("advisory_url", "")
            all_cves.append({"id": cid, "severity": sev, "score": score, "url": url, "patched": "", "fixes": False})
    if all_cves:
        sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get
        lines.append("### Security Advisories")
        lines.append("")
        for cve in all_cves:
            icon = sev_icon(cve["severity"], "⚪")
            score_str = f" · CVSS {cve['score']}" if cve["score"] else ""
            link = f" · [advisory]({cve['url']})" if cve["url"] else ""
            fix_str = " — **fixed by this PR**" if cve["fixes"] else ""
            patched_str = f" (patched in {cve['patched']})" if cve["patched"] else ""
            lines.append(f"- {icon} **{cve['id']}** ({cve['severity']}{score_str}){patched_str}{fix_str}{link}")
        lines.append("")

    pr_num = str(pr.get("pr_num", ""))
    if cross_deps:
        related = [d for d in cross_deps
                   if str(d.get("pr_a")) == pr_num or str(d.get("pr_b")) == pr_num]
        if related:
            lines.append("### Coupled PRs")
            lines.append("")
            for dep in related:
                other = dep["pr_b"] if str(dep["pr_a"]) == pr_num else dep["pr_a"]
                lines.append(f"- PR #{other}: {dep.get('reason', '')} — {dep.get('merge_order', '')}")
            lines.append("")

    lines += [
        "---",
        f"🔬 Deterministic + Probe · 📅 {date.today().isoformat()}",
    ]

    return "\n".join(lines)


def render_pr_comment(pr: Dict[str, Any], cross_deps: Optional[List[Dict]] = None) -> str:
    """Render compact PR comment (~40 lines)."""
    return _render_compact(pr, cross_deps=cross_deps)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Render breakability analysis PR comments")
    parser.add_argument("build_results", help="Path to build-results.json")
    parser.add_argument("--pr", type=str, help="Render only specific PR number")
    parser.add_argument("--stdout", action="store_true", help="Write to stdout instead of files")
    args = parser.parse_args()

    with open(args.build_results) as f:
        data = json.load(f)

    prs_dict = data.get("prs", {})
    results_array = data.get("results", [])

    if results_array:
        results = results_array
    elif prs_dict:
        results = []
        for pr_num_str, pr_data in prs_dict.items():
            if isinstance(pr_data, dict):
                pr_data.setdefault("pr_num", pr_num_str)
                results.append(pr_data)
    else:
        print("No results found in build-results.json (checked 'prs' dict and 'results' array)", file=sys.stderr)
        sys.exit(1)

    cross_deps = data.get("cross_pr_deps") or []

    if args.pr:
        results = [pr for pr in results if str(pr.get("pr_num")) == args.pr]
        if not results:
            print(f"PR #{args.pr} not found in results", file=sys.stderr)
            sys.exit(1)

    for pr in results:
        pr_num = pr.get("pr_num")
        if not pr_num:
            continue

        comment = render_pr_comment(pr, cross_deps=cross_deps)

        if args.stdout:
            print(comment)
        else:
            output_file = f"/tmp/pr-{pr_num}-comment.md"
            with open(output_file, "w") as f:
                f.write(comment)
            print(f"✅ Rendered PR #{pr_num} comment to {output_file}")


if __name__ == "__main__":
    main()
