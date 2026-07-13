"""
rendering.residual_risk — CLI utility with subcommands for inline Python blocks
extracted from post-fallback-comments.sh (Phase 8 modularization).

Handles residual risk synthesis, companion banner generation, and legacy debug wrapping.
NOTE: post-fallback-comments.sh is NOT modified yet (another agent is working on it).
These modules are created first; the bash replacements will be applied in a later step.
"""
import argparse
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))  # scripts/


# ── synthesize ───────────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 1572-1639.
# env:   V2_RESIDUAL_SUMMARY    — raw residual summary
#        V2_RESIDUAL_CHECK      — raw check guidance
#        V2_RESIDUAL_CHANGELOG  — raw changelog context
#        V2_RESIDUAL_REACH      — raw reachability context
#        R_DEP_TYPE             — dependency type
#        R_BUMP                 — semver bump type
#        R_FROM                 — from version
#        R_TO                   — to version
#        R_USAGE_SIG            — usage signal (POSITIVE/NEGATIVE/UNAVAILABLE)
#        R_CHANGELOG_SIG        — changelog signal (POSITIVE/NEGATIVE/UNAVAILABLE)
# stdout: synthesized residual risk lines
def cmd_synthesize(_args):
    def one_line(name):
        return " ".join(os.environ.get(name, "").replace("{sym}", "").replace("{loc}", "").replace("{path}", "").split())

    summary = one_line("V2_RESIDUAL_SUMMARY")
    check = one_line("V2_RESIDUAL_CHECK")
    lines = [
        f"What to check: {summary}",
        f"→ {check}",
    ]
    changelog = one_line("V2_RESIDUAL_CHANGELOG")
    reach = one_line("V2_RESIDUAL_REACH")
    if changelog:
        lines.append(f"Declared change: {changelog}")
    if reach:
        lines.append(f"Reachable at: {reach}")

    # ── Deterministic RESIDUAL-RISK synthesis (no committed behavioral grade) ──────────
    # When there is no test/oracle proof, a dev without a good suite still needs a defensible
    # call. Synthesize the residual risk from signals we ALREADY have: dependency type
    # (prod vs dev/transitive = blast radius), semver bump (breaking-change likelihood), and
    # whether the changed surface is even reachable from our code (usage signal). This answers
    # the buyer question "if no/weak tests, what confidence remains and what risk is left".
    dep_type = os.environ.get("R_DEP_TYPE", "?").strip().lower()
    bump = os.environ.get("R_BUMP", "?").strip().lower()
    usage_sig = os.environ.get("R_USAGE_SIG", "UNAVAILABLE").strip().upper()
    changelog_sig = os.environ.get("R_CHANGELOG_SIG", "UNAVAILABLE").strip().upper()
    fr, to = os.environ.get("R_FROM", "?"), os.environ.get("R_TO", "?")

    factors = []
    # Blast radius
    if dep_type in ("development", "dev", "test", "indirect", "transitive"):
        factors.append(f"{dep_type} dependency (limited blast radius — not shipped in the prod call path)")
    elif dep_type in ("production", "direct", "prod", "runtime"):
        factors.append("production/direct dependency (changes can reach shipped code paths)")
    # Semver risk
    if bump == "patch":
        factors.append("patch bump (intended bug-fix only; lowest semver risk)")
    elif bump == "minor":
        factors.append("minor bump (additive by semver; behavioral drift possible)")
    elif bump == "major":
        factors.append(f"major bump {fr}->{to} (semver signals breaking changes — highest risk)")
    # Reachability of the changed surface
    if usage_sig == "NEGATIVE":
        factors.append("changed API surface appears UNREACHABLE from your code (probe found no call site)")
    elif usage_sig == "POSITIVE":
        factors.append("changed API surface IS reached by your code (review those call sites)")

    if factors:
        lines.append("Residual risk: " + "; ".join(factors) + ".")
        if usage_sig == "NEGATIVE" and bump in ("patch", "minor") and dep_type in ("development", "dev", "test", "indirect", "transitive"):
            lines.append("→ Net: LOW residual risk — unreachable changed surface on a non-prod " + bump + " bump. Safe to merge if the build is green.")
        elif bump == "major" or usage_sig == "POSITIVE":
            lines.append("→ Net: elevated residual risk — review the reached call sites / breaking-change notes before merging.")

    # TODO(AI-LAYER): the deterministic synthesis above cannot READ the changelog/release notes
    # to confirm WHICH breaking changes apply, nor rank which reachable changes actually matter.
    # When changelog evidence is unavailable (R_CHANGELOG_SIG=UNAVAILABLE), an LLM layer fed the
    # from->to release notes + the reached call sites would add: plain-English "safe because X",
    # breaking-change triage, and probabilistic risk. build-results.json already carries usages,
    # dep_type, bump, from/to; it LACKS extracted changelog text — that is the AI layer to add.
    if changelog_sig == "UNAVAILABLE":
        lines.append("Note: changelog/release-note text was not available to the tool, so breaking-change confirmation is deferred (AI-layer opportunity).")

    print("\n".join(lines))


# ── companion_banner ─────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 1652-1692.
# env:   PR_NUM       — current PR number
#        RESULTS_FILE — path to build-results.json
# stdout: companion banner markdown (or empty)
def cmd_companion_banner(_args):
    pr_num = str(os.environ.get("PR_NUM", ""))
    try:
        with open(os.environ["RESULTS_FILE"]) as fh:
            data = json.load(fh)
    except Exception:
        raise SystemExit(0)
    prs = data.get("prs", {})
    cross = data.get("cross_pr_deps", []) or []
    _BLOCKING = {"fail", "pre_existing_plus_new", "vulns_introduced"}

    def _is_blocked(n):
        p = prs.get(str(n)) or {}
        v = (p.get("build", {}) or {}).get("verdict", "")
        if v in _BLOCKING:
            return True
        if p.get("vuln_status") == "vulns_found" and (p.get("vuln_new_findings") or []):
            return True
        # Match the merge-plan blocked bucket: a committed verdict_v2 == BLOCKED also blocks, even
        # when the build is green (so the banner agrees with the plan companion_blocked routing).
        v2 = p.get("verdict_v2")
        if isinstance(v2, dict) and v2.get("verdict") == "BLOCKED":
            return True
        return False

    blockers = []
    # Skip the banner if THIS PR is itself blocked -- its own headline already says so, and the
    # "even though this PR verifies clean" wording would be wrong.
    if not _is_blocked(pr_num):
        for g in cross:
            a, b = str(g.get("pr_a", "")), str(g.get("pr_b", ""))
            other = None
            if pr_num == a:
                other = b
            elif pr_num == b:
                other = a
            if other and _is_blocked(other) and other not in blockers:
                blockers.append(other)
    if blockers:
        nums = ", ".join(f"#{n}" for n in blockers)
        print(f"> ⛔ **DO NOT MERGE YET — blocked by companion PR(s) {nums}.** This is a coordinated upgrade: its companion currently fails build or introduces new CVEs. Even though this PR verifies clean, merging it alone can break the shared dependency set. Fix {nums} first, then merge them together. (See the merge plan for ordering.)")


# ── debug_wrap ───────────────────────────────────────────────────────────────
# Replaces post-fallback-comments.sh lines 1705-1743.
# env:   COMMENT_BODY         — existing comment body
#        V2_HEADLINE          — v2 headline
#        V2_COMPANION_BANNER  — companion banner (may be empty)
#        V2_RESIDUAL_BLOCK    — residual risk block (may be empty)
#        V2_SIGNALS_TABLE     — signals table markdown
# stdout: wrapped comment with v2 headline and legacy in details
def cmd_debug_wrap(_args):
    legacy = os.environ.get("COMMENT_BODY", "")
    headline = os.environ.get("V2_HEADLINE", "").strip()
    companion = os.environ.get("V2_COMPANION_BANNER", "").strip()
    residual = os.environ.get("V2_RESIDUAL_BLOCK", "").strip()
    signals = os.environ.get("V2_SIGNALS_TABLE", "").strip()

    legacy = legacy.replace("{sym}", "").replace("{loc}", "").replace("{path}", "")
    if legacy.startswith("<!-- breakability-check -->\n"):
        legacy = legacy.split("\n", 1)[1]
    legacy = legacy.strip()

    parts = [headline]
    if companion:
        parts.append(companion)
    if residual:
        parts.append(residual)
    if signals:
        parts.append(signals)
    parts.append("<!-- breakability-check -->")
    if legacy:
        parts.append("<details><summary>Internal merge-risk detail</summary>\n\n" + legacy + "\n</details>")

    _head = [headline] + ([companion] if companion else [])
    _rest = parts[len(_head):]
    body = "\n\n".join(_head) + "\n\n" + "\n\n".join(_rest) if _rest else "\n\n".join(_head)
    for marker in ("</details>### ", "</details>\n### "):
        while marker in body:
            body = body.replace(marker, "</details>\n\n### ")
    normalized = []
    for line in body.splitlines():
        if line.startswith("### ") and normalized and normalized[-1] != "":
            normalized.append("")
        normalized.append(line)
    body = "\n".join(normalized)
    print(body)


# ── main ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Residual risk subcommands extracted from post-fallback-comments.sh"
    )
    sub = parser.add_subparsers(dest="cmd")

    # synthesize
    sub.add_parser("synthesize",
                   help="Synthesize residual risk block from env vars")

    # companion_banner
    sub.add_parser("companion_banner",
                   help="Generate companion PR blocked banner (reads env vars)")

    # debug_wrap
    sub.add_parser("debug_wrap",
                   help="Wrap legacy comment in v2 headline + details (reads env vars)")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "synthesize": cmd_synthesize,
        "companion_banner": cmd_companion_banner,
        "debug_wrap": cmd_debug_wrap,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
