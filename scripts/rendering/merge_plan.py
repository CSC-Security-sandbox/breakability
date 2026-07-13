#!/usr/bin/env python3
"""Generate the merge-plan issue body from build-results.json.

Reads /tmp/build-results.json (or the path can be changed by editing the
source) and prints the complete merge-plan Markdown to stdout.

Usage:
    python3 rendering/merge_plan.py
"""

import json, sys, subprocess
from datetime import datetime, timezone

# ci_review_tier is the canonical CI-sensitivity classifier (single source of
# truth, also used by policy_lowering.py and tested in test_ci_classifier.py).
sys.path.insert(0, sys.path[0] if sys.path else ".")
from ci_classifier import ci_review_tier

with open("/tmp/build-results.json") as f:
    data = json.load(f)

prs = data.get("prs", {})
meta = data.get("metadata", {})
cross = data.get("cross_pr_deps", [])
security = data.get("security_posture", {})

# Count total open PRs (not just Dependabot) for completeness note
try:
    result = subprocess.run(
        ["gh", "pr", "list", "--state", "open", "--json", "number", "-q", "length"],
        capture_output=True, text=True, timeout=30
    )
    total_open_prs = int(result.stdout.strip()) if result.returncode == 0 else 0
except (Exception,):
    total_open_prs = 0

non_dependabot_count = max(0, total_open_prs - len(prs))

# Display helper: 0.x semver versions may contain breaking changes
def fmt_bump(bump, from_ver=""):
    """Format bump type for display. Only flags 0.x major bumps, not real v1→v2."""
    if bump == "major":
        fv = from_ver.lstrip("v").split(".")[0] if from_ver else ""
        if fv == "0":
            return "major ⚠️ (0.x unstable)"
        return "major"
    return bump

def _bg_cited_grade(pr):
    # Returns the committed behavioral-oracle grade ('high'/'medium'/'low'/'none') ONLY when
    # it is cited (has rationale/guidance/evidence); else None. This is the single source of
    # truth used to reconcile routing, the merge-plan row tag, and the PR-comment headline.
    bg = pr.get("behavioral_grade") or {}
    src = str(bg.get("source", "")).strip().lower()
    cited = src in ("reasoning", "probe") and bool(
        str(bg.get("rationale", "")).strip() or str(bg.get("guidance", "")).strip()
        or str(bg.get("evidence", "")).strip())
    if not cited:
        return None
    g = str(bg.get("grade", "")).strip().lower()
    return g if g in ("high", "medium", "low", "none") else None

import re as _v2_re
def committed_v2_verdict(pr):
    # The authoritative per-PR verdict, validated EXACTLY like get_verdict_v2() in the comment
    # poster (verdict in {SAFE,REVIEW,BLOCKED}, confidence L0-L5, priority P0-P3). Fail-closed to
    # REVIEW when missing/invalid, so the merge-plan bucket matches the PR comment's own
    # fail-closed verdict and the two can never contradict.
    v2 = pr.get("verdict_v2")
    if not isinstance(v2, dict):
        return "REVIEW"
    verdict = v2.get("verdict")
    conf = v2.get("confidence")
    prio = v2.get("priority")
    if verdict not in ("SAFE", "REVIEW", "BLOCKED"):
        return "REVIEW"
    if not isinstance(conf, str) or not _v2_re.fullmatch(r"L[0-5]", conf):
        return "REVIEW"
    if not isinstance(prio, str) or not _v2_re.fullmatch(r"P[0-3]", prio):
        return "REVIEW"
    return verdict

def _det_risk_tag(pr):
    # The raw deterministic merge_risk tag, normalized to a bare High/Medium/Low word.
    raw = ((pr.get("merge_risk") or (pr.get("deterministic") or {}).get("merge_risk") or {}).get("tag")) or "Medium"
    first = str(raw).replace("—", " ").replace("(", " ").split()[0].strip().capitalize() if str(raw).strip() else "Medium"
    return first if first in ("High", "Medium", "Low") else "Medium"

def effective_risk_tag(pr):
    # ONE verdict across every surface (routing / plan row / headline). The deterministic
    # merge_risk is a FLOOR: a cited behavioral grade may RAISE the risk above it, but a
    # behavioral none/low may NOT erase a deterministic High/Medium signal (floor invariant).
    _RANK = {"Low": 0, "Medium": 1, "High": 2}
    det_tag = _det_risk_tag(pr)
    g = _bg_cited_grade(pr)
    if g is None:
        return det_tag
    beh_tag = {"high": "High", "medium": "Medium", "low": "Low", "none": "Low"}[g]
    return beh_tag if _RANK[beh_tag] >= _RANK[det_tag] else det_tag

def headline_severity(pr):
    # Reproduce the per-PR comment headline grade EXACTLY (post-fallback get_verdict_v2 + the
    # _GRADE block) so the merge-plan severity can never disagree with the PR comment:
    #   - verdict_v2 missing OR verdict/confidence/priority invalid -> fail-closed "medium"
    #     (NEVER read a stale `severity` off a record that failed validation).
    #   - BLOCKED -> high (wins over a cited grade, matching the bash case order).
    #   - cited behavioral-oracle grade -> that grade (SAFE/REVIEW only).
    #   - else verdict_v2.severity if EXACTLY lowercase-valid, else derive
    #     {SAFE:low, REVIEW:medium} (matches get_verdict_v2's fail-safe derivation).
    v2 = pr.get("verdict_v2")
    if not isinstance(v2, dict):
        return "medium"
    verdict = v2.get("verdict")
    conf = v2.get("confidence")
    prio = v2.get("priority")
    if verdict not in ("SAFE", "REVIEW", "BLOCKED"):
        return "medium"
    if not isinstance(conf, str) or not _v2_re.fullmatch(r"L[0-5]", conf):
        return "medium"
    if not isinstance(prio, str) or not _v2_re.fullmatch(r"P[0-3]", prio):
        return "medium"
    if verdict == "BLOCKED":
        return "high"
    g = _bg_cited_grade(pr)
    if g is not None:
        return g
    sev = v2.get("severity")
    if sev in ("none", "low", "medium", "high"):
        base_sev = sev
    else:
        base_sev = {"SAFE": "low", "REVIEW": "medium"}.get(verdict, "medium")
    # CI review-tier floor — mirror the bash _GRADE CI floor exactly: a security-sensitive CI
    # action (auth/token/registry/deploy) must read at least Medium (its body asks for a
    # supply-chain review), never Low/None. Reached only when there is no cited grade (the
    # cited grade returned above), matching the bash `_BG_CITED != 1` guard.
    if pr.get("ecosystem", "") in ("actions", "docker") and ci_review_tier(pr.get("package", ""), pr.get("bump", "")) == "secsens":
        if base_sev in ("none", "low"):
            base_sev = "medium"
    return base_sev

def fmt_merge_risk(pr):
    risk = pr.get("merge_risk") or (pr.get("deterministic") or {}).get("merge_risk") or {}
    tag = risk.get("tag") or "Medium"
    reason = risk.get("reason") or "change evidence is limited; default caution"
    evidence = risk.get("evidenceAxis") or "limited evidence"
    build_verification = risk.get("buildVerificationAxis") or risk.get("confidenceAxis") or pr.get("verification_label") or "unverified"
    # The deterministic reason is built before the behavioral oracle runs, so it ends in a
    # "verify against the release notes" punt. When the oracle later committed a CITED grade,
    # that punt is stale here too (same fix as the per-PR comment) — strip it.
    bg = pr.get("behavioral_grade") or {}
    bg_src = str(bg.get("source", "")).strip().lower()
    bg_cited = bg_src in ("reasoning", "probe") and bool(
        str(bg.get("rationale", "")).strip() or str(bg.get("guidance", "")).strip()
        or str(bg.get("evidence", "")).strip())
    if bg_cited and "verify against the release notes" in reason:
        for sep in (" — verify against the release notes", "; verify against the release notes",
                    ", but verify against the release notes", " verify against the release notes"):
            reason = reason.replace(sep, "")
        glabel = str(bg.get("grade", "medium")).strip().capitalize() or "Medium"
        reason = reason + f" — behavioral oracle graded exposure {glabel} (see PR comment)"
    # Reconcile the merge-plan risk TAG with the committed behavioral grade so this row and
    # the PR-comment headline can never disagree (PR#38 headline Medium vs plan High).
    tag = effective_risk_tag(pr)
    oracle_conf = str(bg.get("confidence", "")).strip().lower() if bg_cited else "not available"
    if oracle_conf not in ("low", "medium", "high"):
        oracle_conf = "cited" if bg_cited else "not available"
    return f"{tag} (Evidence: {evidence} × Build verification: {build_verification} × Oracle confidence: {oracle_conf}) — {reason}"

# Categorize PRs
safe = []        # pass verdicts + pre_existing with L2+ verification
blocked = []     # fail / pre_existing_plus_new
review = []      # pre_existing (unverified) / error / infra_error
skipped = []     # skip (breakability:skip label)
ci_only = []     # V8 FIX (H3): Actions/Docker PRs — no build verification needed
not_analyzed = []  # PRs from cancelled/incomplete batches
cancelled = []   # V8 FIX (C2): discovered but not in results

for num, pr in sorted(prs.items(), key=lambda x: int(x[0])):
    v = pr.get("build", {}).get("verdict", "?")
    pkg = pr.get("package", "?")
    fr = pr.get("from", "?")
    to = pr.get("to", "?")
    bump = pr.get("bump", "?")
    dep_type = pr.get("dep_type", "?")
    ver = pr.get("verification_label", "?")
    cves = pr.get("cves", [])
    eco = pr.get("ecosystem", "?")
    install_ok = pr.get("install_ok", False)
    pkg_dir = pr.get("pkg_dir", "/")
    error_class = pr.get("build", {}).get("error_class", "")
    new_errors = pr.get("build", {}).get("new_errors", [])
    main_exit = pr.get("build", {}).get("main_exit", -1)
    v2 = pr.get("verdict_v2") if isinstance(pr.get("verdict_v2"), dict) else {}
    entry = {"num": num, "pkg": pkg, "from": fr, "to": to, "bump": bump, "dep_type": dep_type, "ver": ver, "cves": cves, "eco": eco, "verdict": v, "install_ok": install_ok, "pkg_dir": pkg_dir, "error_class": error_class, "new_error_count": len(new_errors), "main_exit": main_exit, "merge_risk": fmt_merge_risk(pr), "behavioral_grade": pr.get("behavioral_grade") or {}, "severity": headline_severity(pr), "ci_tier": (ci_review_tier(pkg, bump) if eco in ("actions", "docker") else ""), "v2_reason": v2.get("reason") or (v2.get("residual") or {}).get("summary") or "", "v2_check": (v2.get("residual") or {}).get("check") or ""}

    # V9.8 iter6 (A): security verdict gate — a PR that INTRODUCES new CVEs must never be "safe"
    vuln_status = pr.get("vuln_status", "")
    vuln_new = pr.get("vuln_new_findings", [])
    if vuln_status == "vulns_found" and vuln_new:
        entry["vuln_new_findings"] = vuln_new
        entry["vuln_new_count"] = len(vuln_new)
        if v != "vulns_introduced":
            entry["verdict"] = "vulns_introduced"
            entry["original_verdict"] = v
            v = "vulns_introduced"

    # V9.9 iter9: govulncheck OOM/timeout → review (not safe) — user must verify manually
    if vuln_status in ("failed_oom", "failed_timeout") and v == "pass":
        entry["vuln_incomplete"] = True
        review.append(entry)
    elif v == "skipped":
        skipped.append(entry)
    elif v == "skip":
        skipped.append(entry)
    elif v == "cancelled":
        cancelled.append(entry)
    elif eco in ("actions",) or ver == "CI_ONLY":
        # V8 FIX (H3): Separate CI-only PRs — don't inflate "verified" count
        ci_only.append(entry)
    elif committed_v2_verdict(pr) == "BLOCKED" and v in ("pass", "pre_existing"):
        # ONE-COMMITTED-VERDICT: committed_v2_verdict() is the authoritative per-PR verdict the
        # comment headline/body are built from (fail-closed to REVIEW exactly like the poster). If
        # it says BLOCKED, the merge plan MUST agree — a green build does not override a committed
        # BLOCKED (PR#10/#23 clash).
        entry["v2_blocked"] = True
        blocked.append(entry)
    elif committed_v2_verdict(pr) == "REVIEW" and v in ("pass", "pre_existing"):
        # A committed REVIEW verdict routes to Manual Review here, so the plan can never say
        # "SAFE — merge now" while the PR comment says "review required".
        #
        # SOFT-REVIEW REFINEMENT (restores the reference plan's "Build Passes — Review
        # Recommended (L2/L3)" tier): a build-clean PR whose only uncertainty is a missing
        # changelog / unverifiable (racy) tests — NOT a high-severity break, NOT security-
        # sensitive, NOT a reachable declared break — is "review recommended", not the
        # manual-review wall. It routes to the `safe` list, where ver<L4 renders it under
        # "Build Passes — Review Recommended". Hard guards below keep anything risky out, and
        # the `soft_review` flag + the ver-not-L4/L5 guard ensure it can never be read as
        # "safe to merge now" (the L4 "tests pass" section excludes it).
        _vc = entry.get("v2_check", "")
        _sev = entry.get("severity", "medium")
        _is_break_reachable = (
            _vc == "review:break-reachable-api"
            or bool((pr.get("declared_break_reachability") or {}).get("prod_reachable"))
        )
        _is_sec = (
            bool(entry.get("cves"))
            or _vc == "review:security-sensitive"
            or entry.get("ci_tier") == "secsens"
            or bool(pr.get("vuln_new_findings"))
        )
        _ver = entry.get("ver", "") or ""
        _soft_review = (
            _vc in ("review:uncertain-critical-signal", "review:residual-or-uncertain")
            and _sev in ("low", "medium", "none")
            and not _is_break_reachable
            and not _is_sec
            and not entry.get("vuln_incomplete")
            and not (_ver.startswith("L4") or _ver.startswith("L5"))
            and effective_risk_tag(pr) != "High"
        )
        if _soft_review:
            entry["soft_review"] = True
            safe.append(entry)
        else:
            entry["v2_review"] = True
            review.append(entry)
    elif committed_v2_verdict(pr) == "SAFE" and v in ("pass", "pre_existing"):
        # Committed SAFE normally wins over the raw deterministic heuristic. EXCEPTION (floor
        # invariant): a behavioral none/low must not erase a deterministic High signal. If the
        # effective risk is still High here, the deterministic floor stands and it routes to
        # review — a behavioral oracle cannot lower the final merge action below a det High.
        if effective_risk_tag(pr) == "High":
            entry["high_merge_risk"] = True
            review.append(entry)
        else:
            entry["v2_safe"] = True
            safe.append(entry)
    elif effective_risk_tag(pr) == "High" and v in ("pass", "pre_existing"):
        # FALSE-SAFE GUARD (fallback only when verdict_v2 is entirely absent/invalid): a High
        # effective risk must go to REVIEW, not safe.
        entry["high_merge_risk"] = True
        review.append(entry)
    elif (((pr.get("declared_break_reachability") or {}).get("reachability_kind") == "import")
          and (pr.get("declared_break_reachability") or {}).get("prod_reachable")
          and v in ("pass", "pre_existing")):
        # FALSE-SAFE GUARD (merge plan): a Medium import-reachable BEHAVIORAL declaration is
        # unverifiable by build/test — route to REVIEW (not safe), matching the per-PR
        # "REVIEW SUGGESTED" comment.
        entry["declared_behavioral_review"] = True
        review.append(entry)
    elif v in ("pass",):
        safe.append(entry)
    elif v in ("fail", "pre_existing_plus_new"):
        blocked.append(entry)
    elif v == "pre_existing":
        # pre_existing with L2+ verification = safe (same errors, no new problems)
        # pre_existing with L0/L1 and zero new errors = likely safe
        # pre_existing with L0 and new errors or no comparison = needs review
        if ver.startswith("L2") or ver.startswith("L3") or ver.startswith("L4") or ver.startswith("L5"):
            safe.append(entry)
        else:
            review.append(entry)
    elif v in ("error", "security_review"):
        review.append(entry)
    elif v == "vulns_introduced":
        # V9.8 iter6 (A): PR introduces NEW CVEs → blocked, not safe
        blocked.append(entry)
    elif v == "conflict":
        blocked.append(entry)
    else:
        not_analyzed.append(entry)

# ── V9.6 FIX: Coordinated upgrade companion blocking ─────────────────────────
# If a PR is "safe" but its coordinated-upgrade companion is "blocked",
# move it from safe to a separate companion_blocked list with explanation.
# This prevents showing "#30 Safe" when "#21 Fix Required — must merge together".
blocked_nums = {e["num"] for e in blocked}
blocked_map = {e["num"]: e for e in blocked}
companion_blocked = []
safe_after_coord = []
for entry in safe:
    num = entry["num"]
    companion_blocked_by = []
    companion_has_vulns = False
    for group in cross:
        pr_a = str(group.get("pr_a", ""))
        pr_b = str(group.get("pr_b", ""))
        if num == pr_a and pr_b in blocked_map:
            companion_blocked_by.append(pr_b)
            if blocked_map[pr_b].get("verdict") == "vulns_introduced":
                companion_has_vulns = True
        elif num == pr_b and pr_a in blocked_map:
            companion_blocked_by.append(pr_a)
            if blocked_map[pr_a].get("verdict") == "vulns_introduced":
                companion_has_vulns = True
    if companion_blocked_by:
        entry = dict(entry)
        entry["companion_blocked_by"] = companion_blocked_by
        if companion_has_vulns:
            entry["verdict"] = "vulns_introduced"
            entry["vuln_note"] = "same target version as companion PR which introduces CVEs"
        companion_blocked.append(entry)
    else:
        safe_after_coord.append(entry)
safe = safe_after_coord

# Build markdown
missing_selected = meta.get('missing_pr_numbers') or []
if not meta.get('incomplete'):
    if missing_selected:
        meta['incomplete'] = True
        meta['missing_pr_count'] = len(missing_selected)
    elif meta.get('subset_requested') and meta.get('requested_pr_numbers') and len(prs) < len(meta.get('requested_pr_numbers') or []):
        meta['incomplete'] = True
        meta['missing_pr_count'] = len(meta.get('requested_pr_numbers') or []) - len(prs)
    elif total_open_prs and len(prs) == 0:
        meta['incomplete'] = True
        meta['missing_pr_count'] = total_open_prs
no_successfully_analyzed = not any((pr.get('build') or {}).get('verdict') not in ('cancelled', 'skipped', 'skip') for pr in prs.values())

lines = []
lines.append("<!-- breakability-merge-plan -->")
lines.append(f"# 📋 Breakability Merge Plan")
lines.append(f"")
_gen_ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
lines.append(f"**Generated:** {_gen_ts} (deterministic)")
lines.append(f"**PRs analyzed:** {len(prs)} Dependabot PRs")
if non_dependabot_count > 0:
    lines.append(f"**Not analyzed:** {non_dependabot_count} non-Dependabot PR(s) (out of scope — this tool only analyzes Dependabot dependency upgrades)")
lines.append(f"")

# V8 FIX (M3): Staleness banner — critical for developer trust (Blind Spot 4A)
lines.append(f"> ⏱️ **Snapshot** generated at `{_gen_ts}`. PR states may have changed since analysis.")
lines.append(f"> To refresh: `gh workflow run breakability-agent.yml`")

# V9.5 FIX: Warn if batch was cancelled/incomplete
if meta.get('incomplete'):
    _missing = meta.get('missing_pr_count', '?')
    _ibs = meta.get('incomplete_batches', [])
    lines.append(f"> ")
    lines.append(f"> ⚠️ **INCOMPLETE RUN:** {_missing} PRs were NOT analyzed (batch{'es' if len(_ibs) != 1 else ''} {', '.join(_ibs) if _ibs else '?'} cancelled/failed).")
    lines.append(f"> PRs missing from this plan should be re-analyzed before merging.")
lines.append("")

# QUICK ACTION section — high-level guidance without jargon (replaces severity-firsted complexity)
# This pre-computes what the developer needs to do RIGHT NOW, hiding verification labels in collapsibles below.
lines.append("## ⚡ What to Do Next")
lines.append("")
lines.append("> **TLDR:** Jump to [Developer Action Summary](#developer-action-summary) for numbered merge steps. Or:")
lines.append("")
_act_high_risk = len([e for e in (safe + blocked + review + ci_only + companion_blocked) if e.get("severity") == "high"])
_act_med_risk = len([e for e in (safe + blocked + review + ci_only + companion_blocked) if e.get("severity") == "medium"])
_act_low_risk = len([e for e in (safe + blocked + review + ci_only + companion_blocked) if e.get("severity") == "low"])
_act_blocked = len(blocked)
_quick_cve_fix_prs = set(str(f["pr"]) for f in security.get("cve_fixes", []))
_quick_sec_safe = [e for e in safe + ci_only if e.get("cves") or e["num"] in _quick_cve_fix_prs]
_quick_sec_blocked = [e for e in blocked if e.get("cves") or e["num"] in _quick_cve_fix_prs]
_act_security = len(_quick_sec_safe) + len(_quick_sec_blocked)
_act_msg = []
if _act_blocked > 0:
    _act_msg.append(f"🛑 **Fix first:** {_act_blocked} PR(s) have blocking verification issues — see 'Fix Required' below.")
if _act_security > 0:
    _act_msg.append(f"🔐 **Priority merge:** {_act_security} PR(s) fix known CVEs — merge them first.")
if _act_high_risk > 0:
    _act_msg.append(f"🔴 **Review required:** {_act_high_risk} PR(s) need careful review before merge.")
if _act_med_risk > 0:
    _act_msg.append(f"📋 **Follow the numbered plan:** {_act_med_risk} PR(s) need review/glance handling — see exact actions below.")
if _act_low_risk > 0 and not _act_msg:
    _act_msg.append(f"✅ **Most are safe:** {_act_low_risk} routine upgrades ready to merge.")
if not _act_msg:
    if not prs or no_successfully_analyzed:
        _act_msg.append("⚠️ **No merge order available:** this run produced zero successfully analyzed PRs; re-run the analysis before merging.")
    elif meta.get('incomplete'):
        _act_msg.append("⚠️ **Incomplete run:** do not treat this as all clear; re-run missing batches before merging.")
    else:
        _act_msg.append("✅ **All clear:** All analyzed PRs are ready to merge.")
if meta.get('incomplete') and _act_msg and not any('Incomplete run' in m or 'No merge order available' in m for m in _act_msg):
    _act_msg.insert(0, "⚠️ **Incomplete run:** do not treat this as all clear; re-run missing batches before merging.")
if no_successfully_analyzed and _act_msg and not any('No merge order available' in m for m in _act_msg):
    _act_msg.insert(0, "⚠️ **No merge order available:** this run produced zero successfully analyzed PRs; re-run the analysis before merging.")
for msg in _act_msg:
    lines.append(f"- {msg}")
lines.append("")

# V9.7: govulncheck status aggregation — top-level banner shows scan health + vuln findings.
# V9.7b: distinguish NEW findings (introduced by PR) from pre-existing-on-main.
_vuln_not_installed = 0
_vuln_failed_oom   = 0
_vuln_failed_timeout = 0
_vuln_failed_error = 0
_vuln_found = []   # list of (pr_num, [new_findings])
_vuln_ok_preexisting = 0
_vuln_ok = 0
_main_baseline = data.get("govulncheck", {}).get("main_baseline", {})
_main_baseline_status = _main_baseline.get("status", "unknown")
_main_baseline_findings = _main_baseline.get("findings", [])
for _pn, _pr in prs.items():
    _vs = _pr.get("vuln_status", "")
    _new = _pr.get("vuln_new_findings", [])
    if _vs == "not_installed": _vuln_not_installed += 1
    elif _vs == "failed_oom":  _vuln_failed_oom += 1
    elif _vs == "failed_timeout": _vuln_failed_timeout += 1
    elif _vs == "failed_error":   _vuln_failed_error += 1
    elif _vs == "vulns_found" and _new:
        _vuln_found.append((_pn, _new))
    elif _vs == "ok_preexisting":
        _vuln_ok_preexisting += 1
    elif _vs == "ok": _vuln_ok += 1

# Main baseline context — this is key for developer trust
if _main_baseline_findings:
    lines.append("> ")
    lines.append(f"> 🛡️ **Pre-existing vulnerabilities on main:** {len(_main_baseline_findings)} known CVE(s) detected by govulncheck on the main branch (independent of any PR). Example: {', '.join(_main_baseline_findings[:3])}{'…' if len(_main_baseline_findings) > 3 else ''}. These PRs do not introduce or fix them unless explicitly noted.")
    lines.append("")

# Top banner — prioritize NEW vulns introduced by PRs > failures > ok summary
if _vuln_found:
    lines.append("> ")
    _fb_list = ", ".join(f"#{n} ({','.join(findings[:2])})" for n, findings in _vuln_found[:10])
    lines.append(f"> 🚨 **New vulnerabilities INTRODUCED by {len(_vuln_found)} PR(s)** (not present on main): {_fb_list} — review each PR comment before merging.")
    lines.append("")
if _vuln_failed_oom or _vuln_failed_timeout or _vuln_failed_error:
    _fails = []
    if _vuln_failed_oom:     _fails.append(f"{_vuln_failed_oom} OOM")
    if _vuln_failed_timeout: _fails.append(f"{_vuln_failed_timeout} timed out")
    if _vuln_failed_error:   _fails.append(f"{_vuln_failed_error} error")
    lines.append("> ")
    lines.append(f"> ⚠️ **govulncheck incomplete on {sum([_vuln_failed_oom, _vuln_failed_timeout, _vuln_failed_error])} PR(s)** ({', '.join(_fails)}) — absence of findings in those PRs is NOT proof of safety. Run `govulncheck ./...` locally with GOMEMLIMIT=2GiB before merging.")
    lines.append("")
if _vuln_not_installed:
    lines.append("> ")
    lines.append(f"> ⚠️ **govulncheck not installed on {_vuln_not_installed} PR runner(s)** — vulnerability scan was skipped. Install via `go install golang.org/x/vuln/cmd/govulncheck@latest`.")
    lines.append("")
# Clean summary if scans succeeded with no new findings
if (_vuln_ok + _vuln_ok_preexisting) and not (_vuln_found or _vuln_failed_oom or _vuln_failed_timeout or _vuln_failed_error):
    _clean_parts = []
    if _vuln_ok: _clean_parts.append(f"{_vuln_ok} with no vulns")
    if _vuln_ok_preexisting: _clean_parts.append(f"{_vuln_ok_preexisting} only touching pre-existing vulns on main (no NEW vulns introduced)")
    lines.append(f"> ✅ govulncheck: {' / '.join(_clean_parts)} across {_vuln_ok + _vuln_ok_preexisting} scanned PR(s). No PR introduces new vulnerabilities.")
    lines.append("")

# Summary table
lines.append("<details><summary><strong>📊 Technical Details & Risk Classification</strong> (L-levels, severity, counts)</summary>")
lines.append("")
lines.append("## Summary by Verification Level")
lines.append("")
lines.append(f"| Category | Count |")
lines.append(f"|----------|-------|")
likely_safe_count = sum(1 for e in review if e.get("verdict") == "pre_existing" and e.get("new_error_count", 0) == 0)
unverified_count = sum(1 for e in review if e.get("verdict") == "pre_existing" and e.get("new_error_count", 0) > 0)
needs_review_count = len(review) - likely_safe_count - unverified_count
lines.append(f"| ✅ Safe to merge — tests pass (L4) | {sum(1 for e in safe if e['ver'].startswith('L4') or e['ver'].startswith('L5'))} |")
lines.append(f"| ✅ Build passes — review recommended (L2/L3) | {sum(1 for e in safe if not (e['ver'].startswith('L4') or e['ver'].startswith('L5')))} |")
if companion_blocked:
    lines.append(f"| 🔗 Blocked (safe but companion PR needs fix) | {len(companion_blocked)} |")
if ci_only:
    _ci_sec = [e for e in ci_only if e.get("ci_tier") == "secsens"]
    _ci_maj = [e for e in ci_only if e.get("ci_tier") == "major"]
    _ci_auto = [e for e in ci_only if not e.get("ci_tier")]
    if _ci_auto:
        lines.append(f"| 🔧 CI-only (Actions/Docker — no app impact) | {len(_ci_auto)} |")
    if _ci_maj:
        lines.append(f"| 🟡 CI major action bump — changelog glance | {len(_ci_maj)} |")
    if _ci_sec:
        lines.append(f"| 🔐 CI supply-chain (auth/token/registry/deploy) — security review | {len(_ci_sec)} |")
if likely_safe_count > 0:
    lines.append(f"| ⚙️ Likely safe (deps resolved, no new errors) | {likely_safe_count} |")
if unverified_count > 0:
    lines.append(f"| ⚠️ Unverified (deps failed — infra issue) | {unverified_count} |")
lines.append(f"| ❌ Fix required | {len(blocked)} |")
if needs_review_count > 0:
    # Tier the review wall by the SAME severity shown on each PR headline so a dev sees the
    # true burden: high/medium = genuinely needs a look; low = optional glance. (Addresses
    # "80% review-required defeats the purpose" — most of the wall is usually low/optional.)
    _review_entries = [e for e in review
                       if not (e.get("verdict") == "pre_existing"
                               and e.get("new_error_count", 0) == 0)
                       and not (e.get("verdict") == "pre_existing"
                                and e.get("new_error_count", 0) > 0)]
    _rev_high = sum(1 for e in _review_entries if e.get("severity") == "high")
    _rev_med = sum(1 for e in _review_entries if e.get("severity") == "medium")
    _rev_low = sum(1 for e in _review_entries if e.get("severity") in ("low", "none"))
    if _rev_high:
        lines.append(f"| 🔴 Review required (High) | {_rev_high} |")
    if _rev_med:
        lines.append(f"| 🟠 Review recommended (Medium) | {_rev_med} |")
    if _rev_low:
        lines.append(f"| 🟡 Optional glance (Low) | {_rev_low} |")
    # Fallback: if severity tiering didn't account for every review entry, show the remainder.
    _rev_other = needs_review_count - (_rev_high + _rev_med + _rev_low)
    if _rev_other > 0:
        lines.append(f"| 🔍 Manual review | {_rev_other} |")
if skipped:
    lines.append(f"| ⏭️ Skipped (opted out) | {len(skipped)} |")
if cancelled:
    lines.append(f"| 🚫 Cancelled / Incomplete | {len(cancelled)} |")
if not_analyzed:
    lines.append(f"| ❓ Not analyzed | {len(not_analyzed)} |")
lines.append("")

# Severity summary — the SAME none/low/medium/high grade shown on every PR comment headline
# (single source of truth = headline_severity), so this roll-up and the per-PR headlines can
# never disagree. Replaces the old roll-up that parsed the decoupled legacy merge_risk string.
_all_entries = safe + blocked + review + skipped + ci_only + companion_blocked + not_analyzed + cancelled
if _all_entries:
    from collections import Counter as _Counter
    _sev_counts = _Counter((e.get("severity") or "medium") for e in _all_entries)
    lines.append("## Breakability Summary")
    lines.append("")
    lines.append(
        f"🔴 **High:** {_sev_counts.get('high', 0)} · "
        f"🟠 **Medium:** {_sev_counts.get('medium', 0)} · "
        f"🟡 **Low:** {_sev_counts.get('low', 0)} · "
        f"🟢 **None:** {_sev_counts.get('none', 0)}")
    lines.append("")
    lines.append(
        "> High/Medium = worth a review · Low = optional glance · None = safe to merge. "
        "Severity matches each PR's breakability headline (security-fix PRs show a "
        "merge-priority headline instead).")
    lines.append("")

# Close the Technical Details collapsible section
lines.append("</details>")
lines.append("")

# V8 FIX (M4): Developer Action Summary — prioritized numbered steps (regression from ref plan #39)
lines.append("## Developer Action Summary")
lines.append("")
lines.append("**Plain-English merge guidance — see Technical Details above for verification levels.**")
lines.append("")
_step = 1
# Security fixes first — use BOTH pr-body CVEs AND Dependabot alert matches (cve_fixes)
_cve_fix_prs = set(str(f["pr"]) for f in security.get("cve_fixes", []))
_sec_safe_l4 = [e for e in safe + ci_only if (e.get("cves") or e["num"] in _cve_fix_prs) and (e.get("ver", "").startswith("L4") or e.get("ver", "").startswith("L5")) and not e.get("ci_tier")]
_sec_safe_l2 = [e for e in safe + ci_only if (e.get("cves") or e["num"] in _cve_fix_prs) and (not (e.get("ver", "").startswith("L4") or e.get("ver", "").startswith("L5")) or e.get("ci_tier"))]
_sec_safe = _sec_safe_l4 + _sec_safe_l2  # combined for later reference
_sec_blocked = [e for e in blocked if e.get("cves") or e["num"] in _cve_fix_prs]
def is_optional_glance_entry(e):
    check = str(e.get("v2_check") or "")
    return (
        e.get("severity") in ("low", "none")
        and e.get("verdict") != "pre_existing"
        and check.startswith("glance:")
        and not e.get("cves")
        and e["num"] not in _cve_fix_prs
        and not e.get("ci_tier")
    )
_review_optional_glance = [
    e for e in review
    if is_optional_glance_entry(e)
]
if _sec_safe_l4:
    _sec_nums = ", ".join(f"#{e['num']}" for e in _sec_safe_l4)
    lines.append(f"{_step}. **MERGE NOW — CVE fixes (tests pass):** {_sec_nums} — fix known vulnerabilities right away")
    _step += 1
if _sec_safe_l2:
    _sec_nums = ", ".join(f"#{e['num']}" for e in _sec_safe_l2)
    lines.append(f"{_step}. **REVIEW then MERGE — CVE fixes (build passes, tests not run):** {_sec_nums} — check build details, then merge")
    _step += 1
if _sec_blocked:
    _sec_nums = ", ".join(f"#{e['num']}" for e in _sec_blocked)
    lines.append(f"{_step}. **FIX FIRST — security PRs with blocking issues:** {_sec_nums} — resolve the listed blocker before merging")
    _step += 1
# L4 safe PRs
_l4_safe = [e for e in safe if e.get("ver", "").startswith("L4") and not e.get("cves")]
if _l4_safe:
    lines.append(f"{_step}. **MERGE — tests pass:** {len(_l4_safe)} PR(s) — safest batch, merge together")
    _step += 1
# L2 safe PRs (build passes, tests fail or not run)
_l2_safe = [e for e in safe if not e.get("ver", "").startswith("L4") and not e.get("cves")]
if _l2_safe:
    lines.append(f"{_step}. **GLANCE then MERGE — build passes, tests not run:** {len(_l2_safe)} PR(s) — skim changelog for breaking changes")
    _step += 1
if _review_optional_glance:
    _glance_nums = ", ".join(f"#{e['num']}" for e in _review_optional_glance)
    lines.append(f"{_step}. **GLANCE then MERGE — low breakability:** {_glance_nums} — optional changelog/API skim, not deep review")
    _step += 1
# Companion blocked
if companion_blocked:
    _cb_nums = ", ".join(f"#{e['num']}" for e in companion_blocked)
    lines.append(f"{_step}. **WAIT — paired PRs blocked:** {_cb_nums} — merge these only after fixing their companion PR")
    _step += 1
# CI-only PRs
if ci_only:
    _ci_sec = [e for e in ci_only if e.get("ci_tier") == "secsens"]
    _ci_maj = [e for e in ci_only if e.get("ci_tier") == "major"]
    _ci_auto = [e for e in ci_only if not e.get("ci_tier")]
    if _ci_auto:
        lines.append(f"{_step}. **MERGE — CI/Actions PRs:** {len(_ci_auto)} PR(s) — no app impact")
        _step += 1
    if _ci_maj:
        _ci_maj_nums = ", ".join(f"#{e['num']}" for e in _ci_maj)
        lines.append(f"{_step}. **GLANCE then MERGE — major CI actions:** {_ci_maj_nums} — review for breaking input changes")
        _step += 1
    if _ci_sec:
        _ci_sec_nums = ", ".join(f"#{e['num']}" for e in _ci_sec)
        lines.append(f"{_step}. **REVIEW — supply-chain sensitive CI:** {_ci_sec_nums} — pin to commit SHA, verify permissions")
        _step += 1
# Likely safe
if likely_safe_count > 0:
    lines.append(f"{_step}. **INVESTIGATE — likely safe (unclear baseline):** {likely_safe_count} PR(s) — no new errors detected, but baseline build may be broken")
    _step += 1
# Fix required
_non_sec_blocked = [e for e in blocked if not e.get("cves")]
if _non_sec_blocked:
    lines.append(f"{_step}. **FIX NEEDED:** {len(_non_sec_blocked)} PR(s) have blocking verification issues")
    _step += 1
if _step == 1:
    if not prs or no_successfully_analyzed:
        lines.append(f"{_step}. **NO MERGE ORDER AVAILABLE:** zero PRs were successfully analyzed in this run — re-run breakability analysis.")
    elif meta.get('incomplete'):
        lines.append(f"{_step}. **RERUN REQUIRED:** this run is incomplete; do not merge from this plan until missing PRs are analyzed.")
    else:
        lines.append(f"{_step}. **MERGE:** all analyzed PRs are clear.")
lines.append("")

# Infrastructure banner — when many PRs are in review with the same root cause
if len(review) > 0:
    infra_count = sum(1 for e in review if e.get("verdict") == "pre_existing")
    if infra_count > len(prs) * 0.5:
        # More than half of PRs are pre_existing — likely a systemic issue
        lines.append("> ⚠️ **Infrastructure Issue:** %d of %d PRs have pre-existing build failures (not caused by upgrades)." % (infra_count, len(prs)))
        lines.append("> Fix the baseline build on `main` and re-run analysis to unlock full verification for these PRs.")
        lines.append("> PRs marked \"Likely Safe\" below have no new errors — they are probably safe to merge despite incomplete verification.")
        lines.append("")

        # Show baseline error details so developers know WHAT to fix (end-user feedback 1.2)
        main_build = data.get("main_build", {})
        baseline_errors = []
        # Go baseline errors
        go_data = main_build.get("go", {})
        go_exit = go_data.get("exit", -1)
        go_output = go_data.get("output_tail", "")
        if go_exit is not None and go_exit not in (-1, 0) and go_output:
            error_lines = [l.strip() for l in go_output.split('\n')
                          if 'error' in l.lower() or 'Error' in l or 'FAIL' in l
                          or 'cannot' in l.lower() or 'undefined' in l.lower()][:10]
            if error_lines:
                baseline_errors.extend(error_lines)
        # npm baseline errors
        npm_data = main_build.get("npm", {})
        npm_exit = npm_data.get("exit", -1)
        npm_output = npm_data.get("output_tail", "")
        if npm_exit is not None and npm_exit not in (-1, 0) and npm_output:
            npm_errs = [l.strip() for l in npm_output.split('\n')
                       if 'error' in l.lower() or 'TS' in l][:5]
            if npm_errs:
                baseline_errors.extend(npm_errs)
        # Check per-module baselines from any PR's output (if available)
        error_classes = set()
        for num, pr in prs.items():
            ec = pr.get("build", {}).get("error_class", "")
            if ec and ec != "build_fail":
                error_classes.add(ec)
        if baseline_errors or error_classes:
            lines.append("### Baseline Build Errors on `main`")
            lines.append("")
            if error_classes:
                class_descriptions = {
                    "infra_error": "Infrastructure/network error (GOSUMDB, proxy, or registry issue)",
                    "private_module": "Private module access denied (GOPRIVATE not configured)",
                    "resource_exhaustion": "Out of memory / compiler killed",
                    "timeout": "Build timed out",
                    "cache_corruption": "Go build cache corruption",
                }
                for ec in sorted(error_classes):
                    desc = class_descriptions.get(ec, ec)
                    lines.append(f"- **{ec}:** {desc}")
                lines.append("")
            if baseline_errors:
                lines.append("```")
                for err in baseline_errors[:8]:
                    lines.append(err[:200])
                lines.append("```")
                lines.append("")
            lines.append("Fix these issues on `main`, then re-run: `gh workflow run breakability-agent.yml`")
            lines.append("")

# CVE highlight — union of PR-body CVEs and Dependabot alert-matched fixes
all_cves = []
_cve_fix_prs_set = set(str(f["pr"]) for f in security.get("cve_fixes", []))
# Map PR -> the CVE ids it actually resolves (version-gated, incl. transitive go.mod
# bumps), so Dependabot-only matches don't render blank CVE cells.
_cve_ids_by_pr = {}
for f in security.get("cve_fixes", []):
    _cve_ids_by_pr.setdefault(str(f["pr"]), []).append(f.get("cve_id") or "")
# Track the committed routing bucket for each PR so the verdict shown here can never
# contradict the Manual-Review / Fix-required sections below (one committed verdict).
_cve_bucket = {}
for _catname, cat in [("safe", safe), ("blocked", blocked), ("review", review), ("skipped", skipped), ("ci_only", ci_only), ("companion_blocked", companion_blocked), ("not_analyzed", not_analyzed), ("cancelled", cancelled)]:
    for e in cat:
        if e.get("cves") or e["num"] in _cve_fix_prs_set:
            all_cves.append(e)
            _cve_bucket.setdefault(e["num"], _catname)
if all_cves:
    lines.append("## 🔴 Security — CVEs Fixed by These Upgrades")
    lines.append("")
    lines.append("> **ACTION REQUIRED:** Merge security fix PRs as soon as possible to resolve known vulnerabilities.")
    lines.append("")
    for e in all_cves:
        # Prefer the version-gated Dependabot fix list (merge-results.sh already gated
        # these on first_patched_version vs the resulting incl-transitive go.mod version).
        # Only fall back to raw PR-body CVE claims when there is NO gated match, and mark
        # them unverified — otherwise a PR-body CVE the resulting version does NOT actually
        # reach its fixed-in version (e.g. otel/sdk →1.42 vs a CVE fixed in 1.43) gets
        # wrongly credited as "fixed".
        _gated = [c for c in _cve_ids_by_pr.get(str(e["num"]), []) if c]
        if _gated:
            _cve_list = _gated
            cve_str = ", ".join(_cve_list)
        else:
            _cve_list = [c for c in (e.get("cves") or []) if c]
            cve_str = (", ".join(_cve_list) + " (claimed in PR body — not version-verified vs fixed-in)") if _cve_list else "see Dependabot alerts"
        # Verdict note derived from the COMMITTED bucket (not the raw build verdict):
        # a CVE-fixing PR routed to Manual Review or Fix-required must NOT also read
        # "SAFE — merge now" (the PR#10/#23 self-contradiction).
        _b = _cve_bucket.get(e["num"], "")
        _is_l4 = e.get("ver", "").startswith("L4") or e.get("ver", "").startswith("L5")
        if _b == "blocked":
            verdict_note = " ❌ Fix required before merge"
        elif _b == "review":
            verdict_note = " ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)"
        elif _b == "companion_blocked":
            verdict_note = " 🔗 **Blocked by a companion PR** — see Blocked section below (fix companion first)"
        elif _b == "skipped":
            verdict_note = " ⏭️ Opted out (`breakability:skip`) — merge manually to resolve the CVE"
        elif _b in ("not_analyzed", "cancelled"):
            verdict_note = " ❓ Not analyzed this run — re-run the tool before merging"
        elif _b == "ci_only" and e.get("ci_tier") == "secsens":
            verdict_note = " 🔐 **Review — supply-chain sensitive** (pin SHA, review permissions); merge to resolve the CVE after review"
        elif _b == "ci_only" and e.get("ci_tier") == "major":
            verdict_note = " 🟡 **Major CI action bump** — glance at the changelog, then merge to resolve the CVE"
        elif _b in ("safe", "ci_only"):
            verdict_note = " ✅ **SAFE — merge now** (tests pass, L4)" if _is_l4 else " ⚙️ **Build verified (L2/L3) — tests not verified clean; review then merge**"
        else:
            verdict_note = ""
        lines.append(f"- **PR #{e['num']}** `{e['pkg']}` {e['from']}→{e['to']} — {cve_str}{verdict_note}")
    lines.append("")

# V9.8 iter6 (A): Dedicated security-risk section for PRs that INTRODUCE new CVEs
vulns_introduced = [e for e in blocked if e.get("verdict") == "vulns_introduced"]
if vulns_introduced:
    lines.append("## 🚨 Security Risk — PRs That Introduce NEW Vulnerabilities")
    lines.append("")
    lines.append("> **DO NOT MERGE** these PRs. They add CVEs not present on `main`. Pin to an earlier version, wait for an upstream fix, or close the PR.")
    lines.append("")
    lines.append("| PR | Package | Version | NEW CVEs | Pre-existing |")
    lines.append("|---|---|---|---|---|")
    for e in vulns_introduced:
        cves_new = e.get("vuln_new_findings", [])
        cves_show = ", ".join(cves_new[:5]) + (f" +{len(cves_new)-5} more" if len(cves_new) > 5 else "")
        pre = len([c for c in (e.get("cves") or [])])  # fallback
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {e.get('vuln_new_count', len(cves_new))}: {cves_show} | see PR |")
    lines.append("")

# Safe to merge — split L4 (tests pass) vs L2/L3 (build only)
safe_l4 = [e for e in safe if e["ver"].startswith("L4") or e["ver"].startswith("L5")]
safe_l2 = [e for e in safe if not (e["ver"].startswith("L4") or e["ver"].startswith("L5"))]

if safe_l4:
    lines.append("## ✅ Safe to Merge — Tests Pass (L4 verified, lowest risk)")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
    lines.append("|----|---------|---------|----|------------|-------------|")
    for e in safe_l4:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e['cves'] else ""
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {e['ver']}{cve_badge} |")
    lines.append("")

if safe_l2:
    lines.append("## ✅ Build Passes — Review Recommended (L2/L3 verified)")
    lines.append("")
    lines.append("> Build and type-check pass. Tests were not run or had pre-existing failures. Review changelog for major bumps.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
    lines.append("|----|---------|---------|----|------------|-------------|")
    for e in safe_l2:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e['cves'] else ""
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {e['ver']}{cve_badge} |")
    lines.append("")

# Companion-blocked: safe PRs that can't be merged yet because their coordinated partner is broken
if companion_blocked:
    lines.append("## 🔗 Blocked — Safe but Companion PR Needs Fix First")
    lines.append("")
    lines.append("These PRs pass build verification but are **blocked** because a companion PR (coordinated upgrade) currently has build failures or security issues.")
    lines.append("Fix the companion PR first, then merge both together.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Verification | Blocked By |")
    lines.append("|----|---------|---------|------|------------|-------------|------------|")
    for e in companion_blocked:
        companions = ", ".join(f"#{n}" for n in e.get("companion_blocked_by", []))
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {e['ver']} ✅ | Fix {companions} first |")
    lines.append("")

# Cross-PR deps
if cross:
    lines.append("## 🔗 Coordinated Upgrades (merge together)")
    lines.append("")
    # Group pairwise entries into multi-PR groups by shared package name
    from collections import defaultdict
    _pkg_groups = defaultdict(set)
    _pkg_reason = {}
    _single_groups = []
    for group in cross:
        pr_a = str(group.get("pr_a", "?"))
        pr_b = str(group.get("pr_b", "?"))
        reason = group.get("reason", "related")
        # Extract package name from reason for grouping
        import re as _re
        _m = _re.search(r'`([^`]+)`|Same package \(([^)]+)\)', reason)
        _key = (_m.group(1) or _m.group(2)) if _m else reason[:40]
        _pkg_groups[_key].add(pr_a)
        _pkg_groups[_key].add(pr_b)
        _pkg_reason[_key] = reason
    for pkg_key, pr_set in sorted(_pkg_groups.items()):
        pr_list = sorted(pr_set, key=lambda x: int(x) if x.isdigit() else 99)
        pr_str = " + ".join(f"#{p}" for p in pr_list)
        reason = _pkg_reason.get(pkg_key, pkg_key)
        # P0 FIX: never instruct "merge all together" if any member is blocked
        # (build_fails or introduces NEW CVEs). A coordinated group is only as
        # safe as its weakest member — surfacing this prevents a dangerous merge.
        group_blocked = sorted((p for p in pr_list if p in blocked_nums),
                               key=lambda x: int(x) if x.isdigit() else 99)
        if group_blocked:
            _bb = ", ".join(f"#{n}" for n in group_blocked)
            _reasons = []
            for n in group_blocked:
                bv = blocked_map.get(n, {}).get("verdict", "")
                if bv == "vulns_introduced":
                    _reasons.append(f"#{n} introduces {blocked_map[n].get('vuln_new_count', 0)} NEW CVE(s)")
                elif bv == "fail":
                    _reasons.append(f"#{n} build fails")
                elif bv == "conflict":
                    _reasons.append(f"#{n} has merge conflicts")
                else:
                    _reasons.append(f"#{n} blocked")
            lines.append(f"- ⛔ **{reason}:** {pr_str} — **DO NOT MERGE as a group.** "
                         f"{'; '.join(_reasons)}. Resolve {_bb} first (see sections below); "
                         f"merging the group now would pull in the blocking PR.")
            continue
        # Simplify reason for groups with 3+ PRs
        if len(pr_list) >= 3:
            lines.append(f"- **{reason}:** {pr_str} — merge all {len(pr_list)} together")
        else:
            order = ""
            for group in cross:
                if str(group.get("pr_a")) in pr_set and str(group.get("pr_b")) in pr_set:
                    order = group.get("merge_order", "")
                    break
            order_text = f" ({order})" if order else ""
            lines.append(f"- **{reason}:** {pr_str}{order_text}")
    lines.append("")

# Blocked
if blocked:
    lines.append("## ❌ Fix Required — Do Not Merge")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Issue |")
    lines.append("|----|---------|---------|----|------------|-------|")
    for e in blocked:
        if e["verdict"] == "fail":
            issue = "Build fails"
        elif e["verdict"] == "conflict":
            issue = "Merge conflicts — rebase required"
        elif e["verdict"] == "vulns_introduced":
            issue = f"🚨 {e.get('vuln_new_count', 0)} NEW CVE(s) introduced — see Security Risk section"
        else:
            issue = "New errors on top of pre-existing"
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {issue} |")
    lines.append("")

# Review — split into "Likely Safe" and "Needs Review".
# End-user feedback: L0 pre_existing with zero new errors IS a safety signal.
# The tool compared both branches and found no new errors — that's useful info.
# Only truly "unverified" PRs (where comparison couldn't happen) go into unverified.
likely_safe = [e for e in review if e["verdict"] == "pre_existing" and e.get("new_error_count", 0) == 0]
unverified = [e for e in review if e["verdict"] == "pre_existing" and e.get("new_error_count", 0) > 0]
optional_glance = [
    e for e in review
    if is_optional_glance_entry(e)
]
needs_review = [
    e for e in review
    if e["verdict"] != "pre_existing"
    and not is_optional_glance_entry(e)
]

if likely_safe:
    lines.append("## ⚙️ Likely Safe — No New Errors (pre-existing build failure)")
    lines.append("")
    lines.append("These PRs do **not** introduce new failures. Both `main` and the PR branch")
    lines.append("produce the same build errors. The upgrades are likely safe to merge.")
    lines.append("Fix baseline build on `main` and re-run for full L2+ verification.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Module | Status |")
    lines.append("|----|---------|---------|----|------------|--------|--------|")
    for e in likely_safe:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
        pkg_dir = e.get('pkg_dir', '/')
        mod_col = pkg_dir if pkg_dir != '/' else 'root'
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {mod_col} | {e['ver']} — no new errors{cve_badge} |")
    lines.append("")

if unverified:
    lines.append("## ⚠️ Needs Investigation (new errors detected or comparison failed)")
    lines.append("")
    lines.append("These PRs have new errors or could not be compared against the baseline.")
    lines.append("Manual review is recommended before merging.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Module | Issue |")
    lines.append("|----|---------|---------|----|------------|--------|-------|")
    for e in unverified:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
        pkg_dir = e.get('pkg_dir', '/')
        mod_col = pkg_dir if pkg_dir != '/' else 'root'
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {mod_col} | Deps failed — infra issue{cve_badge} |")
    lines.append("")

if optional_glance:
    lines.append("## 🟡 Optional Glance — Low Breakability")
    lines.append("")
    lines.append("These PR comments are already downgraded to **Low / optional glance** by the committed verdict. Skim the noted evidence, then merge if no project-specific concern appears.")
    lines.append("")
    for e in optional_glance:
        reason = e.get("v2_reason") or "low breakability evidence"
        check = f" (`{e.get('v2_check')}`)" if e.get("v2_check") else ""
        lines.append(f"- **PR #{e['num']}** `{e['pkg']}` {e['from']}→{e['to']} — **Low / optional glance**: {reason}{check}")
    lines.append("")

if needs_review:
    lines.append("## ⚠️ Manual Review Needed")
    lines.append("")
    for e in needs_review:
        bg = e.get("behavioral_grade") or {}
        bg_src = str(bg.get("source", "")).strip().lower()
        bg_cited = bg_src in ("reasoning", "probe") and bool(
            str(bg.get("rationale", "")).strip() or str(bg.get("guidance", "")).strip()
            or str(bg.get("evidence", "")).strip())
        if e["verdict"] == "security_review":
            reason = "Build passes but npm audit found critical/high vulnerabilities"
        elif e.get("declared_behavioral_review") or e.get("high_merge_risk"):
            # Build PASSED — the review signal is a declared BEHAVIORAL break, not a build error.
            if bg_cited:
                glabel = str(bg.get("grade", "medium")).strip().capitalize() or "Medium"
                guid = str(bg.get("guidance", "")).strip()
                reason = (f"Declared behavioral breaking change in a used package — behavioral oracle "
                          f"graded exposure **{glabel}** (build/test/api-diff cannot confirm runtime exposure)")
                if guid:
                    reason += f"; {guid}"
            else:
                reason = ("Declared behavioral breaking change in a used package — build/test/api-diff "
                          "cannot confirm runtime exposure; see the PR comment for the graded verdict")
        else:
            _v = e.get("verdict", "")
            _ver = str(e.get("ver", "") or "")
            _verified_clean = (
                _v in ("pass", "pre_existing") or _ver.startswith(("L2", "L3", "L4"))
            ) and e.get("new_error_count", 0) == 0
            if e.get("vuln_incomplete"):
                reason = ("Build passed but the vulnerability scan was incomplete (timeout/OOM) — "
                          "re-run govulncheck to confirm no new CVEs before merging")
            elif _verified_clean:
                # The build VERIFIED clean (L2+/no new errors); it is only here because a
                # committed REVIEW verdict routed it. Do not mislabel it a build/infra failure.
                reason = (f"Verified clean ({_ver or 'build passed'}); routed to review — "
                          f"see the PR comment for the committed verdict")
            else:
                reason = "Build error / infrastructure issue"
        lines.append(f"- **PR #{e['num']}** `{e['pkg']}` {e['from']}→{e['to']} — Merge Risk: {e.get('merge_risk', 'Medium — default caution')} — {reason}")
    lines.append("")

# V8 FIX (H3/L3): CI-only PRs in their own section, not mixed with verified Go/npm PRs
if ci_only:
    _ci_sec = [e for e in ci_only if e.get("ci_tier") == "secsens"]
    _ci_maj = [e for e in ci_only if e.get("ci_tier") == "major"]
    _ci_auto = [e for e in ci_only if not e.get("ci_tier")]
    if _ci_auto:
        lines.append("## 🔧 CI-Only (Actions / Docker — no application impact)")
        lines.append("")
        lines.append("These PRs only affect CI/CD workflows. No build verification needed — zero app code impact.")
        lines.append("")
        lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
        lines.append("|----|---------|---------|----|------------|-------------|")
        for e in _ci_auto:
            cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
            lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | CI_ONLY — auto-safe{cve_badge} |")
        lines.append("")
    if _ci_maj:
        lines.append("## 🟡 Major CI Action Bumps — Changelog Glance")
        lines.append("")
        lines.append("Major version bumps of CI actions. No application code is affected, but a major bump can change inputs, runtime defaults, or output names and **break the workflow**. Skim the changelog for breaking changes before merging.")
        lines.append("")
        lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
        lines.append("|----|---------|---------|----|------------|-------------|")
        for e in _ci_maj:
            cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
            lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | 🟡 major bump — glance changelog{cve_badge} |")
        lines.append("")
    if _ci_sec:
        lines.append("## 🔐 CI Supply-Chain — Review Required (not auto-safe)")
        lines.append("")
        lines.append("These CI actions handle tokens, credentials, registry/cloud auth, code signing, or deployment/publishing. A breaking or compromised release here is a supply-chain risk, so they are **not** auto-cleared. Before merging: **pin to a full commit SHA**, and review the changelog for changed **permissions / token scopes / inputs**.")
        lines.append("")
        lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
        lines.append("|----|---------|---------|----|------------|-------------|")
        for e in _ci_sec:
            cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
            lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | ⚠️ REVIEW — supply-chain sensitive{cve_badge} |")
        lines.append("")

# Skipped (breakability:skip label)
if skipped:
    lines.append("## ⏭️ Skipped (opted out)")
    lines.append("")
    for e in skipped:
        lines.append(f"- PR #{e['num']} `{e['pkg']}` — skipped ({e.get('eco', '?')})")
    lines.append("")

# Cancelled / Incomplete
if cancelled:
    lines.append("## 🚫 Cancelled / Incomplete")
    lines.append("")
    lines.append("These PRs were discovered but not analyzed (batch timeout or cancellation).")
    lines.append("")
    for e in cancelled:
        lines.append(f"- PR #{e['num']} `{e['pkg']}` — analysis incomplete")
    lines.append("")

# Security posture
if security:
    lines.append("## 🛡️ Repository Security Posture")
    lines.append("")
    _alerts_unavail = security.get("alerts_unavailable", False)
    open_alerts = security.get("total_open_alerts", 0)
    fixable = security.get("alerts_fixable_by_merging", 0)
    if _alerts_unavail:
        lines.append("- Open Dependabot alerts: **⚠️ Unavailable** (token missing `security_events` permission — set `BREAKABILITY_PAT` repo secret)")
    else:
        lines.append(f"- Open Dependabot alerts: **{open_alerts}**")
        if fixable:
            lines.append(f"- Alerts fixable by merging these PRs: **{fixable}**")
        by_sev = security.get("severity_counts", {})
        if by_sev:
            sev_str = ", ".join(f"{s}: {c}" for s, c in sorted(by_sev.items()))
            lines.append(f"- By severity: {sev_str}")
    lines.append("")

    # V9.8 iter6 (B): precise CVE fixes with severity + advisory links
    cve_fixes = security.get("cve_fixes", [])
    if cve_fixes:
        _SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "moderate": 2, "low": 3, "unknown": 4}
        # Group by PR so one PR fixing multiple CVEs appears once
        fixes_by_pr = {}
        for f in cve_fixes:
            pr = f["pr"]
            fixes_by_pr.setdefault(pr, []).append(f)
        def _pr_sort_key(pr_num):
            sev = min(_SEV_RANK.get((f["severity"] or "").lower(), 4) for f in fixes_by_pr[pr_num])
            return (sev, pr_num)
        # Which CVEs are delivered by more than one PR (so a dev knows "merge any one").
        _prs_per_cve = {}
        for f in cve_fixes:
            cid = f.get("cve_id") or ""
            if cid:
                _prs_per_cve.setdefault(cid, set()).add(str(f["pr"]))
        lines.append("### 🛡️ Security Fixes — Merge with Priority")
        lines.append("")
        lines.append("| PR | Package | Version | CVE(s) | Severity | Fixed in | Advisory |")
        lines.append("|---|---|---|---|---|---|---|")
        for pr_num in sorted(fixes_by_pr.keys(), key=_pr_sort_key):
            flist = fixes_by_pr[pr_num]
            pkg = flist[0]["package"]
            fr = flist[0].get("from_version", ""); to = flist[0].get("to_version", "")
            via = flist[0].get("via", "primary")
            primary_pkg = flist[0].get("primary_package", "")
            # A transitive fix bumps the vulnerable package indirectly: its from-version is
            # not the PR's own (primary) from-version, so render only "→{to}" and name the
            # primary package that carried the bump instead of fabricating a range.
            if via == "transitive":
                ver_cell = f"→{to} (transitive via `{primary_pkg}`)" if primary_pkg else f"→{to} (transitive)"
            else:
                ver_cell = f"{fr}→{to}"
            cve_cell = ", ".join(sorted(set(f["cve_id"] for f in flist if f["cve_id"])))
            sev_cell = ", ".join(sorted(set(f["severity"] for f in flist if f["severity"])))
            fixed_cell = ", ".join(sorted(set(f.get("first_patched_version") or "?" for f in flist)))
            adv_cell = " ".join(f"[{f['cve_id']}](https://nvd.nist.gov/vuln/detail/{f['cve_id']})" for f in flist if (f['cve_id'] or '').startswith('CVE-'))
            if not adv_cell:
                adv_cell = "_see Dependabot_"
            lines.append(f"| #{pr_num} | `{pkg}` | {ver_cell} | {cve_cell} | {sev_cell} | {fixed_cell} | {adv_cell} |")
        lines.append("")
        _multi = {c: sorted(p, key=lambda x: int(x) if x.isdigit() else 0) for c, p in _prs_per_cve.items() if len(p) > 1}
        if _multi:
            lines.append("> ℹ️ **Some CVEs are delivered by more than one PR — merge any one to clear them:**")
            for cid in sorted(_multi):
                lines.append(f">   - `{cid}`: " + ", ".join(f"#{n}" for n in _multi[cid]))
            lines.append("")

    # V9.8 iter6 (B): orphan alerts (no PR fixes them) — needs manual attention
    orphans = security.get("orphan_alerts", [])
    if orphans:
        lines.append("### ⚠️ Orphan Alerts — No PR Fixes These")
        lines.append("")
        lines.append("_These open Dependabot alerts have **no corresponding PR** in this batch. Manual remediation required._")
        lines.append("")
        lines.append("| Package | CVE | Severity | Fixed in (upstream) |")
        lines.append("|---|---|---|---|")
        for o in orphans:
            cve_cell = f"[{o['cve_id']}](https://nvd.nist.gov/vuln/detail/{o['cve_id']})" if (o['cve_id'] or '').startswith('CVE-') else (o['cve_id'] or '-')
            lines.append(f"| `{o['package']}` | {cve_cell} | **{o['severity']}** | {o['first_patched_version']} |")
        lines.append("")

lines.append("---")
lines.append("> 🔬 *Deterministic merge plan — generated from build-results.json. Refer to individual PR comments for full details.*")

print("\n".join(lines))
