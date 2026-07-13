#!/usr/bin/env python3
"""Re-assert AI adjudication as the LAST word after deterministic rebuild.

policy_lowering.py --enrich and verdict-map rebuild verdict_v2 / the policy
decision from raw deterministic evidence, clobbering any AI downgrade the
reconcile step applied.  The AI arbiter is authoritative for the break-reachable
residue it resolved, so re-apply its decision here, after the clobbering steps
and before the overlay.  Without this, a verified false-positive the AI cleared
(e.g. dep not imported in the bumped module) snaps back to REVIEW.

Usage:
    python3 core/ai_reassert.py <RESULTS_FILE>
"""

import json
import sys

def main():
    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    changed = False
    for pr in (data.get("prs") or {}).values():
        if not isinstance(pr, dict):
            continue
        adj = pr.get("ai_adjudication")
        if not isinstance(adj, dict):
            continue
        applied = adj.get("applied")
        if applied == "downgrade_to_safe":
            ev = adj.get("evidence") or ""
            pr["verdict_v2"] = {
                "verdict": "SAFE", "confidence": "L4", "priority": "P3", "severity": "low",
                "evidenceState": {"api_diff": "NONE", "usage": "NONE"},
                "residual": {"summary": ev, "check": adj.get("reason_code") or "safe:ai-resolved"},
                "reason": ev,
            }
            dec = (pr.get("policy_lowering") or {}).get("decision")
            if isinstance(dec, dict):
                dec.update({"verdict": "SAFE", "reason_code": adj.get("reason_code") or "safe:ai-resolved",
                            "severity": "low", "display_reason": ev})
            # Neutralize the raw deterministic merge-risk so the collapsed "Internal merge-risk
            # detail" block stops shouting REVIEW REQUIRED / High while the headline says SAFE.
            mr = pr.get("merge_risk")
            if isinstance(mr, dict):
                mr.update({"tag": "Low",
                           "reason": ev,
                           "evidenceAxis": "AI-adjudicated: change not reachable in the bumped module"})
            changed = True
        elif applied == "needs_change":
            v2 = pr.setdefault("verdict_v2", {})
            v2["verdict"] = "REVIEW"
            v2.setdefault("confidence", "L3")
            v2.setdefault("priority", "P2")
            v2.setdefault("severity", "medium")
            v2["residual"] = {"summary": adj.get("evidence") or "",
                              "check": "review:ai-needs-change"}
            v2["reason"] = adj.get("evidence") or v2.get("reason")
            changed = True
    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        print("  AI adjudication re-asserted over deterministic rebuild")


if __name__ == "__main__":
    main()
