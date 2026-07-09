#!/usr/bin/env python3
"""Generate merge plan issue body — AI-enriched or template fallback.

Reads build-results.json + optional ALL_OPEN_PRS env var (from gh pr list),
calls the AI backend with breakability-prompt.md Section 5 context to produce
a rich merge plan, falling back to a deterministic template when AI is
unavailable.

Usage:
  generate_ai_merge_plan.py <build-results.json> \
    [--prompt prompts/breakability-prompt.md] \
    [--model claude-4.5-sonnet] \
    [--run-url URL]
"""
import argparse
import json
import os
import sys
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ai_backend import Backend
from verdict_contract import authoritative_verdict as _authoritative_verdict


def _get_verdict(pr: Dict) -> str:
    """Get the authoritative verdict for a PR, falling back to verdict_v2 or build verdict."""
    has_signals = bool(pr.get("build") or pr.get("test"))
    if has_signals:
        try:
            av = _authoritative_verdict(pr)
            return av.get("verdict", "REVIEW").upper()
        except Exception:
            pass
    v2 = pr.get("verdict_v2", {})
    return v2.get("verdict", pr.get("build", {}).get("verdict", "REVIEW")).upper()


def _categorize_prs(prs: Dict[str, Dict]) -> Dict[str, List[Tuple[str, Dict]]]:
    safe, glance, review, blocked, unverified, skipped = [], [], [], [], [], []
    for num, pr in sorted(prs.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        if pr.get("build", {}).get("verdict") == "skipped":
            skipped.append((num, pr))
            continue
        verdict = _get_verdict(pr)
        if verdict == "SAFE":
            safe.append((num, pr))
        elif verdict == "GLANCE":
            glance.append((num, pr))
        elif verdict in ("BLOCKED", "BUILD_FAILS"):
            blocked.append((num, pr))
        elif verdict == "UNVERIFIED":
            unverified.append((num, pr))
        else:
            review.append((num, pr))
    return {
        "safe": safe, "glance": glance, "review": review, "blocked": blocked,
        "unverified": unverified, "skipped": skipped,
    }


def _pr_row(num: str, pr: Dict) -> str:
    pkg = pr.get("package", "?")
    frm = pr.get("from", "?")
    to = pr.get("to", "?")
    bump = pr.get("bump", "?")
    dep = pr.get("dep_type", "?")
    vl = pr.get("verification_label", "?")
    return f"| #{num} | `{pkg}` | {frm} → {to} | {bump} | {dep} | {vl} |"


def _parse_all_open_prs() -> Dict[str, str]:
    raw = os.environ.get("ALL_OPEN_PRS", "").strip()
    result: Dict[str, str] = {}
    for line in raw.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].strip().isdigit():
            result[parts[0].strip()] = parts[1].strip()
    return result


def generate_template_plan(data: Dict[str, Any], run_url: Optional[str] = None,
                           model_name: str = "template-fallback") -> str:
    prs = data.get("prs", {})
    meta = data.get("metadata", {})
    cross_deps = data.get("cross_pr_deps", [])
    mode = meta.get("mode", "advisory")
    repo = meta.get("repo", "unknown")

    all_open = _parse_all_open_prs()
    analyzed_nums = set(prs.keys())
    not_analyzed = {n: t for n, t in all_open.items() if n not in analyzed_nums}
    total_open = len(all_open) if all_open else len(prs)

    cats = _categorize_prs(prs)
    lines: List[str] = []

    lines.append("# Breakability Merge Plan")
    lines.append("")
    if mode == "advisory":
        lines.append("> ⚠️ **Advisory mode** — All verdicts are recommendations. Merges are not blocked.")
        lines.append("")
    lines.append(f"**Repository:** {repo}")
    lines.append(f"**Analyzed:** {date.today().isoformat()}")
    lines.append(f"**PRs analyzed:** {len(prs)} of {total_open} open Dependabot PRs")
    if run_url:
        lines.append(f"**Analysis run:** [{run_url}]({run_url})")
    if not_analyzed:
        lines.append(f"> ℹ️ {len(not_analyzed)} PR(s) not analyzed in this run — listed below under \"Not Yet Analyzed\"")
    lines.append("")

    auto_clear = len(cats["safe"]) + len(cats["glance"])
    lines.append("## Developer Action Summary")
    lines.append("")
    lines.append(f"| Action | Count | What to do |")
    lines.append(f"|--------|-------|------------|")
    if cats["safe"]:
        lines.append(f"| ✅ Safe to merge | {len(cats['safe'])} | Merge immediately — no breaking changes detected |")
    if cats["glance"]:
        lines.append(f"| 👀 Likely safe | {len(cats['glance'])} | Quick glance, then merge — low-risk changes |")
    if cats["review"]:
        lines.append(f"| ⚠️ Needs review | {len(cats['review'])} | Review the analysis comment before merging |")
    if cats["blocked"]:
        lines.append(f"| ❌ Fix required | {len(cats['blocked'])} | Do not merge — breaking changes or build failures |")
    if cats["unverified"]:
        lines.append(f"| ⚙️ Unverified | {len(cats['unverified'])} | Analysis incomplete — re-run or investigate |")
    lines.append(f"| **Total** | **{len(prs)}** | **{auto_clear} auto-clearable** ({auto_clear * 100 // max(len(prs), 1)}% of analyzed) |")
    lines.append("")

    sec = data.get("security_posture", {})
    cve_alerts = sec.get("total_open_alerts", 0)
    cve_critical = sec.get("severity_counts", {}).get("critical", 0)
    cve_high = sec.get("severity_counts", {}).get("high", 0)
    if cve_critical > 0 or cve_high > 0:
        lines.append("## 🚨 CVE Alert")
        lines.append(f"> **{cve_critical} critical** and **{cve_high} high** severity Dependabot alerts are open on this repository.")
        lines.append("> PRs that address these CVEs should be prioritized.")
        lines.append("")

    if cross_deps:
        lines.append("## ⚠️ Coordinated Upgrades")
        lines.append("| PRs | Relationship | Merge Order |")
        lines.append("|-----|-------------|-------------|")
        for dep in cross_deps:
            a, b = dep.get("pr_a", "?"), dep.get("pr_b", "?")
            reason = dep.get("reason", "")
            order = dep.get("merge_order", "")
            lines.append(f"| #{a}, #{b} | {reason} | {order} |")
        lines.append("")

    table_hdr = ("| PR | Package | Version | Bump | Type | Verification |",
                 "|----|---------|---------|------|------|-------------|")

    for label, emoji, key in [
        ("Safe to Merge", "✅", "safe"),
        ("Likely Safe", "👀", "glance"),
        ("Review Needed", "⚠️", "review"),
        ("Unverified", "⚙️", "unverified"),
        ("Fix Required", "❌", "blocked"),
    ]:
        bucket = cats[key]
        if bucket:
            lines.append(f"## {emoji} {label}")
            lines.extend(table_hdr)
            for num, pr in bucket:
                lines.append(_pr_row(num, pr))
            lines.append("")

    if not_analyzed:
        lines.append("## ⚙️ Not Yet Analyzed")
        lines.append("> These PRs were not included in the current analysis run. They will be analyzed in the next full run.")
        lines.append("")
        lines.append("| PR | Title |")
        lines.append("|----|-------|")
        for num in sorted(not_analyzed, key=lambda x: int(x) if x.isdigit() else 0):
            title = not_analyzed[num].replace("|", "\\|")
            lines.append(f"| #{num} | {title} |")
        lines.append("")

    sec = data.get("security_posture", {})
    govuln = data.get("govulncheck", {})
    if sec or govuln:
        lines.append("## \U0001f512 Security Posture")
        if sec.get("total_open_alerts"):
            lines.append(f"- Open Dependabot alerts: {sec['total_open_alerts']}")
            sev = sec.get("severity_counts", {})
            if sev:
                lines.append(f"  - Critical: {sev.get('critical', 0)}, High: {sev.get('high', 0)}")
        if govuln:
            baseline = govuln.get("main_baseline", {})
            if baseline.get("findings"):
                lines.append(f"- Pre-existing govulncheck findings on main: {len(baseline['findings'])}")
            if govuln.get("prs_with_new_vulns", 0) > 0:
                lines.append(f"- \U0001f6a8 PRs introducing NEW vulnerabilities: {govuln['prs_with_new_vulns']}")
        lines.append("")

    lines.append("## Merge Risk Summary")
    lines.append(f"- **Total open PRs:** {total_open} | Analyzed: {len(prs)} | Not analyzed: {len(not_analyzed)}")
    lines.append(f"- **Safe:** {len(cats['safe'])} | **Likely safe:** {len(cats['glance'])} | "
                 f"**Review:** {len(cats['review'])} | "
                 f"**Blocked:** {len(cats['blocked'])} | **Unverified:** {len(cats['unverified'])}")
    lines.append(f"- **Auto-clearable:** {auto_clear} of {len(prs)} ({auto_clear * 100 // max(len(prs), 1)}%)")
    lines.append("")

    lines.append("---")
    lines.append(f"Mode: Deterministic + Behavioral Probe · Model: {model_name} · "
                 f"Generated: {date.today().isoformat()}")
    return "\n".join(lines)


def _build_merge_plan_prompt(base_prompt: str, data: Dict[str, Any],
                             run_url: Optional[str], model_name: str) -> str:
    prs = data.get("prs", {})
    cross_deps = data.get("cross_pr_deps", [])
    meta = data.get("metadata", {})
    security_posture = data.get("security_posture", {})
    govuln = data.get("govulncheck", {})

    cats = _categorize_prs(prs)
    summary = {k: len(v) for k, v in cats.items()}

    sections = [
        base_prompt,
        "\n\n---\n\n## MERGE PLAN GENERATION TASK\n",
        "You are generating the **Merge Plan** issue body (Section 5 of the prompt above).\n",
        f"\n### PR Summary\n```json\n{json.dumps(summary, indent=2)}\n```\n",
        f"\n### All PRs Data\n```json\n{json.dumps(prs, indent=2, default=str)}\n```\n",
    ]

    if cross_deps:
        sections.append(f"\n### Cross-PR Dependencies\n```json\n{json.dumps(cross_deps, indent=2)}\n```\n")

    if security_posture:
        sections.append(f"\n### Security Posture\n```json\n{json.dumps(security_posture, indent=2)}\n```\n")

    if govuln:
        sections.append(f"\n### govulncheck\n```json\n{json.dumps(govuln, indent=2)}\n```\n")

    if meta:
        sections.append(
            f"\n### Metadata\n- Repo: {meta.get('repo', 'unknown')}\n"
            f"- Mode: {meta.get('mode', 'advisory')}\n"
        )

    run_line = f"[Analysis run]({run_url})" if run_url else ""
    sections.append(
        f"\n### Footer\n"
        f"```\n---\n"
        f"Mode: Deterministic + Behavioral Probe · Model: {model_name} · "
        f"Generated: {date.today().isoformat()}\n"
        f"{run_line}\n```\n"
    )

    sections.append(
        "\n### OUTPUT INSTRUCTIONS\n"
        "Start your response with `# Breakability Merge Plan` — no preamble, "
        "no code fences, no conversational text before the heading. "
        "Generate the COMPLETE merge plan body in markdown. "
        "Required sections IN ORDER:\n"
        "1. Header (repo, date, PR count)\n"
        "2. Developer Action Summary table (action / count / what to do)\n"
        "3. CVE Alert (if critical/high alerts exist)\n"
        "4. Coordinated Upgrades (if cross-PR deps exist)\n"
        "5. Safe to Merge table\n"
        "6. Likely Safe table (GLANCE verdicts — quick review, low risk)\n"
        "7. Review Needed table\n"
        "8. Fix Required table\n"
        "9. Security Posture\n"
        "10. Merge Risk Summary (counts + auto-clearable percentage)\n"
        "11. Footer\n"
        "Order tables by merge priority. "
        "Output ONLY the markdown — do NOT wrap in ```markdown``` fences.\n"
    )

    sections.append(
        "\n### MANDATORY VERDICT COUNTS (use these EXACT numbers)\n"
        f"Safe: {summary.get('safe', 0)} | "
        f"Likely safe (GLANCE): {summary.get('glance', 0)} | "
        f"Review: {summary.get('review', 0)} | "
        f"Blocked: {summary.get('blocked', 0)} | "
        f"Unverified: {summary.get('unverified', 0)}\n"
        "Your Merge Risk Summary MUST use these exact counts. Do NOT recount from the PR data — "
        "these are the authoritative verdicts computed by the analysis pipeline.\n"
    )

    return "\n".join(sections)


import re as _re


def _fix_summary_counts(text: str, data: Dict[str, Any]) -> str:
    """Post-process AI output to ensure summary verdict counts match authoritative verdicts."""
    cats = _categorize_prs(data.get("prs", {}))
    correct = {
        "Safe": len(cats["safe"]),
        "Likely safe": len(cats["glance"]),
        "Review": len(cats["review"]),
        "Blocked": len(cats["blocked"]),
        "Unverified": len(cats["unverified"]),
    }
    correct_line = (
        f"- **Safe:** {correct['Safe']} | **Likely safe:** {correct['Likely safe']} | "
        f"**Review:** {correct['Review']} | "
        f"**Blocked:** {correct['Blocked']} | **Unverified:** {correct['Unverified']}"
    )
    pat = _re.compile(
        r'^- \*\*Safe:?\*\*:?\s*\d+\s*\|.*\*\*(?:Review|Blocked).*$',
        _re.MULTILINE,
    )
    if pat.search(text):
        text = pat.sub(correct_line, text)
    return text


def _is_meta_description(response: str, data: Dict[str, Any]) -> bool:
    """Detect AI meta-description responses that describe the plan instead of being the plan."""
    meta_patterns = [
        "The file is saved at", "The plan adheres to", "ready to be posted",
        "Key Sections:", "I have created", "I've created", "The plan includes",
    ]
    for pat in meta_patterns:
        if pat.lower() in response.lower():
            return True
    if not _re.search(r'\|.*#\d+', response):
        return True
    pr_nums = set(data.get("prs", {}).keys())
    if pr_nums:
        refs = set(_re.findall(r'#(\d+)', response))
        matched = pr_nums & refs
        if len(matched) < len(pr_nums) * 0.5:
            return True
    return False


_SECTION_VERDICT_MAP = {
    "safe": "SAFE", "likely safe": "GLANCE", "review": "REVIEW",
    "fix required": "BLOCKED", "blocked": "BLOCKED", "unverified": "UNVERIFIED",
}

_VERDICT_SECTION_NAME = {
    "SAFE": "Safe to Merge", "GLANCE": "Likely Safe", "REVIEW": "Review Needed",
    "BLOCKED": "Fix Required", "UNVERIFIED": "Unverified",
}


def _fix_section_membership(text: str, data: Dict[str, Any]) -> str:
    """Move misplaced PRs to their correct section based on authoritative verdicts."""
    cats = _categorize_prs(data.get("prs", {}))
    pr_to_verdict: Dict[str, str] = {}
    for verdict_key, pr_list in cats.items():
        mapped = {"safe": "SAFE", "glance": "GLANCE", "review": "REVIEW",
                  "blocked": "BLOCKED", "unverified": "UNVERIFIED"}.get(verdict_key)
        if mapped:
            for num, _ in pr_list:
                pr_to_verdict[num] = mapped

    if not pr_to_verdict:
        return text

    section_pat = _re.compile(r'^##\s+[^\n]*$', _re.MULTILINE)
    sections = []
    for m in section_pat.finditer(text):
        sections.append((m.start(), m.group(0)))

    if not sections:
        return text

    section_ranges = []
    for i, (start, heading) in enumerate(sections):
        end = sections[i + 1][0] if i + 1 < len(sections) else len(text)
        section_ranges.append((start, end, heading))

    def _classify_heading(heading: str) -> Optional[str]:
        h_lower = heading.lower()
        for key, verdict in _SECTION_VERDICT_MAP.items():
            if key in h_lower:
                return verdict
        return None

    row_pat = _re.compile(r'^\|[^\n]*#(\d+)[^\n]*\|[^\n]*$', _re.MULTILINE)
    misplaced = []

    for start, end, heading in section_ranges:
        section_verdict = _classify_heading(heading)
        if section_verdict is None:
            continue
        chunk = text[start:end]
        for rm in row_pat.finditer(chunk):
            pr_num = rm.group(1)
            correct = pr_to_verdict.get(pr_num)
            if correct and correct != section_verdict:
                row_text = rm.group(0)
                row_abs_start = start + rm.start()
                row_abs_end = start + rm.end()
                misplaced.append((pr_num, row_text, row_abs_start, row_abs_end,
                                  section_verdict, correct))

    if not misplaced:
        return text

    remove_ranges = [(s, e) for _, _, s, e, _, _ in misplaced]
    remove_ranges.sort(reverse=True)
    result = text
    for s, e in remove_ranges:
        line_start = result.rfind('\n', 0, s)
        line_start = line_start if line_start >= 0 else s
        line_end = result.find('\n', e)
        line_end = line_end if line_end >= 0 else len(result)
        result = result[:line_start] + result[line_end:]

    for _, row_text, _, _, _, correct_verdict in misplaced:
        target_name = _VERDICT_SECTION_NAME.get(correct_verdict, correct_verdict)
        target_pat = _re.compile(
            r'^(##\s+[^\n]*' + _re.escape(target_name) + r'[^\n]*\n(?:.*\n)*?)'
            r'(\n##|\n*$)', _re.MULTILINE)
        tm = target_pat.search(result)
        if tm:
            insert_pos = tm.end(1)
            result = result[:insert_pos] + row_text + "\n" + result[insert_pos:]
        else:
            heading_line = f"\n## {target_name}\n"
            table_hdr = "| PR | Package | Version | Bump | Type | Verification |\n|----|---------|---------|------|------|-------------|\n"
            result = result.rstrip() + "\n" + heading_line + table_hdr + row_text + "\n"

    return result


def _strip_preamble(text: str) -> str:
    """Remove conversational preamble before the first markdown heading and strip code fences."""
    text = _re.sub(r'^```(?:markdown)?\s*\n', '', text)
    text = _re.sub(r'\n```\s*$', '', text)
    m = _re.search(r'^#{1,3}\s', text, flags=_re.MULTILINE)
    if m and m.start() > 0:
        text = text[m.start():]
    return text


def generate_merge_plan(data: Dict[str, Any], prompt_path: Optional[str] = None,
                        model: str = "claude-4.5-sonnet",
                        run_url: Optional[str] = None) -> str:
    if prompt_path and os.path.exists(prompt_path):
        try:
            with open(prompt_path) as f:
                base_prompt = f.read()

            prompt = _build_merge_plan_prompt(base_prompt, data, run_url, model)
            backend = Backend.from_env(model=model)

            print("Generating AI merge plan...", file=sys.stderr)
            response = backend.invoke(
                prompt,
                namespace="breakability-merge-plan",
                key="merge-plan",
            )

            if response and "# " in response and len(response.splitlines()) > 20:
                response = _strip_preamble(response)
                if _is_meta_description(response, data):
                    print("AI merge plan is meta-description, using template fallback", file=sys.stderr)
                else:
                    response = _fix_section_membership(response, data)
                    response = _fix_summary_counts(response, data)
                    print(f"AI merge plan generated ({len(response.splitlines())} lines)", file=sys.stderr)
                    return response.strip()
            print("AI merge plan insufficient, using template fallback", file=sys.stderr)
        except Exception as e:
            print(f"AI merge plan failed ({e}), using template fallback", file=sys.stderr)

    return generate_template_plan(data, run_url=run_url, model_name=model)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build_results", help="Path to build-results.json")
    ap.add_argument("--prompt", default=None, help="Path to breakability-prompt.md")
    ap.add_argument("--model", default="claude-4.5-sonnet", help="AI model name")
    ap.add_argument("--run-url", default=None, help="Analysis run URL")
    ap.add_argument("--output", default=None, help="Output file (default: stdout)")
    args = ap.parse_args()

    with open(args.build_results) as f:
        data = json.load(f)

    run_url = args.run_url or os.environ.get("ANALYSIS_RUN_URL")
    body = generate_merge_plan(data, prompt_path=args.prompt, model=args.model, run_url=run_url)

    if args.output:
        with open(args.output, "w") as f:
            f.write(body)
        print(f"Merge plan written to {args.output}", file=sys.stderr)
    else:
        print(body)

    return 0


if __name__ == "__main__":
    sys.exit(main())
