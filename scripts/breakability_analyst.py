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
    import_files = det.get("files_importing")
    if not isinstance(import_files, list):
        import_files = []
    reached = len(usages) > 0
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

    if warning_count >= 3:
        risk = "High"
        conf = "L4"
    elif warning_count >= 1:
        risk = "Medium"
        conf = "L3"
    else:
        risk = "Low"
        conf = "L2"

    evidence = " + ".join(signals) if signals else "all signals clean"
    return f"**Merge Risk:** {risk} (Evidence: {evidence} · Confidence: {conf})"


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

    if verdict in ("BUILD_FAILS", "BLOCKED"):
        build = pr.get("build", {})
        if build.get("verdict") == "pre_existing":
            return "Build has pre-existing failures (not caused by this upgrade). Review build infra separately."
        return "Fix build errors before merging."

    if verdict == "SAFE":
        if dep_type in ("dev", "devDependency", "devDependencies"):
            return "Safe to merge — dev dependency with no production impact."
        if not reached:
            return "Safe to merge — not imported by production code."
        if probe["state"] == "SAME":
            return "Safe to merge — behavioral probe confirms identical runtime behavior."
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
    if pr.get("test", {}).get("verdict") not in [None, "skip"]:
        count += 1
    if pr.get("deterministic", {}).get("api_changes", 0) > 0:
        count += 1
    if pr.get("deterministic", {}).get("changelogSignal"):
        count += 1
    if pr.get("deterministic", {}).get("import_files"):
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

    if build.get("verdict") == "pass":
        parts.append("Build passes with all dependencies resolving.")
    elif build.get("verdict") == "fail":
        parts.append("Build fails — fix build errors before merging.")
    elif build.get("verdict") == "pre_existing":
        parts.append("Build has pre-existing failures not caused by this upgrade.")

    if verdict == "SAFE":
        if dep_type in ("dev", "devDependency", "devDependencies"):
            parts.append("Dev dependency with no production impact.")
        elif not reach["reached"]:
            parts.append("Package is not imported by production code.")
        elif probe["state"] == "SAME":
            parts.append("Behavioral probe confirms runtime exports are identical.")
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

    return " ".join(parts) if parts else "Review required for this upgrade."


def _render_compact(pr: Dict) -> str:
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

    reach_file_count = len(reach["import_files"]) or len(set(u.get("file", "") for u in reach["usages"]))
    reach_text = f"{reach_file_count} files" if reach["reached"] else "not reached"
    cl_icon = "⚠️" if changelog_norm["is_breaking"] else "✅" if changelog_norm["available"] else "⬜"
    cl_text = "breaking" if changelog_norm["is_breaking"] else "clean" if changelog_norm["available"] else "n/a"

    explanation = _synthesize_explanation(pr)
    recommendation = _get_recommendation(pr)

    lines = [
        f"## {emoji} {verdict} — `{pkg}` {from_ver} → {to_ver} · {dep_type} · {bump}",
        merge_risk,
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
    probe_detail = "behavior unchanged" if probe["state"] == "SAME" else "behavior changed" if probe["state"] == "DIFFERENT" else "—"
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

    lines += [
        "---",
        f"🔬 Deterministic + Probe · 📅 {date.today().isoformat()}",
    ]

    return "\n".join(lines)


def render_pr_comment(pr: Dict[str, Any]) -> str:
    """Render compact PR comment (~40 lines)."""
    return _render_compact(pr)


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

    if prs_dict:
        results = []
        for pr_num_str, pr_data in prs_dict.items():
            if isinstance(pr_data, dict):
                pr_data.setdefault("pr_num", pr_num_str)
                results.append(pr_data)
    elif results_array:
        results = results_array
    else:
        print("No results found in build-results.json (checked 'prs' dict and 'results' array)", file=sys.stderr)
        sys.exit(1)

    if args.pr:
        results = [pr for pr in results if str(pr.get("pr_num")) == args.pr]
        if not results:
            print(f"PR #{args.pr} not found in results", file=sys.stderr)
            sys.exit(1)

    for pr in results:
        pr_num = pr.get("pr_num")
        if not pr_num:
            continue

        comment = render_pr_comment(pr)

        if args.stdout:
            print(comment)
        else:
            output_file = f"/tmp/pr-{pr_num}-comment.md"
            with open(output_file, "w") as f:
                f.write(comment)
            print(f"✅ Rendered PR #{pr_num} comment to {output_file}")


if __name__ == "__main__":
    main()
