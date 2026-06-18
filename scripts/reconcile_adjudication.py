#!/usr/bin/env python3
"""Reconcile deterministic build-check output with INDEPENDENT AI adjudication.

PRD intent: the AI layer does its own analysis; it is NOT a formatter that echoes the
deterministic verdict. This module is the decision/reconcile layer that lets independent
evidence (deterministic module-scope + AI investigation) override a deterministic
false positive, while staying fail-safe (never invents a merge-block, never clears a CVE).

Two tiers, both call-graph-free:

  Tier 0 (deterministic, ~microseconds, no AI):
    If the bumped dependency is NOT imported anywhere in the BUMPED MODULE, then a
    breaking API change in that dependency cannot reach this module. This clears the
    classic cross-module false positive (e.g. PR #38: lib/pq bumped in
    automations/tstctl but the only Error.Code usages live in the ROOT module's
    database/* — a different go.mod). Verdict -> SAFE, cited by the module manifest.

  Tier 1 (AI independent adjudication, residue only):
    For PRs where the dependency IS imported in the bumped module, consult the AI
    verdict (replayed from a verdicts file or produced live per-PR). Reconcile:
      - AI reachable=true  -> keep REVIEW, attach the AI's call-site citation as proof.
      - AI reachable=false WITH a real in-module citation AND no declared breaking-change
        section -> may downgrade REVIEW -> SAFE (the AI proved the specific call is
        unaffected).  Otherwise HOLD at REVIEW (conservative; a probe / human decides).
      - AI uncertain / missing / unvalidated -> keep the deterministic verdict.

Guardrails (always): the AI/reconcile layer can only move REVIEW<->SAFE. It can NEVER
produce FIX, never override a build:fail, and never clear a CVE.

Usage:
  reconcile_adjudication.py <build-results.json> [--verdicts ai_verdicts.json]
                            [--repo .] [--write] [--harness DIR]
Prints a per-PR reconcile summary; with --write, updates verdict_v2 in build-results.json.
"""
import argparse
import json
import os
import sys


def _module_dir(pr):
    d = (pr.get("pkg_dir") or "/").strip()
    return "" if d in ("/", ".", "") else d.strip("/")


def _in_module(fp, mod):
    f = (fp or "").lstrip("./")
    if mod == "":
        # root module = everything NOT under a nested module dir
        return not (f.startswith("cicd/") or f.startswith("automations/"))
    return f.startswith(mod + "/") or f == mod


def _files_importing_in_module(pr, mod):
    out = []
    for f in pr.get("files_importing") or []:
        path = f if isinstance(f, str) else (f.get("file") or "")
        if _in_module(path, mod):
            out.append(path)
    return out


def _changed_symbol_usages_in_module(pr, mod):
    det = pr.get("deterministic") or {}
    return [u for u in (det.get("usages") or []) if _in_module(u.get("file", ""), mod)]


def _dep_imported_in_module(pr, mod):
    return bool(_files_importing_in_module(pr, mod)) or bool(_changed_symbol_usages_in_module(pr, mod))


def _usage_scan_conclusive(pr):
    """The Tier-0 module-scope clear ("dep not imported in the bumped module -> SAFE")
    trusts the dependency-usage scan. For Go that scan is integral and reliable, so an
    empty result is proof of non-use. For npm it can UNDER-report — the import specifier
    often differs from the package name (e.g. a PR bumping ``react-router`` whose code
    imports ``react-router-dom``), private-registry workspaces may be unbuilt, and
    multi-package bumps confuse the matcher — so an empty result is absence of evidence,
    NOT evidence of absence. Require positive proof the scanner saw this dependency
    somewhere in the repo before trusting an in-module absence; otherwise the scan is
    inconclusive and the PR must stay REVIEW (never a false green)."""
    if (pr.get("ecosystem") or "").lower() != "npm":
        return True
    return bool(pr.get("files_importing"))


def _has_declared_breaking_section(pr):
    det = pr.get("deterministic") or {}
    sig = det.get("changelogSignal")
    blob = ""
    if isinstance(sig, str):
        blob += sig
    elif isinstance(sig, dict):
        blob += json.dumps(sig)
    blob += " " + (det.get("changelogText") or "")
    low = blob.lower()
    return ("breaking change" in low) or ("### breaking" in low) or ("deprecat" in low)


def _policy_decision(pr):
    pol = pr.get("policy_lowering") or {}
    dec = pol.get("decision") if isinstance(pol, dict) else None
    return dec if isinstance(dec, dict) else {}


def _semver_tuple(v):
    """Best-effort (major, minor) from a version string; None if unparseable."""
    if not v:
        return None
    s = str(v).lstrip("vV").split("+")[0].split("-")[0]
    parts = s.split(".")
    try:
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except (ValueError, IndexError):
        return None


def _tests_executed(pr):
    """True only when the candidate version's test suite actually RAN (not skipped/absent)."""
    t = pr.get("test") or {}
    if not isinstance(t, dict):
        return False
    return bool(t.get("ran")) and (t.get("exit") in (0, None) or t.get("main_test_exit") == 0)


def _declared_breaking_unverified_clear(pr):
    """Guard: when the dependency's OWN changelog/release notes declare a breaking change (or
    deprecation), a name-grep / reachability-only AI clear is insufficient — the declared break
    may manifest behaviorally (changed semantics under an unchanged signature) or in code paths
    a static grep misjudges (e.g. generated code, reflection, build-time wiring). Such a PR must
    NOT be cleared on reachability alone; it requires execution evidence (an actually-run test
    suite or a behavioral probe). Otherwise it stays REVIEW.

    Returns (True, human_reason) when the clear is insufficient and must be blocked."""
    if not _has_declared_breaking_section(pr):
        return (False, "")
    if _tests_executed(pr):
        return (False, "")  # execution evidence backs the clear
    return (True, (f"Dependency `{pr.get('package')}` declares a BREAKING change in its release "
                   f"notes for {pr.get('from')}->{pr.get('to')}, and the SAFE basis is "
                   f"reachability/grep only with no executed test suite or behavioral probe. "
                   f"A declared break can manifest behaviorally or via code paths grep misjudges; "
                   f"holding REVIEW (run the suite or a behavioral probe to clear)."))


_STRUCTURAL_PROBE_TOKENS = (
    "package.main", "package.module", "package.type", "package.exports",
    "removed_package_exports", "changed_package_exports",
    "load.require", "load.import",
)


def _probe_structural_break_unverified_clear(pr):
    """Guard: the deterministic npm runtime-shape probe found a STRUCTURAL module-system
    break — the package changed its entrypoint (`package.main`), module type/format
    (`package.module`/`package.type`), its `exports` map, or its require()/import()
    loadability (e.g. it went ESM-only). Such a break affects EVERY consumer regardless
    of which symbols it imports (a CommonJS consumer cannot require() an ESM-only package),
    so an AI reachability/grep clear ("the symbol I use is unchanged / not imported")
    cannot refute it. Only execution evidence (an actually-run test suite against the new
    version) may clear it. Otherwise hold REVIEW.

    Symbol-level-only probe diffs (changed_exports/removed_exports of individual members)
    are intentionally NOT covered here — those remain AI-resolvable via reachability.

    Returns (True, human_reason) when the clear is insufficient and must be blocked."""
    bg = pr.get("behavioral_grade") or {}
    if not isinstance(bg, dict):
        return (False, "")
    if bg.get("probe_kind") != "npm_runtime_shape":
        return (False, "")
    if bg.get("same_behavior") is not False:
        return (False, "")  # probe found same behavior, or was unavailable -> nothing to protect
    ev = str(bg.get("evidence") or "")
    if not any(tok in ev for tok in _STRUCTURAL_PROBE_TOKENS):
        return (False, "")  # only symbol-level diffs -> AI reachability may legitimately resolve
    if _tests_executed(pr):
        return (False, "")  # execution evidence backs the clear
    hit = ", ".join(t for t in _STRUCTURAL_PROBE_TOKENS if t in ev)
    return (True, (f"The deterministic runtime-shape probe found a STRUCTURAL module-system "
                   f"break in `{pr.get('package')}` {pr.get('from')}->{pr.get('to')} ({hit}) — "
                   f"e.g. the package changed its entrypoint/module type/exports map or its "
                   f"require()/import() loadability (it may have gone ESM-only). This breaks "
                   f"consumers regardless of which symbols they import, so a reachability/grep "
                   f"clear cannot refute it; holding REVIEW (a passing test suite executed "
                   f"against the new version is required to clear)."))


def _pkg_name(pr):
    p = pr.get("package")
    if isinstance(p, list):
        p = p[0] if p else ""
    return (p or "").strip().lower()


def _is_compat_promise_module(pr):
    """Go project sub-repositories (golang.org/x/...) version as 0.x by convention but follow
    the Go 1 compatibility promise — minor releases do not break API or behavior. The generic
    pre-1.0 floor does not apply to them."""
    pkg = _pkg_name(pr)
    return pkg == "golang.org/x" or pkg.startswith("golang.org/x/")


def _pre1_unverified_reachability_clear(pr):
    """Guard: a pre-1.0 (0.x) dependency carries NO semver minor-stability guarantee, so a
    multi-version 0.x jump can ship breaking BEHAVIORAL changes that name-grep of the apidiff
    symbols cannot rule out. If the only basis for an AI SAFE downgrade is reachability/grep
    (no probe, no executed test suite), such a jump must NOT be cleared -- it stays REVIEW.

    Returns (True, human_reason) when the clear is insufficient and must be blocked."""
    if _is_compat_promise_module(pr):
        return (False, "")  # golang.org/x/*: Go 1 compatibility promise; minor bumps are safe
    frm = _semver_tuple(pr.get("from"))
    to = _semver_tuple(pr.get("to"))
    if not frm or not to:
        return (False, "")
    if frm[0] != 0:
        return (False, "")  # >=1.0 dep: semver protects minors; reachability clear is sound
    # pre-1.0: any change above the patch level (minor or major component moved) is risky
    risky_jump = (frm[0], frm[1]) != (to[0], to[1])
    if not risky_jump:
        return (False, "")  # 0.x patch bump -> low risk, allow clear
    if _tests_executed(pr):
        return (False, "")  # execution evidence backs the clear
    return (True, (f"pre-1.0 dependency `{pr.get('package')}` jumps {pr.get('from')}->{pr.get('to')} "
                   f"(no semver minor-stability guarantee) and the SAFE basis is reachability/grep "
                   f"only with no executed test suite; name-grep cannot rule out a behavioral change. "
                   f"Holding REVIEW (run the suite or a behavioral probe to clear)."))


def _current_verdict(pr):
    # verdict_v2 is materialized late (by the comment poster's policy overlay). At reconcile
    # time the authoritative verdict lives in policy_lowering.decision; fall back to it.
    v2 = pr.get("verdict_v2") or {}
    v = (v2.get("verdict") or "").upper()
    if v:
        return v
    return (_policy_decision(pr).get("verdict") or "").upper()


def _reason_code(pr):
    v2 = pr.get("verdict_v2") or {}
    r = (v2.get("residual") or {}).get("check") or v2.get("reason") or ""
    if r:
        return r
    return _policy_decision(pr).get("reason_code") or ""


def _apply_safe(pr, reason, evidence, citation, source):
    """Downgrade this PR's verdict to SAFE. Never touches CVE/security state.

    The ai_adjudication marker makes the comment poster's legacy policy overlay skip this PR,
    so verdict_v2 must be fully self-sufficient (verdict/confidence/priority/severity) for the
    renderer. We also flip policy_lowering.decision so any downstream consumer agrees."""
    v2 = pr.setdefault("verdict_v2", {})
    v2["verdict"] = "SAFE"
    v2["confidence"] = "L4"
    v2["priority"] = "P3"
    v2["severity"] = "low"
    es = v2.setdefault("evidenceState", {})
    es["api_diff"] = "NONE"
    es["usage"] = "NONE"
    v2["residual"] = {"summary": evidence, "check": reason}
    v2["reason"] = evidence
    dec = _policy_decision(pr)
    if dec:
        dec["verdict"] = "SAFE"
        dec["reason_code"] = reason
        dec["severity"] = "low"
        dec["display_reason"] = evidence
    pr["ai_adjudication"] = {
        "applied": "downgrade_to_safe",
        "source": source,
        "reason_code": reason,
        "evidence": evidence,
        "citation": citation,
    }


def _apply_needs_change(pr, evidence, citation, remediation, flaw, source):
    """Keep REVIEW but attach the resolved finding + remediation (the work is done; advisory,
    never a hard merge block — the AI may not FIX-gate)."""
    v2 = pr.setdefault("verdict_v2", {})
    v2["verdict"] = "REVIEW"
    v2.setdefault("confidence", "L3")
    v2.setdefault("priority", "P2")
    v2.setdefault("severity", "medium")
    v2["residual"] = {"summary": evidence, "check": "review:ai-needs-change"}
    v2["reason"] = evidence
    pr["ai_adjudication"] = {
        "applied": "needs_change",
        "source": source,
        "evidence": evidence,
        "citation": citation,
        "remediation": remediation,
        "deterministic_flaw": flaw,
    }


def _record_review(pr, evidence, citation, source, flaw="", question=""):
    pr["ai_adjudication"] = {
        "applied": "hold_review",
        "source": source,
        "evidence": evidence,
        "citation": citation,
        "deterministic_flaw": flaw,
        "escalation_question": question,
    }


def reconcile_pr(pr, verdict, repo):
    """Return (action, detail). Mutates pr's verdict_v2 when it downgrades to safe."""
    mod = _module_dir(pr)
    verdict_now = _current_verdict(pr)
    reason = _reason_code(pr)

    # Only ever reconsider REVIEW verdicts driven by a breakability signal the AI can resolve
    # by reading the consumer code: a (possibly cross-module) reachable API break, concrete
    # apidiff changes, or a declared breaking changelog. NEVER touch FIX (build/security),
    # already-SAFE, or a security/CVE hold (clearing a CVE is out of scope here — that is the
    # safety floor that guarantees we never turn a vulnerability into a false green).
    es = (pr.get("verdict_v2", {}).get("evidenceState", {}) or {})
    det = pr.get("deterministic") or {}
    is_break_reachable = "break-reachable" in reason or es.get("api_diff") == "POSITIVE"
    has_api_changes = len(det.get("api_changes_detail") or []) > 0
    is_declared_breaking = "declared-breaking" in reason
    is_security_hold = "security" in reason or "cve" in reason or "vuln" in reason
    adjudicable = (is_break_reachable or has_api_changes or is_declared_breaking) and not is_security_hold
    if verdict_now != "REVIEW" or not adjudicable:
        return ("kept", f"verdict={verdict_now or 'n/a'} (not an adjudicable breakability review; untouched)")

    # ── Tier 0: deterministic module-scope (no AI, no call graph) ──────────────
    if not _dep_imported_in_module(pr, mod) and _usage_scan_conclusive(pr):
        is_npm = (pr.get("ecosystem") or "").lower() == "npm"
        manifest_name = "package.json" if is_npm else "go.mod"
        manifest = (mod + "/" + manifest_name) if mod else manifest_name
        if not os.path.exists(os.path.join(repo, manifest)):
            manifest = ""  # still safe; manifest path just unavailable for citation
        ev = (f"Dependency `{pr.get('package')}` is not imported in the bumped module "
              f"`{mod or 'root'}`; a breaking API change in it cannot reach this module. "
              f"The flagged usages, if any, live in a different module.")
        _apply_safe(pr, "safe:not-imported-in-bumped-module", ev, manifest, "deterministic_module_scope")
        return ("downgraded_safe", f"Tier0: dep not imported in '{mod or 'root'}' -> SAFE")

    # ── Tier 1: AI as senior arbiter (independent-first + deterministic-audit) ──
    if not verdict or not verdict.get("accepted", True):
        return ("kept", "dep imported in module; no accepted AI verdict -> keep REVIEW")

    # Normalize across schema versions: prefer the arbitrated final_verdict.
    final = verdict.get("final_verdict") or verdict.get("verdict")
    if final is None and "reachable" in verdict:  # legacy
        final = "safe" if verdict.get("recommendation") == "safe" else "review"
    cite = (verdict.get("citation") or "").strip()
    ev = (verdict.get("proof") or verdict.get("evidence") or "").strip()
    flaw = (verdict.get("deterministic_flaw") or "").strip()
    remediation = (verdict.get("remediation") or "").strip()
    question = (verdict.get("escalation_question") or "").strip()

    if final == "safe":
        # The AI resolved it to SAFE with shown work. Honor it (validator already required
        # proof + real citation). The override of the deterministic REVIEW is justified by the
        # named deterministic_flaw when the two disagree.
        blocked, why = _pre1_unverified_reachability_clear(pr)
        if blocked:
            _record_review(pr, why, cite, "pre1_reachability_floor", flaw)
            return ("kept", f"AI said SAFE but blocked: pre-1.0 unverified reachability clear -> REVIEW")
        dbr_blocked, dbr_why = _declared_breaking_unverified_clear(pr)
        if dbr_blocked:
            _record_review(pr, dbr_why, cite, "declared_breaking_floor", flaw)
            return ("kept", f"AI said SAFE but blocked: declared-breaking change not execution-verified -> REVIEW")
        probe_blocked, probe_why = _probe_structural_break_unverified_clear(pr)
        if probe_blocked:
            _record_review(pr, probe_why, cite, "probe_structural_floor", flaw)
            return ("kept", f"AI said SAFE but blocked: structural runtime-shape (module-system) break not execution-verified -> REVIEW")
        reason = "safe:ai-resolved" + ("-audit-override" if flaw else "")
        _apply_safe(pr, reason, ev, cite, "ai_arbiter")
        tail = f" (override flaw: {flaw})" if flaw else ""
        return ("downgraded_safe", f"AI resolved SAFE, cite {cite or '(grep-negative)'}{tail}")

    if final == "needs_change":
        _apply_needs_change(pr, ev, cite, remediation, flaw, "ai_arbiter")
        return ("kept", f"AI resolved NEEDS_CHANGE, cite {cite} -> REVIEW (+remediation)")

    if final == "escalate":
        _record_review(pr, ev, cite, "ai_arbiter", flaw, question)
        return ("kept", f"AI escalated (runtime-irreducible): {question[:60]}")

    return ("kept", f"AI final_verdict={final!r} unhandled -> keep REVIEW")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--verdicts", default="")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    data = json.load(open(args.results))
    prs = data.get("prs") or {}
    verdicts = {}
    if args.verdicts and os.path.exists(args.verdicts):
        raw = json.load(open(args.verdicts))
        for k, v in raw.items():
            if k.startswith("_"):
                continue
            verdicts[str(k)] = v

    n_safe = n_kept = 0
    for pid, pr in prs.items():
        v = verdicts.get(str(pid))
        if v is not None and "accepted" not in v:
            v = dict(v, accepted=True)  # replay verdicts are pre-trusted grounded outputs
        action, detail = reconcile_pr(pr, v, args.repo)
        if action == "downgraded_safe":
            n_safe += 1
        else:
            n_kept += 1
        print(f"PR#{pid}: {action.upper():16s} {detail}")

    print(f"\nRECONCILE_SUMMARY downgraded_safe={n_safe} kept={n_kept} total={len(prs)}")
    if args.write:
        json.dump(data, open(args.results, "w"), indent=2)
        print(f"WROTE {args.results}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
