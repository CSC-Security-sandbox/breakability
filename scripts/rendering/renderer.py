"""
rendering.renderer — Core comment renderer.

Combines normalized signals and helper computations into the final Markdown
PR comment string.
"""
import os
import re
from typing import Dict, Any, List, Optional

from rendering.normalizers import (
    _normalize_verdict,
    _normalize_changelog,
    _normalize_test,
    _normalize_probe,
    _normalize_reachability,
)
from rendering.helpers import (
    _merge_risk_tag,
    _get_recommendation,
    _per_layer_confidence,
    _build_expanded_layer_sections,
    _build_risk_assessment,
    _build_numbered_recommendations,
    _is_api_diff_skipped,
    _extract_error_lines,
)

__all__ = [
    "render_pr_comment",
]


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
    vuln_match = re.search(r'(\d+)\s+(high|critical)\s+severity\s+vulnerabilit', build_output, re.IGNORECASE)
    if vuln_match:
        parts.append(f"⚠️ npm audit: {vuln_match.group(0)}ies found.")

    return " ".join(parts) if parts else "Review required for this upgrade."


def _render_compact(pr: Dict, cross_deps: Optional[List[Dict]] = None,
                    prs_lookup: Optional[Dict[str, Dict]] = None) -> str:
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

    ecosystem = pr.get("ecosystem", "")
    build = pr.get("build", {})
    build_v = build.get("verdict", "unknown")
    is_actions = ecosystem == "actions"
    if is_actions:
        build_icon = "ℹ️"
    else:
        build_icon = {"pass": "✅", "fail": "❌", "pre_existing": "⚠️"}.get(build_v, "⬜")

    test_norm = _normalize_test(pr.get("test", {}))
    test_icon = {"pass": "✅", "fail": "❌", "skip": "⬜", "pre_existing": "⚠️"}.get(test_norm["verdict"], "⬜")
    if test_norm["verdict"] == "pre_existing":
        test_suffix = f" (exit {test_norm['exit_code']}, same on main)"
    elif test_norm["verdict"] == "fail":
        test_suffix = f" (exit {test_norm['exit_code']})"
    else:
        test_suffix = ""

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

    api_diff_skipped = _is_api_diff_skipped(pr)
    build_display = "CI-only" if is_actions else build_v
    api_diff_text = "unavailable" if api_diff_skipped else f"{api_changes} changes"
    vlevel_badge = f" · Verification: {vlevel_str}" if vlevel_str else ""
    lines = [
        "<!-- breakability-check -->",
        f"## {emoji} {verdict} — `{pkg}` {from_ver} → {to_ver} · {dep_type} · {bump}",
        merge_risk + vlevel_badge,
        "",
        f"**Build:** {build_icon} {build_display} · **Tests:** {test_icon} {test_norm['verdict']}{test_suffix} · **Probe:** {probe_icon} {probe_state_display}",
        f"**Reachability:** {reach_text} · **Changelog:** {cl_icon} {cl_text} · **API Diff:** {api_diff_text}",
        "",
        "### What this means",
        explanation,
        "",
        "### Recommendation",
        "",
    ]
    rec_steps = _build_numbered_recommendations(pr)
    for i, step in enumerate(rec_steps, 1):
        lines.append(f"{i}. {step}")
    lines.append("")

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

    layer_conf = _per_layer_confidence(build_v, test_norm, api_changes, changelog_norm, reach, probe,
                                       api_diff_skipped=api_diff_skipped, ecosystem=ecosystem)

    if is_actions:
        build_row = f"| Build | {build_icon} CI-only | no build applicable | {layer_conf['Build'][0]} — {layer_conf['Build'][1]} |"
    else:
        build_row = f"| Build | {build_icon} {build_v} | exit {build.get('pr_exit', build.get('main_exit', '?'))} | {layer_conf['Build'][0]} — {layer_conf['Build'][1]} |"

    if api_diff_skipped:
        api_row = f"| API Diff | ⬜ unavailable | tool not available | {layer_conf['API Diff'][0]} — {layer_conf['API Diff'][1]} |"
    elif api_changes > 0:
        api_row = f"| API Diff | ⚠️ breaking | {api_changes} symbol(s) | {layer_conf['API Diff'][0]} — {layer_conf['API Diff'][1]} |"
    else:
        api_row = f"| API Diff | ✅ clean | {api_changes} symbol(s) | {layer_conf['API Diff'][0]} — {layer_conf['API Diff'][1]} |"

    lines += [
        "### Evidence Summary",
        "",
        "| Layer | Signal | Detail | Confidence |",
        "|-------|--------|--------|------------|",
        build_row,
        f"| Tests | {test_icon} {test_norm['verdict']} | {test_detail} | {layer_conf['Tests'][0]} — {layer_conf['Tests'][1]} |",
        api_row,
        f"| Changelog | {cl_icon} {cl_text} | {cl_detail} | {layer_conf['Changelog'][0]} — {layer_conf['Changelog'][1]} |",
        f"| Reachability | {'⚠️ reached' if reach['reached'] else '✅ not reached'} | {reach_file_count} imports | {layer_conf['Reachability'][0]} — {layer_conf['Reachability'][1]} |",
        f"| Probe | {probe_icon} {probe_state_display} | {probe_detail} | {layer_conf['Probe'][0]} — {layer_conf['Probe'][1]} |",
    ]

    ai_adj = pr.get("ai_adjudication", {})
    if ai_adj and isinstance(ai_adj, dict):
        ai_verdict = ai_adj.get("final_verdict", ai_adj.get("verdict", "—"))
        ai_conf = ai_adj.get("confidence", "—")
        ai_icon = {"SAFE": "✅", "REVIEW": "🟠"}.get(ai_verdict, "⬜")
        lines.append(f"| AI Arbiter | {ai_icon} {ai_verdict} | confidence: {ai_conf} |")
    else:
        lines.append("| AI Arbiter | ⬜ not run | — |")

    lines.append("")

    if is_actions:
        build_check_text = "CI-only dependency — no build applicable"
    else:
        build_check_text = f"Installed `{pkg}@{to_ver}` and ran full build pipeline"
    if api_diff_skipped:
        api_check_text = "API diff tool unavailable — no symbol comparison performed"
    else:
        api_check_text = f"Compared exported symbols between {from_ver} and {to_ver}"
    if test_norm["verdict"] == "pre_existing":
        test_check_text = "Ran project test suite — failures match main branch (pre-existing)"
    elif test_norm["ran"]:
        test_check_text = "Ran project test suite"
    else:
        test_check_text = "No test suite executed"
    lines += [
        "### How we checked",
        "",
        f"- **Build**: {build_check_text}",
        f"- **Tests**: {test_check_text}",
        f"- **API Diff**: {api_check_text}",
        f"- **Changelog**: {'Parsed release notes for breaking-change markers' if changelog_norm['available'] else 'No changelog found for this version range'}",
        f"- **Reachability**: Scanned project source for imports of `{pkg}`",
        f"- **Probe**: {'Compared runtime behavior before/after upgrade' if probe['state'] != 'NOT_RUN' else 'Behavioral probe was not executed'}",
        "",
    ]

    lines += _build_expanded_layer_sections(build, build_v, test_norm, api_changes,
                                            changelog_norm, reach, probe, pkg, from_ver, to_ver,
                                            pr, layer_conf)
    lines.append("")
    if verdict in ("REVIEW", "BUILD_FAILS", "BLOCKED"):
        lines += _build_risk_assessment(pr, verdict, build_v, test_norm, api_changes,
                                         changelog_norm, reach, probe, pkg, from_ver, to_ver, dep_type, bump)
        lines.append("")

    lines += [
        "<details><summary>Verdict logic</summary>",
        "",
        "```",
        f"build      = {build_v.upper()}",
        f"tests      = {test_norm['verdict'].upper()}",
        f"probe      = {probe['state']}",
        f"reachable  = {str(reach['reached']).upper()}",
        f"changelog  = {'BREAKING' if changelog_norm['is_breaking'] else 'CLEAN'}",
        f"verdict    = {verdict}",
        "```",
        "",
        "</details>",
        "",
    ]

    ecosystem = pr.get("ecosystem", "npm")
    lines += ["<details><summary>Verification commands</summary>", "", "```bash"]
    if ecosystem == "gomod":
        lines += [
            f"# Install and build with the new version",
            f"go get {pkg}@v{to_ver}",
            f"go build ./...",
            "",
            f"# Run tests",
            f"go test ./...",
            "",
            f"# Check package docs",
            f"go doc {pkg}",
            "",
            f"# Check your imports",
            f'grep -r "{pkg}" --include="*.go" -l .',
        ]
    elif ecosystem == "actions":
        lines += [
            f"# Check which workflows use this action",
            f'grep -r "uses: {pkg}" --include="*.yml" --include="*.yaml" -l .github/',
        ]
    else:
        lines += [
            f"# Install and build with the new version",
            f"npm install {pkg}@{to_ver}",
            f"npm run build",
            "",
            f"# Run tests",
            f"npm test",
            "",
            f"# Check what changed in the API",
            f"npm info {pkg} --json | jq '.versions'",
            "",
            f"# Check your imports",
            f"grep -r '{pkg}' --include='*.ts' --include='*.js' -l .",
        ]
    lines += ["```", "", "</details>", ""]

    build_output = build.get("output_tail", "")
    if build_output and not is_actions:
        display_output = _extract_error_lines(build_output, 500)
        lines += [
            "<details><summary>🔨 Build output</summary>",
            "",
            "```",
            display_output,
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
        if old_hash and new_hash:
            lines.append(f"**Old SHA256:** `{old_hash}`")
            lines.append(f"**New SHA256:** `{new_hash}`")
            lines.append("")
        if not old_out and not new_out and old_hash and new_hash:
            old_out = f"sha256:{old_hash}"
            new_out = f"sha256:{new_hash}"
        if changed_summary:
            lines.append(f"**Change:** {changed_summary[:300]}")
            lines.append("")
        if old_out or new_out:
            lines.append("<details><summary>🔬 Probe diff — what changed</summary>")
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
    usage_refs = []
    if reach["usages"]:
        for u in reach["usages"]:
            uf = u.get("file", "")
            ul = u.get("line")
            if uf:
                usage_refs.append(f"{uf}:{ul}" if ul else uf)
        usage_refs = sorted(set(usage_refs))
    if not import_list and usage_refs:
        import_list = usage_refs
    elif import_list and usage_refs:
        file_to_ref = {}
        for ref in usage_refs:
            base = ref.split(":")[0]
            file_to_ref.setdefault(base, []).append(ref)
        enriched = []
        for f in import_list:
            if f in file_to_ref:
                enriched.extend(file_to_ref[f])
            else:
                enriched.append(f)
        import_list = enriched
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
            blocked_others = []
            for dep in related:
                other = str(dep["pr_b"] if str(dep["pr_a"]) == pr_num else dep["pr_a"])
                other_verdict = ""
                if prs_lookup and other in prs_lookup:
                    other_pr = prs_lookup[other]
                    other_verdict_norm = _normalize_verdict(other_pr)
                    other_verdict = other_verdict_norm.get("verdict", "")
                icon = "⛔" if other_verdict in ("BLOCKED", "BUILD_FAILS") else "🔗"
                line = f"- {icon} PR #{other}: {dep.get('reason', '')} — {dep.get('merge_order', '')}"
                if other_verdict in ("BLOCKED", "BUILD_FAILS"):
                    line += f" ⚠️ **PR #{other} is currently {other_verdict}**"
                    blocked_others.append(other)
                lines.append(line)
            if blocked_others:
                blocked_str = ", ".join(f"#{n}" for n in blocked_others)
                lines.append(f"")
                lines.append(f"> ⚠️ **Warning:** {blocked_str} {'is' if len(blocked_others)==1 else 'are'} currently blocked. Resolve before merging this group.")
            lines.append("")

    merge_plan_issue = os.environ.get("MERGE_PLAN_ISSUE", "")
    run_url = os.environ.get("ANALYSIS_RUN_URL", "")

    lines.append("---")
    footer = (f"Mode: Deterministic + Behavioral Probe · Model: template-fallback · "
              f"Analyzed: {date.today().isoformat()}")
    lines.append(footer)
    if merge_plan_issue:
        lines.append(f"Merge plan: #{merge_plan_issue}")
    if run_url:
        lines.append(f"[Analysis run]({run_url})")

    return "\n".join(lines)


def render_pr_comment(pr: Dict[str, Any], cross_deps: Optional[List[Dict]] = None,
                      prs_lookup: Optional[Dict[str, Dict]] = None) -> str:
    """Render compact PR comment (~40 lines)."""
    return _render_compact(pr, cross_deps=cross_deps, prs_lookup=prs_lookup)
