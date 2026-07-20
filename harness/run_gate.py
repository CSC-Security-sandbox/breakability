#!/usr/bin/env python3
"""Breakability acceptance gate — the deterministic fitness function for the loop.

Replaces the LLM "SCORE: X.X" vibe oracle in loop.sh. Run in seconds, no CI.

Pipeline:
  1. Load build-results.json (deterministic tool output).
  2. Derive a prediction per PR (auto_clear/review/fix) from build verdict + merge_risk.
  3. Score predictions vs corpus.json (verified labels) using breakability_eval.Scorer.
  4. INVENTED-CITATION GUARD: any PR whose verdict claims break-reachability but whose
     files_importing is empty (or points to files that don't exist) is a fabricated claim
     -> HARD FAIL. This is the #38 failure mode (claimed Error.Code reachable in a module
     that does not import lib/pq).
  5. GOLDEN GUARD (optional): if golden_predictions.json exists, any categorization drift
     for a previously-correct PR is flagged.
  6. Emit machine-parseable SCORE + GATE lines for loop.sh.

ACCEPTED iff: zero_false_green AND zero_invented_citations AND no_golden_regression.

Usage:
  python3 run_gate.py <build-results.json> <corpus.json> [--repo <root>] [--golden <file>]
Exit code 0 = ACCEPTED, 1 = REJECTED.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from breakability_eval import CorpusCase, Scorer  # noqa: E402

try:
    from verdict_contract import prediction_for_pr as _contract_prediction  # noqa: E402
except Exception:  # pragma: no cover - contract must exist, but never crash the gate
    _contract_prediction = None


def check_pipeline_completeness(results):
    """P0/P1 checks: did the pipeline actually run all stages?

    Returns list of (severity, tag, detail) tuples.
    """
    findings = []
    meta = results.get("meta", {})
    pf = meta.get("pipeline_flags", {})
    prs = results.get("prs", {})
    if isinstance(prs, list):
        prs = {str(p.get("pr_id") or p.get("number")): p for p in prs}

    if pf:
        if pf.get("skip_agent_requested"):
            if pf.get("ai_comments_generated"):
                findings.append(("P1", "AI_SKIPPED",
                                 "skip_agent=true — deterministic fallback comments generated"))
            else:
                findings.append(("P0", "AI_SKIPPED",
                                 "skip_agent=true — AI layer explicitly disabled, all comments are thin templates"))
        elif not pf.get("ai_comments_generated"):
            findings.append(("P0", "AI_FAILED",
                             "AI agent ran but produced no comments — 1,257-line prompt unused"))
        if pf.get("template_fallback_used") and not pf.get("skip_agent_requested"):
            findings.append(("P0", "TEMPLATE_FALLBACK",
                             "template-fallback active despite AI not being skipped — AI layer crashed"))
        if not pf.get("ai_agent_installed") and not pf.get("skip_agent_requested"):
            findings.append(("P1", "AI_NOT_INSTALLED",
                             "AI agent CLI was not installed"))
    else:
        fallback_detected = False
        for pid, pr in prs.items():
            comment_footer = str(pr.get("comment_footer", ""))
            comment_model = str(pr.get("comment_model", ""))
            if "template-fallback" in comment_footer or "template-fallback" in comment_model:
                fallback_detected = True
                break
        if fallback_detected:
            findings.append(("P0", "TEMPLATE_FALLBACK_DETECTED",
                             "meta.pipeline_flags missing AND template-fallback detected in PR comments"))
        else:
            all_empty_det = all(
                not (pr.get("deterministic") or {}).get("changelogSignal")
                for pr in prs.values()
            ) if prs else False
            if all_empty_det:
                findings.append(("P0", "AI_LAYER_SUSPECT",
                                 "meta.pipeline_flags missing, all PRs have empty deterministic/changelogSignal — "
                                 "AI layer likely did not run (add pipeline_flags to workflow)"))

    no_changelog = 0
    for pid, pr in prs.items():
        cs = (pr.get("deterministic") or {}).get("changelogSignal")
        if cs is None or cs == "" or (isinstance(cs, dict) and cs.get("status") == "missing"):
            no_changelog += 1
    if no_changelog > 0:
        pct = no_changelog / max(len(prs), 1) * 100
        if pct > 50:
            findings.append(("P1", "CHANGELOG_MISSING",
                             f"changelog data missing on {no_changelog}/{len(prs)} PRs ({pct:.0f}%)"))

    return findings


def check_security_coverage(results):
    """P1 checks: is the security analysis actually functional?

    Returns list of (severity, tag, detail) tuples.
    """
    findings = []
    sp = results.get("security_posture", {})
    prs = results.get("prs", {})
    if isinstance(prs, list):
        prs = {str(p.get("pr_id") or p.get("number")): p for p in prs}

    if sp.get("alerts_unavailable"):
        findings.append(("P1", "ALERTS_BLIND",
                         "alerts_unavailable=true — tool cannot correlate Dependabot alerts with PRs"))

    unknown_vuln = 0
    for pid, pr in prs.items():
        if pr.get("vuln_status") == "unknown":
            unknown_vuln += 1
    if unknown_vuln > 0 and unknown_vuln == len(prs):
        findings.append(("P1", "VULN_ALL_UNKNOWN",
                         f"vuln_status=unknown on all {unknown_vuln} PRs — govulncheck not running"))

    if sp.get("total_open_alerts", 0) > 0 and not sp.get("prs_fixing_alerts"):
        findings.append(("P1", "ALERTS_NO_CORRELATION",
                         f"{sp['total_open_alerts']} open alerts but no PR correlation in prs_fixing_alerts"))

    return findings


def check_build_misattribution(results):
    """P1 check: detect build failures caused by pre-existing/environmental errors.

    If 3+ PRs from different packages share byte-identical build output_tail,
    those failures are almost certainly a pre-existing break in the build sandbox
    (e.g. a codegen issue in vcm-proxy) rather than real dependency-bump breakage.

    Returns list of (severity, tag, detail) tuples.
    """
    findings = []
    prs = results.get("prs", {})
    if isinstance(prs, list):
        prs = {str(p.get("pr_id") or p.get("number")): p for p in prs}

    # Collect PRs whose build verdict is "fail" and that have output_tail content
    fail_groups = {}  # output_tail -> list of (pr_id, package)
    for pid, pr in prs.items():
        build = pr.get("build") or {}
        if build.get("verdict") != "fail":
            continue
        tail = build.get("output_tail")
        if not tail or not tail.strip():
            continue
        pkg = pr.get("package", "unknown")
        fail_groups.setdefault(tail, []).append((pid, pkg))

    for tail, group in fail_groups.items():
        if len(group) < 3:
            continue
        # Check that the PRs come from different packages (not one package repeated)
        packages = set(pkg for _, pkg in group)
        if len(packages) < 2:
            continue
        pr_ids = sorted(group, key=lambda x: int(x[0]) if x[0].isdigit() else x[0])
        pr_list = ",".join(pid for pid, _ in pr_ids)
        # Extract a short error signature from the tail for the message
        lines = tail.strip().splitlines()
        # Pick the first non-empty error-like line for context
        sig = ""
        for line in lines:
            stripped = line.strip()
            if stripped and ("undefined" in stripped or "error" in stripped.lower()
                            or "cannot" in stripped.lower() or "failed" in stripped.lower()):
                sig = stripped[:120]
                break
        if not sig and lines:
            sig = lines[-1].strip()[:120]
        detail = (f"PRs {pr_list} share identical build error — "
                  f"likely pre-existing, not caused by their dependency bumps")
        if sig:
            detail += f" ({sig})"
        findings.append(("P1", "BUILD_MISATTRIBUTED", detail))

    return findings


def check_merge_risk_uniformity(results):
    """P1 check: flag when merge_risk is suspiciously uniform across PRs.

    If >50% of PRs share a byte-identical merge_risk object, the risk
    computation is likely broken (static template instead of computed).
    """
    findings = []
    prs = results.get("prs", {})
    if isinstance(prs, list):
        prs = {str(p.get("pr_id") or p.get("number")): p for p in prs}
    if len(prs) < 3:
        return findings

    import json as _json
    risk_groups = {}
    for pid, pr in prs.items():
        mr = pr.get("merge_risk") or {}
        key = _json.dumps(mr, sort_keys=True)
        risk_groups.setdefault(key, []).append(pid)

    for key, group in risk_groups.items():
        pct = len(group) / len(prs) * 100
        if pct > 50 and len(group) > 2:
            mr_obj = _json.loads(key)
            tag = mr_obj.get("tag", "?")
            findings.append(("P1", "MERGE_RISK_UNIFORM",
                             f"{len(group)}/{len(prs)} PRs ({pct:.0f}%) share identical "
                             f"merge_risk (tag={tag}) — risk computation may be broken"))
    return findings


def _legacy_prediction(pr):
    """Original derivation from build.verdict + merge_risk.tag (pre-typed-policy artifacts)."""
    build = pr.get("build") or {}
    if build.get("verdict") == "fail":
        return "fix"
    tag = ((pr.get("merge_risk") or {}).get("tag") or "").lower()
    if tag in ("low", "none", ""):
        return "auto_clear"
    return "review"  # medium / high -> needs a look


def derive_prediction(pr):
    """Map tool output -> {auto_clear|review|fix}, mirroring SPEC buckets.

    The gate MUST grade the same verdict the developer sees, otherwise a renderer/policy
    regression (e.g. the #121->#128 GLANCE->REVIEW review-wall) moves the rendered output
    but not the gate number and slips through.

    Always prefer the contract prediction (verdict_contract.prediction_for_pr) — it is the
    single authoritative source that accounts for misattribution, CVE floor, probe escalation,
    and all other verdict rules. The legacy derivation only fires when the contract module
    is unavailable (should never happen in practice).
    """
    if _contract_prediction is not None:
        return _contract_prediction(pr)
    return _legacy_prediction(pr)


def claims_reachability(pr):
    reason = ((pr.get("merge_risk") or {}).get("reason") or "").lower()
    return ("break-reachable" in reason or "reached in" in reason
            or "reachable api" in reason)


def overclaims_function_reach(pr):
    """#38 class, generalized: the verdict TEXT asserts symbol/function-level reachability,
    but the structured reachability evidence is absent or only import-level + unconfirmed.
    Import-level reachability proves the package is imported, NOT that the changed symbol is
    called. Asserting 'BREAK-reachable <symbol>' off import evidence is an over-claim. The cheap
    remedy (NOT a callgraph) is a symbol-usage proof: grep the importing files for the changed
    symbol token at a real call site, or a probe diff. Until that exists, it must stay REVIEW."""
    if not claims_reachability(pr):
        return False, ""
    # A failing build already PROVES the break by compilation — the reachability text is
    # corroborated, not an over-claim. Over-claim only applies when build/test PASS but the
    # verdict still asserts symbol-level reachability on weak evidence.
    if (pr.get("build") or {}).get("verdict") == "fail":
        return False, ""
    dbr = pr.get("declared_break_reachability")
    if not dbr:  # text claims reachability, zero structured evidence (PR#38)
        return True, "verdict asserts symbol reachability with no declared_break_reachability evidence"
    if dbr.get("reachability_kind") == "import" and not dbr.get("behavior_confirmed"):
        return True, ("verdict asserts symbol/function reachability but evidence is import-level "
                      "+ behavior_confirmed=false (needs symbol-usage proof or probe diff)")
    return False, ""


def invented_citation(pr, repo_root):
    """True if the PR claims reachability but has no real importing file to back it."""
    if not claims_reachability(pr):
        return False, ""
    importers = pr.get("files_importing") or []
    if not importers:
        return True, "claims break-reachability but files_importing is empty"
    pkg_dir = pr.get("pkg_dir") or ""
    missing = [f for f in importers
               if not os.path.exists(os.path.join(repo_root, pkg_dir, f.lstrip("./")))
               and not os.path.exists(os.path.join(repo_root, f.lstrip("./")))]
    if missing:
        return True, f"cites importing files that do not exist: {missing}"
    return False, ""


def _cite_references_pkg(citation, repo_root, package):
    """Stronger than existence: the cited file must be a SOURCE call site that actually
    references the package/symbol. A real-but-irrelevant citation (or the dependency manifest,
    which merely lists the package) is the subtle failure existence-checks miss."""
    path = citation.split(":")[0].lstrip("./")
    base = os.path.basename(path)
    # manifests merely DECLARE the dependency; they are never proof of a call site
    if base in {"go.mod", "go.sum", "package.json", "package-lock.json",
                "yarn.lock", "requirements.txt", "Pipfile", "Pipfile.lock", "go.work"}:
        return False
    if not package:
        return True  # nothing to anchor against; existence check already passed
    full = os.path.join(repo_root, path)
    try:
        text = open(full, encoding="utf-8", errors="ignore").read()
    except OSError:
        return False
    # last path segment of the module (e.g. lib/pq -> pq, otel/sdk -> sdk) + bare name
    tokens = {package, package.rsplit("/", 1)[-1], package.split(".")[0].rsplit("/", 1)[-1]}
    return any(t and t in text for t in tokens)


def _normalize_ai_verdict(v):
    """Accept BOTH the legacy {reachable, recommendation, citation} shape and the
    normalized adjudicator shape {final_verdict, break_class, citation, proof} emitted
    by independent_adjudicate.sh -> validate_adjudication.py. Returns a dict in the
    legacy shape so the single falsifiability contract below applies to both.

    The adjudicator's `safe` is only ever produced after validate_adjudication.py has
    already enforced a real citation + shown proof, so mapping safe -> reachable=False
    is faithful (the AI asserted the break does not reach us)."""
    if "final_verdict" in v and "recommendation" not in v:
        fv = v.get("final_verdict")
        rec = "safe" if fv == "safe" else "review"  # needs_change/escalate stay REVIEW
        return {
            "reachable": False if fv == "safe" else True,
            "recommendation": rec,
            "citation": v.get("citation", ""),
            "proof": v.get("proof", ""),
        }
    return v


def _validate_ai(v, repo_root, package=None):
    """Falsifiability contract: reject invented citations, reject FIX/CVE attempts, require a
    citation that actually references the symbol for any downgrade-to-safe."""
    v = _normalize_ai_verdict(v)
    need = {"reachable", "recommendation", "citation"}
    if not need <= set(v):
        return False, f"missing keys {sorted(need - set(v))}"
    if v["recommendation"] not in ("safe", "review"):
        return False, "AI can only say safe|review (never fix/clear-CVE)"
    cite = (v.get("citation") or "").strip()
    if cite:
        path = cite.split(":")[0].lstrip("./")
        if not os.path.exists(os.path.join(repo_root, path)):
            return False, f"INVENTED CITATION {cite}"
        if not _cite_references_pkg(cite, repo_root, package):
            return False, f"IRRELEVANT CITATION {cite} does not reference {package}"
    if v["recommendation"] == "safe" and not (v["reachable"] is False and cite):
        return False, "downgrade to safe needs reachable=false WITH real citation"
    return True, ""


def main():
    if len(sys.argv) < 3:
        print("Usage: run_gate.py <build-results.json> <corpus.json> [--repo R] [--golden G]")
        return 2
    results_path, corpus_path = sys.argv[1], sys.argv[2]
    repo_root = "."
    golden_path = None
    ai_path = None
    args = sys.argv[3:]
    for i, a in enumerate(args):
        if a == "--repo" and i + 1 < len(args):
            repo_root = args[i + 1]
        if a == "--golden" and i + 1 < len(args):
            golden_path = args[i + 1]
        if a == "--ai" and i + 1 < len(args):
            ai_path = args[i + 1]

    results = json.load(open(results_path))
    corpus = json.load(open(corpus_path))
    prs = results.get("prs", {})
    if isinstance(prs, list):
        prs = {str(p.get("pr_id") or p.get("number")): p for p in prs}

    cases = [CorpusCase(c) for c in corpus["cases"]]

    # 1. deterministic predictions for corpus PRs (AI-off baseline)
    predictions = {}
    skipped_mismatch = []
    for c in cases:
        pid = str(c.pr_id)
        if pid not in prs:
            continue  # Scorer defaults to abstain (counts as false_none for review/fix)
        pr_pkg = prs[pid].get("package", "")
        if c.package and pr_pkg and c.package != pr_pkg:
            skipped_mismatch.append((pid, c.package, pr_pkg))
            continue  # PR number reused for a different package — skip
        predictions[pid] = derive_prediction(prs[pid])

    base_res = Scorer(cases).score(dict(predictions))
    base_ac = base_res["metrics"]["auto_clear_pct"]
    base_fb = base_res["errors"]["false_block_count"]

    # 1b. apply VALIDATED AI verdicts on top (the differentiator, measurable).
    #     AI may only downgrade REVIEW->auto_clear with a real citation; never touch FIX/CVE.
    ai_applied, ai_proof_added, ai_rejected = [], [], []
    if ai_path and os.path.exists(ai_path):
        ai = json.load(open(ai_path))
        for pid, v in ai.items():
            if predictions.get(pid) != "review":
                continue  # AI only adjudicates the REVIEW bucket
            ok, why = _validate_ai(v, repo_root, (prs.get(pid) or {}).get("package"))
            if not ok:
                ai_rejected.append((pid, why))
                continue
            nv = _normalize_ai_verdict(v)
            if nv.get("recommendation") == "safe":
                predictions[pid] = "auto_clear"
                ai_applied.append((pid, nv.get("citation", "")))
            else:  # review, but now PROOF-backed (citation) instead of generic caution
                ai_proof_added.append((pid, nv.get("citation", "")))

    score_res = Scorer(cases).score(predictions)

    # 2. invented-citation guard (over ALL prs, not just corpus)
    invented = []
    for pid, pr in prs.items():
        bad, why = invented_citation(pr, repo_root)
        if bad:
            invented.append((pid, pr.get("package", "?"), why))

    # 2b. over-claim guard: verdict asserts function-level reachability off import-only/absent
    #     evidence. Forces escalation to deep.go callgraph or a probe before asserting.
    overclaims = []
    for pid, pr in prs.items():
        bad, why = overclaims_function_reach(pr)
        if bad:
            overclaims.append((pid, pr.get("package", "?"), why))

    # 3. golden regression (optional)
    golden_regressions = []
    if golden_path and os.path.exists(golden_path):
        golden = json.load(open(golden_path))
        for pid, want in golden.items():
            got = predictions.get(pid)
            if got and got != want:
                golden_regressions.append((pid, want, got))

    # 4. pipeline completeness checks (AI layer, changelog)
    pipeline_findings = check_pipeline_completeness(results)

    # 5. security coverage checks (alerts, vuln status)
    security_findings = check_security_coverage(results)

    # 6. build misattribution (cross-PR error deduplication)
    misattribution_findings = check_build_misattribution(results)

    # 7. merge_risk uniformity (detects static template bug)
    uniformity_findings = check_merge_risk_uniformity(results)

    fg = score_res["errors"]["false_green_count"]
    fb = score_res["errors"]["false_block_count"]
    fn = score_res["errors"]["false_none_count"]
    ac = score_res["metrics"]["auto_clear_pct"]

    zero_fg = fg == 0
    zero_invented = len(invented) == 0
    no_golden_reg = len(golden_regressions) == 0
    zero_overclaim = len(overclaims) == 0

    has_p0_pipeline = any(s == "P0" for s, _, _ in pipeline_findings)
    has_p0_security = any(s == "P0" for s, _, _ in security_findings)

    accepted = (zero_fg and zero_invented and no_golden_reg
                and zero_overclaim and not has_p0_pipeline and not has_p0_security)

    # composite 0-10 score: start 10, subtract heavy for hard fails, light for noise
    score = 10.0
    score -= fg * 4.0            # false-green is catastrophic
    score -= len(invented) * 3.0  # fabricated evidence destroys trust
    score -= len(overclaims) * 2.0  # unproven function-reachability assertion
    score -= len(golden_regressions) * 2.0
    score -= fb * 1.0            # over-flagging (noise)
    score -= fn * 1.0
    for sev, tag, _ in pipeline_findings:
        score -= 3.0 if sev == "P0" else 1.5
    for sev, tag, _ in security_findings:
        score -= 2.0 if sev == "P0" else 1.0
    for sev, tag, _ in misattribution_findings:
        score -= 1.0  # P1: pre-existing build failures inflating false breakage
    for sev, tag, _ in uniformity_findings:
        score -= 1.5  # P1: merge_risk computation broken
    score = max(0.0, round(score, 1))

    print(f"SCORE: {score}")
    print(f"ACCEPTED: {accepted}")
    print(f"FALSE_GREEN: {fg}")
    print(f"FALSE_BLOCK: {fb}")
    print(f"FALSE_NONE: {fn}")
    print(f"INVENTED_CITATIONS: {len(invented)}")
    print(f"OVERCLAIMS: {len(overclaims)}")
    print(f"GOLDEN_REGRESSIONS: {len(golden_regressions)}")
    print(f"PIPELINE_ISSUES: {len(pipeline_findings)}")
    print(f"SECURITY_ISSUES: {len(security_findings)}")
    print(f"BUILD_MISATTRIBUTIONS: {len(misattribution_findings)}")
    print(f"MERGE_RISK_UNIFORMITY: {len(uniformity_findings)}")
    print(f"AUTO_CLEAR_PCT: {ac:.1f}")
    if ai_path:
        print(f"AI_OFF_AUTO_CLEAR_PCT: {base_ac:.1f}")
        print(f"AI_ON_AUTO_CLEAR_PCT: {ac:.1f}")
        print(f"AI_OFF_FALSE_BLOCK: {base_fb}")
        print(f"AI_ON_FALSE_BLOCK: {fb}")
        print(f"AI_DOWNGRADES_APPLIED: {len(ai_applied)}")
        print(f"AI_PROOF_ADDED: {len(ai_proof_added)}")
        print(f"AI_REJECTED: {len(ai_rejected)}")
    if skipped_mismatch:
        print(f"CORPUS_MISMATCHED: {len(skipped_mismatch)}")
        for pid, corpus_pkg, data_pkg in skipped_mismatch:
            print(f"  PR#{pid}: corpus={corpus_pkg} data={data_pkg} (skipped)")

    print("FINDINGS:")
    sev_map = {"false_green": "P0", "false_none": "P1", "false_block": "P2"}
    for c in score_res["per_case"]:
        if c["error"]:
            p = sev_map.get(c["error"], "P2")
            print(f"- [{p}] PR#{c['pr_id']} | {c['error']} | expected={c['expected']} predicted={c['predicted']}")
    for pid, pkg, why in invented:
        print(f"- [P0] PR#{pid} {pkg} | INVENTED CITATION | {why}")
    for pid, pkg, why in overclaims:
        print(f"- [P1] PR#{pid} {pkg} | OVERCLAIM | {why}")
    for pid, want, got in golden_regressions:
        print(f"- [P1] PR#{pid} | GOLDEN REGRESSION want={want} got={got}")
    for sev, tag, detail in pipeline_findings:
        print(f"- [{sev}] PIPELINE | {tag} | {detail}")
    for sev, tag, detail in security_findings:
        print(f"- [{sev}] SECURITY | {tag} | {detail}")
    for sev, tag, detail in misattribution_findings:
        print(f"- [{sev}] BUILD | {tag} | {detail}")
    for sev, tag, detail in uniformity_findings:
        print(f"- [{sev}] QUALITY | {tag} | {detail}")
    print("END_FINDINGS")

    json.dump({"score": score, "accepted": accepted, "metrics": score_res["metrics"],
               "errors": score_res["errors"], "invented": invented, "overclaims": overclaims,
               "golden_regressions": golden_regressions, "predictions": predictions,
               "pipeline_findings": [{"sev": s, "tag": t, "detail": d} for s, t, d in pipeline_findings],
               "security_findings": [{"sev": s, "tag": t, "detail": d} for s, t, d in security_findings],
               "misattribution_findings": [{"sev": s, "tag": t, "detail": d} for s, t, d in misattribution_findings],
               "uniformity_findings": [{"sev": s, "tag": t, "detail": d} for s, t, d in uniformity_findings]},
              open("gate-result.json", "w"), indent=2)
    return 0 if accepted else 1


def _err_of(score_res, pid):
    for c in score_res["per_case"]:
        if c["pr_id"] == pid:
            return c["error"]
    return None


if __name__ == "__main__":
    sys.exit(main())
