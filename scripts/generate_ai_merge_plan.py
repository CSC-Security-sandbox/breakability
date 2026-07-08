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
    safe, review, blocked, unverified, skipped = [], [], [], [], []
    for num, pr in sorted(prs.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        if pr.get("build", {}).get("verdict") == "skipped":
            skipped.append((num, pr))
            continue
        verdict = _get_verdict(pr)
        if verdict == "SAFE":
            safe.append((num, pr))
        elif verdict in ("BLOCKED", "BUILD_FAILS"):
            blocked.append((num, pr))
        elif verdict == "UNVERIFIED":
            unverified.append((num, pr))
        else:
            review.append((num, pr))
    return {
        "safe": safe, "review": review, "blocked": blocked,
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
        ("Review Needed", "⚠️", "review"),
        ("Unverified", "⚙️", "unverified"),
        ("Blocked / Fix Required", "❌", "blocked"),
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

    lines.append("## Summary")
    lines.append(f"- **Total open PRs:** {total_open} | Analyzed: {len(prs)} | Not analyzed: {len(not_analyzed)}")
    lines.append(f"- **Safe:** {len(cats['safe'])} | **Review:** {len(cats['review'])} | "
                 f"**Blocked:** {len(cats['blocked'])} | **Unverified:** {len(cats['unverified'])}")
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
        "Include: repo/date header, coordinated upgrades, "
        "Safe/Review/Blocked/Unverified tables, security posture, "
        "summary stats, and footer. "
        "Order tables by merge priority. "
        "Output ONLY the markdown — do NOT wrap in ```markdown``` fences.\n"
    )

    return "\n".join(sections)


import re as _re


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
