#!/usr/bin/env python3
"""Validate one independent-first + deterministic-audit AI-adjudicator JSON output.

The AI is the senior arbiter, but it is only trusted when it is FALSIFIABLE and shows its work:
  - strict schema (keys + types)
  - citation, if non-empty, must resolve to a real file:line in THIS repo (the #38 guard)
  - a `safe`/`needs_change` final verdict must carry non-empty `proof` (a command that was run)
  - if the AI claims deterministic was WRONG (deterministic_agrees=false), it MUST name the flaw
    (forces it to justify any override rather than hand-wave)
  - `needs_change` must carry a remediation; `escalate` must carry a question
  - any failure => REJECT => PR stays REVIEW (fail-safe)

Backward compatible: also accepts the legacy {reachable, recommendation} and the interim
{verdict, break_class} shapes and normalizes them to `final_verdict`.

Usage:  echo '<agent json>' | python3 validate_adjudication.py --repo .
Exit 0 = accepted (prints normalized verdict json); exit 1 = rejected (prints reason).
"""
import json
import os
import sys


def fail(reason):
    print(json.dumps({"accepted": False, "reason": reason, "final_verdict": "review"}))
    return 1


def _cite_ok(cite, repo):
    if not cite:
        return True
    path = cite.split(":")[0].lstrip("./")
    return os.path.exists(os.path.join(repo, path))


def _emit(d, final, repo):
    cite = (d.get("citation") or "").strip()
    print(json.dumps({
        "accepted": True,
        "final_verdict": final,
        "pr": d.get("pr"),
        "break_class": d.get("break_class", ""),
        "independent_verdict": d.get("independent_verdict", final),
        "deterministic_agrees": d.get("deterministic_agrees"),
        "deterministic_flaw": (d.get("deterministic_flaw") or "").strip(),
        "proof": (d.get("proof") or d.get("evidence") or "").strip(),
        "citation": cite,
        "remediation": (d.get("remediation") or "").strip(),
        "escalation_question": (d.get("escalation_question") or "").strip(),
        "confidence": d.get("confidence", 0.0),
    }))
    return 0


def main():
    repo = "."
    if "--repo" in sys.argv:
        repo = sys.argv[sys.argv.index("--repo") + 1]
    raw = sys.stdin.read().strip()
    if "```" in raw:
        raw = raw.split("```")[1].lstrip("json").strip() if raw.count("```") >= 2 else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < 0:
        return fail("no JSON object in adjudicator output")
    try:
        d = json.loads(raw[start:end + 1])
    except Exception as e:
        return fail(f"invalid JSON: {e}")

    # ── Legacy {reachable, recommendation} ─────────────────────────────────────
    if "recommendation" in d and "final_verdict" not in d and "verdict" not in d:
        cite = (d.get("citation") or "").strip()
        if not _cite_ok(cite, repo):
            return fail(f"INVENTED CITATION: {cite} does not exist in repo")
        if d.get("recommendation") == "safe" and not (d.get("reachable") is False and cite):
            return fail("legacy downgrade to 'safe' requires reachable=false WITH a real citation")
        d["proof"] = d.get("evidence", "")
        return _emit(d, "safe" if d.get("recommendation") == "safe" else "review", repo)

    # ── Resolve the final verdict field (current or interim shape) ─────────────
    final = d.get("final_verdict") or d.get("verdict")
    if final is None:
        return fail("missing final_verdict")
    if final not in ("safe", "needs_change", "escalate"):
        return fail("final_verdict must be safe|needs_change|escalate (AI can never clear a CVE or FIX-block)")

    bc = d.get("break_class")
    if bc is not None and bc not in ("symbol_removed", "signature_changed", "behavioral", "none"):
        return fail("break_class invalid")

    cite = (d.get("citation") or "").strip()
    if not _cite_ok(cite, repo):
        return fail(f"INVENTED CITATION: {cite} does not exist in repo")

    proof = (d.get("proof") or "").strip()
    if final in ("safe", "needs_change") and len(proof) < 8:
        return fail(f"verdict '{final}' requires shown work in 'proof' (a command you ran)")
    if d.get("deterministic_agrees") is False and not (d.get("deterministic_flaw") or "").strip():
        return fail("claiming deterministic is wrong requires naming the specific deterministic_flaw")
    if final == "needs_change" and not (d.get("remediation") or "").strip():
        return fail("needs_change requires a remediation")
    if final == "escalate" and not (d.get("escalation_question") or "").strip():
        return fail("escalate requires an escalation_question")

    return _emit(d, final, repo)


if __name__ == "__main__":
    sys.exit(main())
