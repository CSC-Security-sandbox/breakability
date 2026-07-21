#!/usr/bin/env python3
"""Generate rich AI-powered PR comments using breakability-prompt.md.

Reads the full breakability-prompt.md (domain knowledge, verdict rules, visual
templates) plus build-results.json and calls the AI backend per PR to generate
200-300 line rich comments with all 13 golden features.

Falls back to breakability_analyst.py template rendering if AI call fails.

Usage:
  generate_ai_comments.py <build-results.json> \
    --prompt prompts/breakability-prompt.md \
    [--model claude-4.5-sonnet] \
    [--run-url URL] \
    [--merge-plan-issue NUMBER]
"""

__all__ = [
    "_read_prompt", "_extract_pr_data", "_build_per_pr_prompt",
    "_reject_false_cve_claim", "_reject_cve_direction_error", "_reject_fabricated_probe",
    "_strip_agent_narration", "_strip_govulncheck",
    "_enforce_verdict_floor", "_downgrade_mismatched_probe", "_enforce_merge_risk_tag", "_normalize_verdict_text", "_inject_verdict_logic",
    "_ensure_marker", "_signal_table_ok", "_validate_comment", "_near_valid",
    "_fallback_comment", "generate_comments", "main",
]

import argparse
import json
import os
import re
import sys
from datetime import date
from typing import Dict, Any, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))  # parent scripts/ dir

from ai_backend import Backend
from verdict_contract import authoritative_verdict, _is_currently_vulnerable


def _read_prompt(prompt_path: str) -> str:
    with open(prompt_path) as f:
        return f.read()


def _extract_pr_data(pr: Dict[str, Any]) -> str:
    """Serialize a single PR's data as JSON for the AI prompt context."""
    return json.dumps(pr, indent=2, default=str)


def _detect_misattribution_groups(
    pr_items: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Detect PRs sharing byte-identical build output across different packages.

    Returns {pr_num: {"pr_ids": [...], "packages": [...]}} for PRs in groups of 3+.
    """
    fail_groups: Dict[str, list] = {}
    for num, pr in pr_items.items():
        build = pr.get("build") or {}
        if build.get("verdict") != "fail":
            continue
        tail = build.get("output_tail")
        if not tail or not tail.strip():
            continue
        pkg = pr.get("package", "unknown")
        fail_groups.setdefault(tail, []).append((num, pkg))

    result: Dict[str, Dict[str, Any]] = {}
    for group in fail_groups.values():
        if len(group) < 3:
            continue
        packages = set(pkg for _, pkg in group)
        if len(packages) < 2:
            continue
        pr_ids = sorted([pid for pid, _ in group], key=lambda x: int(x) if x.isdigit() else 0)
        group_info = {"pr_ids": pr_ids, "packages": sorted(packages)}
        for pid, _ in group:
            result[pid] = group_info
    return result


def _build_per_pr_prompt(
    base_prompt: str,
    pr: Dict[str, Any],
    pr_num: str,
    metadata: Dict[str, Any],
    run_url: Optional[str],
    merge_plan_issue: Optional[str],
    model_name: str,
    cross_deps: list,
    top_level: Dict[str, Any],
    misattribution_group: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the full prompt for one PR: base instructions + PR-specific data."""
    pr_json = _extract_pr_data(pr)

    relevant_cross_deps = [
        d for d in cross_deps
        if str(d.get("pr_a")) == pr_num or str(d.get("pr_b")) == pr_num
    ]

    plan_ref = f"#{merge_plan_issue}" if merge_plan_issue else ""

    sections = [
        base_prompt,
        "\n\n---\n\n## CONTEXT FOR THIS PR\n",
        f"You are generating a comment for **PR #{pr_num}**.\n",
        (f"Use `{plan_ref}` for the merge plan link.\n" if plan_ref else "Omit the merge plan link line.\n"),
        f"\n### PR Data (from build-results.json)\n```json\n{pr_json}\n```\n",
    ]

    if pr.get("go_resolution"):
        sections.append(
            "\n### ⚠️ DATA SOURCE WARNING: go_resolution\n"
            "The `go_resolution` field (including `modsum_diff`) shows the result of "
            "running `go work sync` across the ENTIRE workspace. It may include packages "
            "and changes from OTHER modules that are NOT touched by this PR's actual diff. "
            "Do NOT cite `go_resolution` or `modsum_diff` content as 'added in this PR' or "
            "'introduced by this PR'. Only the PR's actual diff (files listed in `changed_files` "
            "or the PR diff) reflects what this specific PR changes.\n"
        )

    if misattribution_group:
        other_prs = ", ".join(f"#{p}" for p in misattribution_group["pr_ids"] if p != pr_num)
        sections.append(
            f"\n### ⚠️ SHARED BUILD FAILURE — LIKELY PRE-EXISTING\n"
            f"This PR's build output is **byte-identical** to {len(misattribution_group['pr_ids']) - 1} "
            f"other PRs from different packages ({other_prs}). "
            f"This strongly indicates the failure is a pre-existing/environmental issue in the "
            f"build sandbox, NOT caused by this specific dependency bump.\n\n"
            f"You MUST:\n"
            f"- State that the build failure is shared across {len(misattribution_group['pr_ids'])} "
            f"unrelated PRs and is likely pre-existing\n"
            f"- Do NOT assert that 'this PR introduces' or 'this PR causes' the failure\n"
            f"- Use hedged language: 'appears to be a pre-existing issue' or "
            f"'shared by N other PRs from different packages'\n"
        )

    if relevant_cross_deps:
        sections.append(
            f"\n### Cross-PR Dependencies\n```json\n"
            f"{json.dumps(relevant_cross_deps, indent=2)}\n```\n"
        )

    workspace_graph = top_level.get("workspace_graph")
    if workspace_graph:
        sections.append(
            f"\n### Workspace Graph (monorepo structure)\n```json\n"
            f"{json.dumps(workspace_graph, indent=2, default=str)}\n```\n"
        )

    nestjs_skew = top_level.get("nestjs_skew")
    if nestjs_skew:
        sections.append(
            f"\n### NestJS Version Skew\n```json\n"
            f"{json.dumps(nestjs_skew, indent=2)}\n```\n"
        )

    cve_details = pr.get("cve_details") or []
    det_sec = (pr.get("deterministic") or {}).get("security") or {}
    if cve_details:
        sections.append(
            f"\n### CVE/Vulnerability Data for This PR\n"
            f"**CRITICAL FRAMING RULE:** These CVEs are vulnerabilities present in the OLD "
            f"(FROM) version that are FIXED/REMEDIATED by upgrading to the new (TO) version. "
            f"This PR REMEDIATES these CVEs — it does NOT introduce them.\n\n"
            f"You MUST:\n"
            f"- Say 'remediates', 'fixes', or 'addresses' — NEVER 'introduces' or 'adds'\n"
            f"- Include a '### Security Impact' section listing each CVE with severity, "
            f"summary, and advisory link\n"
            f"- NEVER write 'No CVEs associated with this upgrade' — this PR has {len(cve_details)} CVE(s)\n\n"
            f"```json\n{json.dumps(cve_details, indent=2)}\n```\n"
        )
    elif det_sec.get("isSecurity") and det_sec.get("cveIds"):
        cve_ids = det_sec["cveIds"]
        cvss = det_sec.get("cvssScore") or det_sec.get("cvss_score") or "N/A"
        sections.append(
            f"\n### CVE/Vulnerability Data for This PR (from deterministic.security)\n"
            f"**CRITICAL FRAMING RULE:** These CVEs are vulnerabilities present in the OLD "
            f"(FROM) version that are FIXED/REMEDIATED by upgrading to the new (TO) version. "
            f"This PR REMEDIATES these CVEs — it does NOT introduce them.\n\n"
            f"- CVE IDs: {', '.join(cve_ids[:10])}{' (and more)' if len(cve_ids) > 10 else ''}\n"
            f"- Max CVSS: {cvss}\n"
            f"- Vulnerable range: {det_sec.get('vulnerableVersionRange', 'N/A')}\n\n"
            f"You MUST include a '### Security Impact' section mentioning the CVE count ({len(cve_ids)}) "
            f"and CVSS score.\n"
        )

    security_posture = top_level.get("security_posture")
    if security_posture:
        sections.append(
            f"\n### Security Posture\n```json\n"
            f"{json.dumps(security_posture, indent=2)}\n```\n"
        )

    if metadata:
        sections.append(
            f"\n### Metadata\n- Repo: {metadata.get('repo', 'unknown')}\n"
            f"- Mode: {metadata.get('mode', 'advisory')}\n"
            f"- Timestamp: {metadata.get('timestamp', 'unknown')}\n"
        )

    footer_parts = []
    if run_url:
        footer_parts.append(f"Analysis run: {run_url}")
        sections.append(f"\n### Run Link\nInclude this link in the footer: [{run_url}]({run_url})\n")

    sections.append(
        f"\n### Footer Requirements\n"
        f"End the comment with:\n"
        f"```\n"
        f"---\n"
        f"Mode: Deterministic + Behavioral Probe · Model: {model_name} · "
        f"Analyzed: {date.today().isoformat()}\n"
    )
    if run_url:
        sections.append(f"[Analysis run]({run_url})\n")
    sections.append("```\n")

    bg = pr.get("behavioral_grade") or {}
    if bg:
        probe_ran = bg.get("same_behavior") is not None
        sections.append(
            f"\n### Behavioral Probe Data (GROUND TRUTH — do not fabricate)\n"
            f"```json\n{json.dumps(bg, indent=2)}\n```\n"
        )
        if not probe_ran:
            sections.append(
                "**The behavioral probe did NOT run successfully.** "
                f"Reason: {bg.get('rationale', 'unavailable')}. "
                "Do NOT invent SHA256 hashes, doc line counts, or any other probe output. "
                "State that the probe was unavailable and report confidence as LOW.\n"
            )
        else:
            sections.append(
                "Use ONLY the data above for the behavioral probe section. "
                "Do not invent hashes or metrics not present in this data.\n"
            )

    sections.append(
        "\n### OUTPUT INSTRUCTIONS\n"
        "Generate the COMPLETE PR comment in markdown. Start with `<!-- breakability-check -->` "
        "on the first line. Follow the visual format templates from Section 4/5 of the prompt.\n\n"
        "MANDATORY REQUIREMENTS:\n"
        "- The comment MUST be at least 200 lines long. Target 200-300 lines. "
        "Comments under 200 lines are flagged for review. Expand narrative sections, "
        "include more stdout/stderr, and add detailed reasoning to reach 200+ lines.\n"
        "- Include ALL sections: headline, signal summary table (7 rows, 4-column: | Layer | Result | Confidence | Evidence |), per-layer narrative "
        "(Build Analysis, Test Analysis, etc. with 'What we checked' bullets and actual "
        "stdout/stderr in code blocks), behavioral probe with SHA256 hashes, reachability "
        "with file:line references, policy decision pseudocode, final recommendation with "
        "numbered steps, and independent verification resources.\n"
        "- MUST include at least one ```bash code block with reproducible verification commands.\n"
        "- MUST include numbered action steps (1. 2. 3.) in the recommendation section.\n"
        "- Each per-layer section needs a confidence rating (HIGH/MEDIUM/LOW) with reasoning.\n"
        "- The `severity` and `priority` fields in verdict_v2 reflect **build/test verification "
        "status** (e.g. build-fail → high/P0), NOT CVE severity or security risk. Do not "
        "present them as security ratings. If mentioning severity, clarify it is build-derived.\n"
        "- MAXIMUM 350 lines. If your comment exceeds 350 lines, trim the least essential "
        "sections (verbose output blocks, redundant changelog excerpts) while keeping all "
        "13 required features.\n"
        "- Output ONLY the markdown comment — no preamble, no explanation.\n"
    )

    return "\n".join(sections)


_VERDICT_MAP = {"BUILD_FAILS": "BLOCKED", "GLANCE": "SAFE"}

_AGENT_NARRATION_RE = re.compile(
    r"^(Now let me|I'll create|Let me (draft|write|create|analyze|generate)|"
    r"Here'?s the (complete|final|full)|I will now|Let me now)\b.*$",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_agent_narration(comment: str) -> str:
    """Remove LLM scratch-pad / self-narration lines that leak into comments."""
    return _AGENT_NARRATION_RE.sub("", comment).lstrip("\n")


def _strip_govulncheck(comment: str) -> str:
    """Remove any recommendation to install or run govulncheck (permanently banned)."""
    comment = re.sub(
        r"^.*govulncheck.*$", "", comment, flags=re.MULTILINE | re.IGNORECASE
    )
    comment = re.sub(r"\n{3,}", "\n\n", comment)
    return comment


def _enforce_verdict_floor(comment: str, pr: Dict[str, Any], pr_num: str) -> str:
    """Post-processing guard: ensure AI verdict matches authoritative_verdict() exactly.

    Rewrites the entire H2 header line so the verdict word, emoji, and any trailing
    text ("REVIEW RISK") are all corrected in one pass.
    """
    av = authoritative_verdict(pr)
    contract_verdict = av.get("verdict", "REVIEW")
    emoji_map = {"SAFE": "✅", "REVIEW": "⚠️", "BLOCKED": "🚫"}
    header_re = re.compile(
        r'^##\s+\S+\s+'
        r'(SAFE|GLANCE|REVIEW|BLOCKED|BUILD_FAILS)'
        r'(?:\s+\w+)*'
        r'(\s+—\s+.*)',
        re.MULTILINE,
    )
    m = header_re.search(comment)
    if not m:
        return comment
    ai_verdict = m.group(1)
    ai_normalized = _VERDICT_MAP.get(ai_verdict, ai_verdict)
    if ai_normalized == contract_verdict:
        return comment
    print(
        f"PR#{pr_num}: verdict match enforcement — AI said {ai_verdict}, "
        f"contract says {contract_verdict} (source={av.get('source', '?')}). Overriding.",
        file=sys.stderr,
    )
    new_emoji = emoji_map.get(contract_verdict, "⚠️")
    new_header = f"## {new_emoji} {contract_verdict}{m.group(2)}"
    return comment[:m.start()] + new_header + comment[m.end():]


def _normalize_verdict_text(comment: str, pr_num: str) -> str:
    """Map non-standard verdict strings in the H2 header to valid buckets.

    UNVERIFIED → REVIEW, BUILD_FAILS → BLOCKED, any other unknown → REVIEW.
    """
    VALID = {"SAFE", "REVIEW", "BLOCKED", "GLANCE"}
    KNOWN_MAP = {"UNVERIFIED": "REVIEW", "BUILD_FAILS": "BLOCKED", "INCONCLUSIVE": "REVIEW"}
    EMOJI = {"SAFE": "✅", "REVIEW": "⚠️", "BLOCKED": "🚫"}

    m = re.search(
        r'^(##\s+[^\n]*?\b)(SAFE|GLANCE|REVIEW|BLOCKED|BUILD_FAILS|UNVERIFIED|INCONCLUSIVE|[A-Z][A-Z_]{3,})\b',
        comment, re.MULTILINE,
    )
    if not m:
        return comment
    found = m.group(2)
    if found in VALID:
        return comment
    mapped = KNOWN_MAP.get(found, "REVIEW")
    print(
        f"PR#{pr_num}: non-standard verdict '{found}' mapped to '{mapped}'",
        file=sys.stderr,
    )
    comment = comment.replace(m.group(0), m.group(1) + mapped)
    new_emoji = EMOJI.get(mapped, "⚠️")
    for old in ("❓", "❔", "❌", "🔍", "🔎"):
        if old in comment:
            comment = comment.replace(old, new_emoji, 1)
            break
    return comment


def _inject_verdict_logic(comment: str, pr: Dict[str, Any], pr_num: str) -> str:
    """Inject deterministic verdict logic pseudocode if the AI omitted it."""
    if re.search(r'^###\s+Verdict\s+Logic', comment, re.MULTILINE):
        return comment
    if re.search(r'(?i)verdict\s+logic', comment):
        return comment
    if re.search(r'\bIF\b.*\bTHEN\b.*verdict\s*[:=]', comment, re.DOTALL | re.IGNORECASE):
        return comment

    av = authoritative_verdict(pr)
    verdict = av.get("verdict", "REVIEW")
    build = pr.get("build") or {}
    test = pr.get("test") or {}
    bg = pr.get("behavioral_grade") or {}
    dep_type = pr.get("dep_type", "unknown")

    conditions = []
    if build.get("verdict"):
        conditions.append(f'build.verdict = "{build["verdict"]}"')
    if build.get("pr_exit") is not None:
        conditions.append(f'build.pr_exit = {build["pr_exit"]}')
    if test.get("ran") is not None:
        if test["ran"] and test.get("exit") is not None:
            conditions.append(f'test.exit = {test["exit"]}')
        elif not test["ran"]:
            conditions.append('test.ran = false')
    if bg.get("same_behavior") is not None:
        conditions.append(f'behavioral_grade.same_behavior = {str(bg["same_behavior"]).lower()}')
    conditions.append(f'dep_type = "{dep_type}"')

    cond_str = "\n  AND ".join(conditions)
    source = av.get("source", "verdict_contract")
    pseudocode = (
        f"\n\n### Verdict Logic\n\n```\nIF {cond_str}\n"
        f"THEN verdict = {verdict}\nSource: {source}\n```\n"
    )

    rec_m = re.search(r'^###?\s+.*(?:Recommend|Next\s+Step|Action|Step|Verification|How\s+to|Developer|What\s+to)', comment, re.MULTILINE | re.IGNORECASE)
    if rec_m:
        comment = comment[:rec_m.start()] + pseudocode + "\n" + comment[rec_m.start():]
    else:
        comment = comment.rstrip() + pseudocode

    print(f"PR#{pr_num}: injected deterministic verdict logic pseudocode", file=sys.stderr)
    return comment


def _reject_false_cve_claim(comment: str, pr: Dict[str, Any], pr_num: str) -> str:
    """Post-gen guard: if the PR has CVEs, the comment must not claim there are none."""
    cve_details = pr.get("cve_details") or []
    if not cve_details:
        return comment
    false_claims = [
        r'[Nn]o\s+CVE',
        r'[Nn]o\s+known\s+(?:CVE|vulnerabilit)',
        r'[Nn]o\s+(?:CVE|vulnerabilit).*associated',
        r'0\s+CVE',
    ]
    for pattern in false_claims:
        if re.search(pattern, comment):
            print(
                f"PR#{pr_num}: REJECTED — comment claims no CVEs but PR has "
                f"{len(cve_details)} CVE(s). Replacing with fallback.",
                file=sys.stderr,
            )
            return ""
    return comment


def _reject_cve_direction_error(comment: str, pr: Dict[str, Any], pr_num: str) -> str:
    """Post-gen guard: upgrade PRs must say CVEs are fixed, not introduced."""
    cve_details = pr.get("cve_details") or []
    if not cve_details:
        return comment
    banner_match = re.search(r'^##\s+[^\n]*', comment, re.MULTILINE)
    if not banner_match:
        return comment
    start = banner_match.start()
    lines = comment[start:].split('\n')
    scan_region = '\n'.join(lines[:6])
    if re.search(r'\bintroduce', scan_region, re.IGNORECASE) and re.search(r'\bCVE|vulnerabilit', scan_region, re.IGNORECASE):
        print(
            f"PR#{pr_num}: REJECTED — comment says 'introduces' CVEs for an upgrade PR "
            f"(found in H2 + blockquote region). Replacing with fallback.",
            file=sys.stderr,
        )
        return ""
    return comment


def _reject_fabricated_probe(comment: str, pr: Dict[str, Any], pr_num: str) -> str:
    """Post-gen guard: reject SHA256 claims when the behavioral probe never ran."""
    bg = pr.get("behavioral_grade") or {}
    probe_ran = bg.get("same_behavior") is not None
    if probe_ran:
        return comment
    eco = str(pr.get("ecosystem", "")).strip().lower()
    if eco == "actions":
        return comment
    if re.search(r'SHA256[:\s]+[0-9a-f]{8,}', comment):
        print(
            f"PR#{pr_num}: REJECTED — comment contains fabricated SHA256 hash "
            f"(probe never ran: {bg.get('rationale', 'unavailable')}). Stripping probe section.",
            file=sys.stderr,
        )
        comment = re.sub(
            r'(?:^|\n)###?\s*(?:Behavioral|Runtime)\s*Probe.*?(?=\n###?\s|\n---|\Z)',
            '\n### Behavioral Probe\n\n⬜ **UNAVAILABLE** — probe did not run. '
            f'Reason: {bg.get("rationale", "unavailable")}. Confidence: LOW.\n',
            comment, count=1, flags=re.DOTALL | re.IGNORECASE,
        )
    return comment


def _downgrade_mismatched_probe(comment: str, pr: Dict[str, Any], pr_num: str) -> str:
    """Downgrade displayed probe confidence when reconciliation_note flags a mismatch."""
    bg = pr.get("behavioral_grade") or {}
    note = bg.get("reconciliation_note") or ""
    if "MISMATCH" not in note.upper():
        return comment
    probe_re = re.compile(
        r'(Behavioral\s+Probe\s*\|[^|]*\|)\s*(High|Medium)\s+confidence',
        re.IGNORECASE,
    )
    m = probe_re.search(comment)
    if m:
        replacement = (m.group(1) +
                       " Low confidence — ⚠️ package mismatch detected, probe may have analyzed wrong package")
        comment = comment[:m.start()] + replacement + comment[m.end():]
        print(f"PR#{pr_num}: downgraded probe confidence due to PACKAGE-MISMATCH reconciliation note",
              file=sys.stderr)
    return comment


def _enforce_merge_risk_tag(comment: str, pr: Dict[str, Any], pr_num: str) -> str:
    """Post-gen guard: ensure comment body doesn't contradict merge_risk.tag."""
    mr = pr.get("merge_risk") or (pr.get("deterministic") or {}).get("merge_risk") or {}
    tag = mr.get("tag", "")
    if not tag:
        return comment
    tag_upper = tag.upper()
    if tag_upper == "HIGH":
        changed = False
        if re.search(r'(?i)merge\s*risk[:\s]+low', comment):
            comment = re.sub(r'(?i)(merge\s*risk[:\s]+)low', r'\g<1>High', comment)
            changed = True
        contract_verdict = (authoritative_verdict(pr).get("verdict") or "")
        if contract_verdict in ("REVIEW", "BLOCKED"):
            if re.search(r'(?i)AI\s+Arbiter[:\s]+SAFE', comment):
                comment = re.sub(r'(?i)(AI\s+Arbiter[:\s]+)SAFE', r'\g<1>' + contract_verdict, comment)
                changed = True
        if changed:
            print(f"PR#{pr_num}: merge_risk tag enforcement — corrected Low→High in comment body", file=sys.stderr)
    return comment


def _ensure_marker(comment: str) -> str:
    """Ensure the comment starts with the breakability marker."""
    marker = "<!-- breakability-check -->"
    stripped = comment.strip()
    if not stripped.startswith(marker):
        return f"{marker}\n{stripped}"
    return stripped


def _signal_table_ok(comment: str) -> str:
    """Check signal table presence and column count. Returns status string or ''."""
    for marker in ("| Layer ", "| Check ", "| Signal "):
        if marker in comment:
            header_line = next((l for l in comment.splitlines() if marker in l), "")
            pipes = header_line.count("|")
            if pipes >= 4:
                return "4-col"
            return f"present-{pipes - 1}col"
    return ""


def _validate_comment(comment: str, pr_num: str, pr_data: Dict[str, Any] = None) -> tuple:
    """Validate that the AI output meets golden standard quality bars.

    Returns (passed: bool, diagnostics: dict) where diagnostics maps each
    criterion to {passed: bool, value: any}.

    Checks all 13 golden features plus verdict consistency against
    authoritative_verdict() when pr_data is provided.
    """
    line_count = len(comment.strip().splitlines())
    comment_lower = comment.lower()
    h3_count = len(re.findall(r'^###\s', comment, re.MULTILINE))

    diagnostics = {
        "line_count": {"passed": line_count >= 150, "value": line_count},
        "has_h2": {"passed": "##" in comment, "value": "##" in comment},
        "has_signal_table": {
            "passed": bool(_signal_table_ok(comment)),
            "value": _signal_table_ok(comment),
        },
        "has_h3": {"passed": "###" in comment, "value": "###" in comment},
        "has_mode_footer": {"passed": "Mode:" in comment, "value": "Mode:" in comment},
        "has_numbered_list": {
            "passed": bool(re.search(r'\d+[\.\)]\s', comment)),
            "value": bool(re.search(r'\d+[\.\)]\s', comment)),
        },
        "has_bash_block": {
            "passed": "```bash" in comment or "```shell" in comment,
            "value": "```bash" in comment or "```shell" in comment,
        },
        "has_reachability": {
            "passed": "reachab" in comment_lower or "import" in comment_lower,
            "value": "reachab" in comment_lower or "import" in comment_lower,
        },
        "has_sha256": {
            "passed": "sha256" in comment_lower or "hash" in comment_lower,
            "value": "sha256" in comment_lower or "hash" in comment_lower,
        },
        "has_policy_pseudocode": {
            "passed": bool(
                re.search(r'^###\s+Verdict\s+Logic', comment, re.MULTILINE)
                or re.search(r'(?i)verdict\s+logic', comment)
                or re.search(r'\bIF\b.*\bTHEN\b.*verdict\s*[:=]', comment, re.DOTALL | re.IGNORECASE)
            ),
            "value": bool(
                re.search(r'^###\s+Verdict\s+Logic', comment, re.MULTILINE)
                or re.search(r'(?i)verdict\s+logic', comment)
                or re.search(r'\bIF\b.*\bTHEN\b.*verdict\s*[:=]', comment, re.DOTALL | re.IGNORECASE)
            ),
        },
        "has_confidence_reasoning": {
            "passed": bool(re.search(r'\b(HIGH|MEDIUM|LOW)\b', comment)),
            "value": bool(re.search(r'\b(HIGH|MEDIUM|LOW)\b', comment)),
        },
        "has_h3_narrative_sections": {
            "passed": h3_count >= 3,
            "value": h3_count,
        },
        "has_merge_plan_link": {
            "passed": bool(re.search(r'#\d+', comment)),
            "value": bool(re.search(r'#\d+', comment)),
        },
    }

    # SHA256 is N/A for actions PRs and PRs where behavioral probe didn't run
    if pr_data is not None:
        eco = str(pr_data.get("ecosystem", "")).strip().lower()
        bg = pr_data.get("behavioral_grade") or {}
        probe_ran = isinstance(bg, dict) and bg.get("same_behavior") is not None
        if eco == "actions" or not probe_ran:
            diagnostics["has_sha256"] = {"passed": True, "value": "N/A (no probe)"}

    if pr_data is not None:
        severity_order = {"SAFE": 0, "REVIEW": 1, "BLOCKED": 2, "BUILD_FAILS": 3}
        av = authoritative_verdict(pr_data)
        contract_verdict = av.get("verdict", "REVIEW")
        m = re.search(r'^##\s+[^\n]*?\b(SAFE|REVIEW|BLOCKED|BUILD_FAILS)\b', comment, re.MULTILINE)
        ai_verdict = m.group(1) if m else None
        if ai_verdict and severity_order.get(ai_verdict, 1) < severity_order.get(contract_verdict, 1):
            diagnostics["verdict_mismatch"] = {
                "passed": True,
                "value": f"AI={ai_verdict} contract={contract_verdict} (source={av.get('source', '?')}) [warning — contract overrides]",
            }

    if pr_data is not None:
        cve_details = pr_data.get("cve_details") or []
        det_sec = (pr_data.get("deterministic") or {}).get("security") or {}
        has_cve_data = bool(cve_details) or (det_sec.get("isSecurity") and det_sec.get("cveIds"))
        if has_cve_data:
            has_cve = bool(re.search(r'CVE-\d{4}|GHSA-|[Ss]ecurity\s+[Ii]mpact|[Vv]ulnerabilit', comment))
            diagnostics["has_cve_section"] = {"passed": has_cve, "value": has_cve}

    all_passed = all(d["passed"] for d in diagnostics.values())

    if not all_passed:
        parts = []
        for name, d in diagnostics.items():
            val = d["value"]
            status = "FAIL" if not d["passed"] else "ok"
            parts.append(f"{name}={val}({status})")
        print(f"PR#{pr_num} validation: {', '.join(parts)}", file=sys.stderr)
    elif line_count < 200:
        print(f"PR#{pr_num}: AI comment is {line_count} lines (below 200-line golden target)", file=sys.stderr)

    return (all_passed, diagnostics)


def _near_valid(diagnostics: dict) -> bool:
    """Accept a comment without retry when it is long enough and nearly passes."""
    lc = diagnostics.get("line_count", {})
    line_count = lc.get("value") or 0
    if line_count < 100:
        return False
    h3_check = diagnostics.get("has_h3_narrative_sections", {})
    if not h3_check.get("passed", True):
        return False
    failures = sum(1 for d in diagnostics.values() if not d.get("passed"))
    if line_count >= 200 and failures <= 2:
        return True
    if line_count >= 180 and failures == 0:
        return True
    if line_count >= 150 and failures <= 1:
        return True
    return False


def _fallback_comment(pr: Dict[str, Any], pr_num: str, run_url: Optional[str],
                      merge_plan_issue: Optional[str], model_name: str,
                      cross_pr_deps: list = None, metadata: Dict[str, Any] = None) -> str:
    """Generate an enriched fallback comment with available signal data."""
    pkg = pr.get("package", "unknown")
    from_ver = pr.get("from", "?")
    to_ver = pr.get("to", "?")
    dep_type = pr.get("dep_type", "unknown")
    bump = pr.get("bump", "unknown")
    plan_ref = f"#{merge_plan_issue}" if merge_plan_issue else ""

    av = authoritative_verdict(pr)
    verdict = av.get("verdict", "REVIEW")
    emoji_map = {"SAFE": "✅", "BLOCKED": "🚫", "REVIEW": "⚠️"}
    emoji = emoji_map.get(verdict, "⚠️")

    build = pr.get("build") or {}
    test = pr.get("test") or {}
    det = pr.get("deterministic") or {}
    bg = pr.get("behavioral_grade") or {}
    files_importing = pr.get("files_importing") or []

    build_verdict = build.get("verdict", "unknown")
    b_emoji = "✅" if build_verdict == "pass" else ("🚫" if build_verdict == "fail" else "❓")

    test_ran = test.get("ran", False)
    test_exit = test.get("exit")
    new_failures = test.get("new_failures") or []
    if test_ran and test_exit == 0:
        t_status = "✅ Passed"
    elif test_ran and test_exit is not None and not new_failures:
        t_status = f"⚠️ Failed — pre-existing (exit {test_exit})"
    elif test_ran and test_exit is not None:
        t_status = f"❌ Failed (exit {test_exit})"
    else:
        t_status = "⏭️ Not executed"

    probe_same = bg.get("same_behavior")
    if probe_same is True:
        p_status = "✅ Same behavior"
    elif probe_same is False:
        p_status = "⚠️ Different behavior"
    else:
        p_status = "⏭️ Not available"

    reach_count = len(files_importing)
    r_status = f"📦 {reach_count} file(s)" if reach_count > 0 else "✅ Not imported"

    changelog_signal = det.get("changelogSignal")
    if isinstance(changelog_signal, dict):
        cl_status = (changelog_signal.get("status") or "").lower()
    elif isinstance(changelog_signal, str):
        cl_status = changelog_signal.lower()
    else:
        cl_status = ""
    _CL_STATUS_MAP = {
        "breaking": "⚠️ Breaking changes detected",
        "missing": "⏭️ Unavailable",
        "clean": "✅ No breaking changes",
        "none": "✅ No breaking changes (low confidence)",
    }
    cl_short = _CL_STATUS_MAP.get(cl_status, "⏭️ Unknown" if changelog_signal is None else "⏭️ Unknown")

    api_diff_tool = det.get("api_diff_tool")
    api_changes = det.get("api_changes")
    if api_diff_tool is None:
        a_status = "⏭️ Unavailable"
    elif isinstance(api_diff_tool, dict) and api_diff_tool.get("status") == "unavailable":
        a_status = "⏭️ Unavailable"
    elif api_changes is None and not (isinstance(api_diff_tool, dict) and api_diff_tool.get("status")):
        a_status = "⏭️ Unavailable"
    elif api_changes:
        a_status = f"⚠️ {api_changes} changes"
    else:
        a_status = "✅ No changes"

    untested_qualifier = " (no test evidence)" if av.get("untested") else ""
    lines = [
        "<!-- breakability-check -->",
        "<!-- ai-fallback -->",
        f"## {emoji} {verdict}{untested_qualifier} — `{pkg}` {from_ver} → {to_ver} • {dep_type} • {bump}",
        "",
        "> **Note:** AI comment generation failed. This is an automated fallback with available signal data.",
        "",
    ]
    if verdict == "SAFE" and build_verdict == "pre_existing":
        lines.extend([
            "> **ℹ️ Build/test issues shown below are pre-existing** (identical on the main branch) "
            "and are not caused by this upgrade.",
            "",
        ])
    lines.extend([
        "### Signal Summary",
        "",
        "| Layer | Result | Confidence | Evidence |",
        "|-------|--------|------------|----------|",
        f"| Build | {b_emoji} {build_verdict.upper()} | HIGH | Exit: {build.get('pr_exit', 'N/A')} |",
        f"| Tests | {t_status} | {'HIGH' if test_exit == 0 else 'MEDIUM'} | {'Exit: ' + str(test_exit) if test_exit is not None else 'N/A'} |",
        f"| Behavioral Probe | {p_status} | {bg.get('confidence', '—').upper() if probe_same is not None else '—'} | — |",
        f"| Reachability | {r_status} | {'HIGH' if reach_count > 0 else 'LOW'} | {'Direct import' if reach_count > 0 else 'Not reached'} |",
        f"| Changelog | {cl_short} | {'—' if cl_status in ('missing', '') or changelog_signal is None else 'MEDIUM'} | — |",
        f"| API Diff | {a_status} | {'—' if a_status.startswith('⏭️') else 'MEDIUM'} | — |",
        "",
    ])

    if cl_status == "breaking" and isinstance(changelog_signal, dict):
        bullets = changelog_signal.get("bullets") or changelog_signal.get("breaking_items") or []
        if bullets:
            lines.append("#### Changelog Breaking Changes")
            lines.append("")
            for b in bullets[:10]:
                lines.append(f"- {b}")
            if len(bullets) > 10:
                lines.append(f"- … and {len(bullets) - 10} more")
            lines.append("")

    lines.extend([
        "### Verdict Logic",
        "",
        f"- **Authoritative verdict:** {verdict} (source: `{av.get('source', 'unknown')}`)",
        f"- **Breakability grade:** {av.get('breakability_grade', 'N/A')}",
        f"- **Severity:** {av.get('severity', 'N/A')} · **Priority:** {av.get('priority', 'N/A')}",
        f"- **Reason:** {av.get('reason', 'N/A')}",
        "",
    ])

    if verdict == "BLOCKED":
        new_errors = build.get("new_errors") or []
        if new_errors:
            lines.append("### Build Errors")
            lines.append("")
            lines.append("```")
            for err in new_errors[:5]:
                lines.append(str(err)[:200])
            lines.append("```")
            lines.append("")
        elif new_failures:
            lines.append("### Test Failures")
            lines.append("")
            for tf in new_failures[:10]:
                lines.append(f"- `{tf}`")
            lines.append("")
        else:
            output_tail = build.get("output_tail") or ""
            if output_tail.strip():
                excerpt = output_tail.strip()[:300]
                lines.append("### Build Output (excerpt)")
                lines.append("")
                lines.append("```")
                lines.append(excerpt)
                lines.append("```")
                lines.append("")

    mr = pr.get("merge_risk") or (pr.get("deterministic") or {}).get("merge_risk") or {}
    mr_tag = mr.get("tag", "")
    if mr_tag:
        mr_emoji = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}.get(mr_tag, "⚪")
        lines.append("### Merge Risk")
        lines.append("")
        lines.append(f"**{mr_emoji} {mr_tag}**")
        mr_reason = mr.get("reason", "")
        if mr_reason:
            lines.append(f"- {mr_reason}")
        lines.append("")

    relevant_deps = [
        d for d in (cross_pr_deps or [])
        if str(d.get("pr_a")) == pr_num or str(d.get("pr_b")) == pr_num
    ]
    if relevant_deps:
        lines.append("### ⚠️ Coordinated Upgrades")
        lines.append("")
        for dep in relevant_deps:
            other = str(dep["pr_b"]) if str(dep.get("pr_a")) == pr_num else str(dep["pr_a"])
            reason = dep.get("reason", "related upgrade")
            order = dep.get("merge_order", "")
            lines.append(f"- **PR #{other}**: {reason}")
            if order:
                lines.append(f"  - Merge order: {order}")
        lines.append("")

    cve_details = pr.get("cve_details") or []
    det_sec_fb = (pr.get("deterministic") or {}).get("security") or {}
    if cve_details:
        lines.append("### Security Impact")
        lines.append("")
        lines.append("This PR addresses the following vulnerabilities:")
        lines.append("")
        for cve in cve_details:
            sev = cve.get("severity", "unknown")
            sev_emoji = "🔴" if sev in ("critical", "high") else "🟡"
            cve_id = cve.get("cve_id") or cve.get("id", "?")
            summary = cve.get("summary", "")
            lines.append(f"- {sev_emoji} **{cve_id}** ({sev}){': ' + summary if summary else ''}")
            advisory = cve.get("advisory_url")
            if advisory:
                lines.append(f"  - Advisory: {advisory}")
        lines.append("")
    elif det_sec_fb.get("isSecurity") and det_sec_fb.get("cveIds"):
        cve_ids = det_sec_fb["cveIds"]
        cvss = det_sec_fb.get("cvssScore") or det_sec_fb.get("cvss_score") or "N/A"
        is_vuln = _is_currently_vulnerable(pr)
        lines.append("### Security Impact")
        lines.append("")
        if is_vuln:
            lines.append(f"This PR remediates {len(cve_ids)} CVE(s) (max CVSS: {cvss}):")
        else:
            lines.append(f"Historical advisory — base version already outside vulnerable range. {len(cve_ids)} CVE(s) listed (max CVSS: {cvss}):")
        lines.append("")
        for cid in cve_ids[:10]:
            lines.append(f"- **{cid}**")
        if len(cve_ids) > 10:
            lines.append(f"- … and {len(cve_ids) - 10} more")
        lines.append("")

    probe_hashes = bg.get("hashes") or bg.get("sha256")
    if isinstance(probe_hashes, dict) and probe_hashes:
        lines.append("<details><summary>Probe SHA256 hashes</summary>")
        lines.append("")
        lines.append("```")
        for k, v in probe_hashes.items():
            lines.append(f"{k}: {v}")
        lines.extend(["```", "", "</details>", ""])

    if files_importing:
        lines.append("<details><summary>Files importing this package</summary>")
        lines.append("")
        for f_path in files_importing[:20]:
            lines.append(f"- `{f_path}`")
        if len(files_importing) > 20:
            lines.append(f"- … and {len(files_importing) - 20} more")
        lines.extend(["", "</details>", ""])

    ecosystem = pr.get("ecosystem", "npm")
    lines.extend([
        "### How We Checked",
        "",
        f"- **Build:** Installed `{pkg}@{to_ver}` and ran full build",
        f"- **Tests:** {'Executed test suite' if test_ran else 'No test execution available'}",
        f"- **Behavioral probe:** {'Compared runtime exports before/after' if probe_same is not None else 'Not available for this package'}",
        f"- **Reachability:** Scanned project source for direct imports of `{pkg}`",
        f"- **Changelog:** Parsed release notes for breaking/deprecation signals",
        "",
        "<details><summary>Independent verification commands</summary>",
        "",
        "```bash",
    ])
    if ecosystem == "gomod":
        lines.extend([
            f"go get {pkg}@{to_ver}",
            "go build ./...",
            "go test ./...",
        ])
    elif ecosystem == "actions":
        lines.extend([
            f"# Review the action's release notes and changelog:",
            f"# https://github.com/{pkg}/releases",
            f"git diff main -- .github/workflows/",
            f"# Verify workflow files reference the correct version ({to_ver})",
        ])
    else:
        lines.extend([
            f"npm install {pkg}@{to_ver}",
            "npm run build",
            "npm test",
        ])
    lines.extend([
        "```",
        "",
        "</details>",
        "",
        "### Recommendation",
        "",
        "1. Review the changelog and release notes manually",
        "2. Run the project's test suite locally",
        "3. Check the files listed above for breaking API usage",
        "",
    ])
    if plan_ref:
        lines.extend([f"📋 Merge plan: {plan_ref}", ""])
    analyzed_date = (metadata or {}).get("timestamp", date.today().isoformat())
    if isinstance(analyzed_date, str) and "T" in analyzed_date:
        analyzed_date = analyzed_date.split("T")[0]
    lines.extend([
        "---",
        f"Mode: Deterministic + Behavioral Probe · Model: template-fallback (no AI analysis performed) · "
        f"Analyzed: {analyzed_date}",
    ])
    if run_url:
        lines.append(f"[Analysis run]({run_url})")

    return "\n".join(lines)


def generate_comments(
    build_results: Dict[str, Any],
    prompt_path: str,
    model: str = "claude-4.5-sonnet",
    run_url: Optional[str] = None,
    merge_plan_issue: Optional[str] = None,
) -> Dict[str, str]:
    """Generate AI comments for all PRs. Returns {pr_num: comment_text}."""
    base_prompt = _read_prompt(prompt_path)
    metadata = build_results.get("metadata", {})
    cross_deps = build_results.get("cross_pr_deps", [])

    top_level = {
        k: build_results.get(k)
        for k in ("workspace_graph", "nestjs_skew", "govulncheck", "security_posture")
        if build_results.get(k)
    }

    prs = build_results.get("prs", {})
    results_list = build_results.get("results", [])

    # Merge verdict_v2 from results[] into prs{} (prs{} often has it as None)
    if prs and results_list:
        _results_v2 = {}
        for _r in results_list:
            _rn = str(_r.get("pr_num", _r.get("pr_number", _r.get("pr", ""))))
            _rv2 = _r.get("verdict_v2")
            if _rn and isinstance(_rv2, dict) and "verdict" in _rv2:
                _results_v2[_rn] = _rv2
        for _num, _pr in prs.items():
            if not isinstance(_pr.get("verdict_v2"), dict) or "verdict" not in (_pr.get("verdict_v2") or {}):
                v2_from_results = _results_v2.get(_num)
                if v2_from_results:
                    _pr["verdict_v2"] = v2_from_results
                else:
                    _pr["verdict_v2"] = authoritative_verdict(_pr)

    pr_items = []
    if prs:
        for pr_num_str, pr_data in prs.items():
            if isinstance(pr_data, dict):
                pr_data.setdefault("pr_num", pr_num_str)
                pr_items.append((pr_num_str, pr_data))
    elif results_list:
        for pr_data in results_list:
            pr_num_str = str(pr_data.get("pr_num", pr_data.get("pr", "")))
            if pr_num_str:
                pr_items.append((pr_num_str, pr_data))

    if not pr_items:
        print("No PRs found in build-results.json", file=sys.stderr)
        return {}

    # Skip PRs with breakability:skip label
    pr_items = [
        (num, data) for num, data in pr_items
        if data.get("build", {}).get("verdict") != "skipped"
    ]

    misattribution_map = _detect_misattribution_groups(
        {num: data for num, data in pr_items}
    )

    backend = Backend.from_env(model=model)
    comments = {}
    diagnostics_log: list = []

    for pr_num, pr_data in sorted(pr_items, key=lambda x: int(x[0]) if x[0].isdigit() else 0):
        print(f"PR#{pr_num}: Generating AI comment (model={backend.model})...", file=sys.stderr)

        prompt = _build_per_pr_prompt(
            base_prompt=base_prompt,
            pr=pr_data,
            pr_num=pr_num,
            metadata=metadata,
            run_url=run_url,
            merge_plan_issue=merge_plan_issue,
            model_name=model,
            cross_deps=cross_deps,
            top_level=top_level,
            misattribution_group=misattribution_map.get(pr_num),
        )

        comment = None
        max_attempts = 2
        _best_response = None
        _best_response_len = 0
        for attempt in range(max_attempts):
            retry_key = f"comment-pr-{pr_num}" if attempt == 0 else f"comment-pr-{pr_num}-retry{attempt}"
            response = backend.invoke(
                prompt,
                namespace="breakability-comment",
                key=retry_key,
            )

            if response:
                if len(response) > _best_response_len:
                    _best_response = response
                    _best_response_len = len(response)
                valid, diag = _validate_comment(response, pr_num, pr_data)
                if valid:
                    comment = _ensure_marker(response)
                    line_count = len(comment.splitlines())
                    print(f"PR#{pr_num}: AI comment generated ({line_count} lines)", file=sys.stderr)
                    break
                if _near_valid(diag):
                    comment = _ensure_marker(response)
                    line_count = len(comment.splitlines())
                    print(f"PR#{pr_num}: AI comment near-valid, accepted ({line_count} lines)", file=sys.stderr)
                    break
                diagnostics_log.append({
                    "pr_num": pr_num,
                    "attempt": attempt,
                    "response_length": len(response),
                    "gate_results": {
                        k: {"passed": v["passed"], "value": str(v.get("value", ""))}
                        for k, v in diag.items()
                    },
                    "timestamp": date.today().isoformat(),
                    "model": backend.model,
                })
                reason = "validation failed"
                preview = response[:200].replace('\n', '\\n')
                print(f"PR#{pr_num}: response preview ({len(response)} chars): {preview}", file=sys.stderr)
                if attempt == max_attempts - 1 and max_attempts == 2 and _best_response_len >= 8000:
                    max_attempts = 3
                    print(f"PR#{pr_num}: substantial AI output ({_best_response_len} chars) — adding third attempt", file=sys.stderr)
            else:
                diagnostics_log.append({
                    "pr_num": pr_num,
                    "attempt": attempt,
                    "response_length": 0,
                    "gate_results": {"empty_response": {"passed": False, "value": "0"}},
                    "timestamp": date.today().isoformat(),
                    "model": backend.model,
                })
                reason = "empty response (0 chars)"
                print(f"PR#{pr_num}: {reason}", file=sys.stderr)
            if attempt < max_attempts - 1:
                print(f"PR#{pr_num}: AI call {reason}, retrying...", file=sys.stderr)

        if comment:
            comment = _reject_false_cve_claim(comment, pr_data, pr_num)
        if comment:
            comment = _reject_cve_direction_error(comment, pr_data, pr_num)
        if comment:
            comment = _reject_fabricated_probe(comment, pr_data, pr_num)
        if comment:
            comment = _strip_agent_narration(comment)
            comment = _strip_govulncheck(comment)
            comment = _downgrade_mismatched_probe(comment, pr_data, pr_num)
            comment = _enforce_verdict_floor(comment, pr_data, pr_num)
            comment = _normalize_verdict_text(comment, pr_num)
            comment = _enforce_merge_risk_tag(comment, pr_data, pr_num)
            comment = _inject_verdict_logic(comment, pr_data, pr_num)
            comments[pr_num] = comment
        else:
            print(f"PR#{pr_num}: AI failed after retry, using fallback", file=sys.stderr)
            comments[pr_num] = _fallback_comment(
                pr_data, pr_num, run_url, merge_plan_issue, model,
                cross_pr_deps=cross_deps, metadata=metadata,
            )

    if diagnostics_log:
        try:
            with open("/tmp/ai-comment-diagnostics.json", "w") as f:
                json.dump(diagnostics_log, f, indent=2)
            print(f"Wrote {len(diagnostics_log)} diagnostic records to /tmp/ai-comment-diagnostics.json", file=sys.stderr)
        except OSError:
            pass

    return comments


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("build_results", help="Path to build-results.json")
    ap.add_argument(
        "--prompt",
        default=os.path.join(
            os.environ.get("BREAKABILITY_PROMPTS_DIR",
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")),
            "breakability-prompt.md"),
        help="Path to breakability-prompt.md",
    )
    ap.add_argument("--model", default="claude-4.5-sonnet", help="AI model to use")
    ap.add_argument("--run-url", default=None, help="GitHub Actions run URL")
    ap.add_argument("--merge-plan-issue", default=None, help="Merge plan issue number")
    ap.add_argument("--pr", type=str, help="Generate for a single PR only")
    ap.add_argument("--stdout", action="store_true", help="Write to stdout instead of files")
    args = ap.parse_args()

    if not os.path.exists(args.prompt):
        print(f"Prompt file not found: {args.prompt}", file=sys.stderr)
        print("Falling back to breakability_analyst.py", file=sys.stderr)
        sys.exit(2)

    with open(args.build_results) as f:
        build_results = json.load(f)

    run_url = args.run_url or os.environ.get("ANALYSIS_RUN_URL")

    comments = generate_comments(
        build_results=build_results,
        prompt_path=args.prompt,
        model=args.model,
        run_url=run_url,
        merge_plan_issue=args.merge_plan_issue,
    )

    if args.pr:
        comments = {k: v for k, v in comments.items() if k == args.pr}

    stub_count = 0
    real_count = 0
    written = 0
    for pr_num, comment in comments.items():
        is_stub = "AI comment generation failed" in comment or "<!-- ai-fallback -->" in comment or len(comment.strip().splitlines()) < 30
        if is_stub:
            stub_count += 1
        else:
            real_count += 1
        if args.stdout:
            print(f"\n{'='*60}\nPR #{pr_num}\n{'='*60}")
            print(comment)
        else:
            output_file = f"/tmp/pr-{pr_num}-comment.md"
            with open(output_file, "w") as f:
                f.write(comment)
            print(f"✅ PR #{pr_num} → {output_file}", file=sys.stderr)
        written += 1

    print(f"\n✅ Generated {written} AI comments ({real_count} AI, {stub_count} stubs)", file=sys.stderr)

    if written > 0 and stub_count == written:
        print(
            f"⚠️ All {stub_count} comments are fallback stubs (AI backend unavailable). "
            "Exiting non-zero so workflow falls back to breakability_analyst.py.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
