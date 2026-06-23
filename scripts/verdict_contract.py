#!/usr/bin/env python3
"""verdict_contract.py — the SINGLE source of truth for "what verdict does this PR have".

Why this exists
---------------
The breakability pipeline derived the per-PR verdict in (at least) four independent
places, each with its own ``verdict_v2 or policy_lowering.decision`` fallback:

  * reconcile_adjudication.py     (_current_verdict)
  * independent_adjudicate.sh     (inline python)
  * post-fallback-comments.sh     (map_policy / get_verdict_v2)   <- the RENDERER
  * breakability/harness/run_gate.py (derive_prediction)          <- the GATE

Two failure modes followed directly from that duplication:

  1. SILENT NO-OP / field drift: reconcile + the AI step read ``verdict_v2`` which is
     materialised LATE (by the renderer). Read too early it is empty, so those stages
     selected 0 PRs and exited 0 — "succeeded but did nothing". The AI layer was dormant
     for weeks for exactly this reason ("Oracle confidence: not available").

  2. UNMEASURED RENDERER REGRESSION (#121 -> #128): the GATE graded ``build.verdict`` +
     ``merge_risk.tag`` while the RENDERER graded ``policy_lowering.decision`` -> bucket.
     A regression in the renderer/policy mapping (GLANCE -> REVIEW collapsing the
     "Safe to merge (L4)" + "Build passes (L2/L3)" categories into a review wall) moved
     the rendered output but NOT the gate number, so the loop never caught it.

This module collapses all of that into one documented mapping + one accessor so:
  * every reader agrees on the authoritative verdict, and
  * the gate grades the SAME verdict the developer sees.

It deliberately has NO dependency on the slow build layer so it runs offline in
milliseconds (unit-testable, fixture-replayable).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

# ── Buckets the developer actually sees (verdict_v2 vocabulary) ───────────────
BUCKET_SAFE = "SAFE"
BUCKET_REVIEW = "REVIEW"
BUCKET_BLOCKED = "BLOCKED"
VALID_BUCKETS = {BUCKET_SAFE, BUCKET_REVIEW, BUCKET_BLOCKED}

# ── Gate prediction vocabulary (corpus / run_gate) ───────────────────────────
PRED_AUTO_CLEAR = "auto_clear"
PRED_REVIEW = "review"
PRED_FIX = "fix"

_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}

# ── Breakability grades (decisive verdict terminology) ────────────────────────
GRADE_SAFE = "SAFE"
GRADE_LOW_BREAKING = "LOW_BREAKING"
GRADE_MEDIUM_BREAKING = "MEDIUM_BREAKING"
GRADE_HIGH_BREAKING = "HIGH_BREAKING"

# ── Canonical typed-verdict -> bucket mapping ────────────────────────────────
# typed VerdictAction (policy_lowering.decision.verdict) ∈
#   {MERGE, GLANCE, REVIEW, FIX, ABSTAIN}
#
# THE FIX (restores #121, kills the #128 review wall):
#   GLANCE means "build/tests clean, only soft/missing-changelog uncertainty —
#   merge with an optional glance". #121 auto-cleared these (Safe to merge / optional
#   glance, Low). The typed rewrite mapped GLANCE -> REVIEW, which zeroed the
#   "Safe to merge (L4)" + "Build passes (L2/L3)" categories and ballooned REVIEW.
#   GLANCE -> SAFE (low) restores the product-useful categorisation.
#
# Safety: GLANCE is only emitted by evidence_contract.decide() when build+test pass
# (clean core signals). The corpus false-green guards (PR#11, PR#38) are REVIEW
# (break-reachable), and PR#18 is FIX (build fail) — none are GLANCE — so GLANCE->SAFE
# cannot move a known false-green. run_gate.py's false_green==0 hard gate is the
# backstop that proves this on the full corpus.
_ACTION_TO_BUCKET = {
    "FIX": BUCKET_BLOCKED,
    "REVIEW": BUCKET_REVIEW,
    "ABSTAIN": BUCKET_REVIEW,
    "GLANCE": BUCKET_SAFE,
    "MERGE": BUCKET_SAFE,
}

_ACTION_DEFAULT_SEVERITY = {
    "FIX": "high",
    "ABSTAIN": "medium",
    "REVIEW": "medium",
    "GLANCE": "low",
    "MERGE": "none",
}

_BUCKET_TO_PREDICTION = {
    BUCKET_BLOCKED: PRED_FIX,
    BUCKET_REVIEW: PRED_REVIEW,
    BUCKET_SAFE: PRED_AUTO_CLEAR,
}


def _confidence_to_level(conf: Any, action: str) -> str:
    if action == "ABSTAIN":
        return "L0"
    return {"high": "L4", "medium": "L3", "low": "L2"}.get(str(conf).lower(), "L2")


def _priority(action: str, severity: str) -> str:
    if action == "FIX":
        return "P0"
    if severity == "high":
        return "P1"
    if severity == "medium":
        return "P2"
    return "P3"


def map_policy_decision(decision: Mapping[str, Any]) -> Optional[dict]:
    """Map a typed policy_lowering.decision -> rendered verdict_v2 object.

    Returns None for an unknown/empty action so callers can fail-closed to REVIEW.
    This is the canonical version of post-fallback-comments.sh::map_policy — keep the
    renderer in sync with this function (it imports it).
    """
    if not isinstance(decision, Mapping):
        return None
    action = decision.get("verdict")
    if action not in _ACTION_TO_BUCKET:
        return None
    severity = decision.get("severity")
    if severity not in _SEVERITY_RANK:
        severity = _ACTION_DEFAULT_SEVERITY.get(action, "medium")
    bucket = _ACTION_TO_BUCKET[action]
    reason = decision.get("display_reason") or decision.get("reason_code") or ""
    return {
        "verdict": bucket,
        "severity": severity,
        "confidence": _confidence_to_level(decision.get("confidence"), action),
        "priority": _priority(action, severity),
        "reason": reason,
        "residual": {"summary": reason, "check": decision.get("reason_code") or ""},
        "policyDecision": dict(decision),
    }


def _policy_decision(pr: Mapping[str, Any]) -> dict:
    pol = pr.get("policy_lowering") or {}
    dec = pol.get("decision") if isinstance(pol, Mapping) else None
    return dec if isinstance(dec, Mapping) else {}


def _hard_fix_floor(pr: Mapping[str, Any]) -> bool:
    """States that MUST be FIX/BLOCKED and can never be downgraded (false-green floor):
    a build that does not compile, or a policy decision that introduced a security finding.
    """
    if (pr.get("build") or {}).get("verdict") == "fail":
        return True
    rc = str(_policy_decision(pr).get("reason_code") or "")
    return rc.startswith("build:") or rc == "security:introduced"


def _valid_v2(pr: Mapping[str, Any]) -> Optional[dict]:
    v2 = pr.get("verdict_v2")
    if isinstance(v2, Mapping) and v2.get("verdict") in VALID_BUCKETS:
        return dict(v2)
    return None


def assign_breakability_grade(pr: Mapping[str, Any], verdict_bucket: str, signals: Optional[Mapping] = None) -> str:
    """Assign decisive breakability grade based on evidence.
    
    Returns SAFE | LOW_BREAKING | MEDIUM_BREAKING | HIGH_BREAKING
    
    Grading logic:
      HIGH: Build FAIL + test FAIL + reached (must fix before merge)
      MEDIUM: Probe mismatch + reached OR major + changelog breaking + reached
      LOW: API warnings + reached BUT probe same (quick review)
      SAFE: NOT-REACHED OR all signals green
    """
    if verdict_bucket == BUCKET_BLOCKED:
        return GRADE_HIGH_BREAKING
    if verdict_bucket == BUCKET_SAFE:
        return GRADE_SAFE
    
    # Extract signals from build_results or policy
    policy = _policy_decision(pr)
    if not signals:
        signals = {}
    
    # Check for high breakability (multiple hard failures)
    build_failed = policy.get("build_outcome") in ["FAIL", "FAILED"]
    test_failed = policy.get("test_outcome") in ["FAIL", "FAILED"]
    reached = policy.get("reachability", {}).get("relevant") is True
    
    if build_failed and test_failed and reached:
        return GRADE_HIGH_BREAKING
    
    # Check for medium breakability (behavioral changes or major breaking)
    probe_mismatch = policy.get("probe_outcome") == "DIFFERENT"
    bump_type = policy.get("bump_type", "")
    changelog_breaking = "breaking" in policy.get("changelog_summary", "").lower()
    
    if (probe_mismatch and reached) or (bump_type == "major" and changelog_breaking and reached):
        return GRADE_MEDIUM_BREAKING
    
    # Check for low breakability (API warnings but behavior same)
    apidiff_warned = policy.get("apidiff_grade") in ["WARN", "ERROR"]
    probe_same = policy.get("probe_outcome") == "SAME"
    
    if apidiff_warned and reached and probe_same:
        return GRADE_LOW_BREAKING
    
    # Default: MEDIUM for unclear cases (fail-closed, better than hiding)
    return GRADE_MEDIUM_BREAKING if verdict_bucket == BUCKET_REVIEW else GRADE_SAFE


def authoritative_verdict(pr: Mapping[str, Any]) -> dict:
    """THE one accessor. Returns the authoritative rendered verdict object for a PR.

    Precedence (highest first), all converging on a single bucket:
      0. hard FIX floor (build fail / security introduced)  -> BLOCKED, never downgraded
      1. AI adjudication (reconcile)                         -> SAFE / REVIEW
      2. an already-materialised, valid verdict_v2           -> as-is
      3. typed policy_lowering.decision mapped via map_policy
      4. fail-closed                                         -> REVIEW (medium)

    Always returns a dict with at least: verdict, severity, confidence, priority,
    reason, plus ``source`` describing which rule fired (for debuggability).
    Now also includes ``breakability_grade`` for decisive verdicts.
    """
    if _hard_fix_floor(pr):
        dec = _policy_decision(pr)
        result = {
            "verdict": BUCKET_BLOCKED,
            "severity": "high",
            "confidence": "L4",
            "priority": "P0",
            "reason": dec.get("display_reason") or dec.get("reason_code") or "build failed on the candidate version",
            "source": "hard_fix_floor",
        }
        result["breakability_grade"] = assign_breakability_grade(pr, BUCKET_BLOCKED)
        return result

    adj = pr.get("ai_adjudication")
    if isinstance(adj, Mapping):
        applied = adj.get("applied")
        if applied == "downgrade_to_safe":
            ev = adj.get("evidence") or ""
            result = {
                "verdict": BUCKET_SAFE, "severity": "low", "confidence": "L4", "priority": "P3",
                "reason": ev, "source": "ai:downgrade_to_safe",
            }
            result["breakability_grade"] = GRADE_SAFE
            return result
        if applied == "needs_change":
            result = {
                "verdict": BUCKET_REVIEW, "severity": "medium", "confidence": "L3", "priority": "P2",
                "reason": adj.get("evidence") or "", "source": "ai:needs_change",
            }
            result["breakability_grade"] = assign_breakability_grade(pr, BUCKET_REVIEW)
            return result

    v2 = _valid_v2(pr)
    if v2 is not None:
        v2.setdefault("source", "verdict_v2")
        v2.setdefault("breakability_grade", assign_breakability_grade(pr, v2.get("verdict", BUCKET_REVIEW)))
        return v2

    mapped = map_policy_decision(_policy_decision(pr))
    if mapped is not None:
        mapped["source"] = "policy_lowering"
        mapped["breakability_grade"] = assign_breakability_grade(pr, mapped.get("verdict", BUCKET_REVIEW))
        return mapped

    result = {
        "verdict": BUCKET_REVIEW, "severity": "medium", "confidence": "L2", "priority": "P2",
        "reason": "no typed verdict available; fail-closed to review",
        "source": "fail_closed",
    }
    result["breakability_grade"] = GRADE_MEDIUM_BREAKING
    return result


def prediction_for_pr(pr: Mapping[str, Any]) -> str:
    """Authoritative gate prediction {auto_clear|review|fix} for a PR.

    Grades the SAME verdict the renderer shows (via authoritative_verdict), so a
    renderer/policy regression now moves the gate number. This is what closes the
    #121->#128 "regression invisible to the gate" hole.
    """
    bucket = authoritative_verdict(pr).get("verdict", BUCKET_REVIEW)
    return _BUCKET_TO_PREDICTION.get(bucket, PRED_REVIEW)


# ── Loud "did this stage actually do work?" assertion ────────────────────────
class StageNoOpError(RuntimeError):
    """Raised when a pipeline stage processed 0 of N PRs without opting in to empty."""


def assert_stage_did_work(stage: str, input_count: int, processed_count: int,
                          allow_empty: bool = False) -> None:
    """Fail LOUD instead of the historical silent exit-0-did-nothing.

    A stage that had input PRs but processed none is almost always a field-mismatch /
    dormant-layer bug (the class that hid the AI layer for weeks). Callers that legitimately
    expect zero (e.g. no REVIEW residue) pass allow_empty=True.
    """
    if input_count > 0 and processed_count == 0 and not allow_empty:
        raise StageNoOpError(
            f"[{stage}] processed 0 of {input_count} PRs — likely a verdict-field mismatch or a "
            f"dormant layer. Pass allow_empty=True if zero is genuinely expected."
        )


def stage_report(stage: str, input_count: int, processed_count: int) -> str:
    return f"[{stage}] input_prs={input_count} processed_prs={processed_count}"


# ── CLI entrypoint ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import json
    
    if len(sys.argv) < 2:
        print("Usage: verdict_contract.py <build-results.json> [--write]", file=sys.stderr)
        print("", file=sys.stderr)
        print("Enriches build-results.json with authoritative verdicts.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Options:", file=sys.stderr)
        print("  --write    Write enriched data back to input file (default: print to stdout)", file=sys.stderr)
        sys.exit(1)
    
    results_file = sys.argv[1]
    write_back = "--write" in sys.argv
    
    with open(results_file) as f:
        data = json.load(f)
    
    # Process all PRs
    results = data.get("results", [])
    if not results:
        results = [v for k, v in data.get("prs", {}).items()]
    
    processed = 0
    for pr in results:
        verdict = authoritative_verdict(pr)
        pr["verdict_v2"] = verdict
        processed += 1
    
    # Write back or print
    if write_back:
        with open(results_file, "w") as f:
            json.dump(data, f, indent=2)
        print(f"✅ Enriched {processed} PRs with authoritative verdicts → {results_file}", file=sys.stderr)
    else:
        json.dump(data, sys.stdout, indent=2)
    
    sys.exit(0)
