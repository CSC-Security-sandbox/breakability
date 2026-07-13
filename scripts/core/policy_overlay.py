#!/usr/bin/env python3
"""Apply policy-lowering overlay to verdict_v2 in build-results.json.

Maps the policy_lowering.decision to a verdict_v2 record, respecting AI
adjudication, hard-fail signals, GLANCE auto-clearing, and the severity-rank
ordering so that stronger evidence is never silently overridden.

Usage:
    python3 core/policy_overlay.py <RESULTS_FILE>
"""

import json
import sys

def main():
    path = sys.argv[1]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    rank_severity = {v: k for k, v in severity_rank.items()}
    valid_v2_verdicts = {"SAFE", "REVIEW", "BLOCKED"}


    def confidence_to_level(conf, action):
        if action == "ABSTAIN":
            return "L0"
        return {"high": "L4", "medium": "L3", "low": "L2"}.get(str(conf).lower(), "L2")


    def priority(action, severity):
        if action == "FIX":
            return "P0"
        if severity == "high":
            return "P1"
        if severity == "medium":
            return "P2"
        return "P3"


    def map_policy(decision):
        # CANONICAL mapping lives in .github/scripts/verdict_contract.py::map_policy_decision.
        # Prefer it so the renderer, reconcile, and the gate never drift again; fall back to the
        # inline copy (kept in sync) if the module can't be imported in this heredoc context.
        try:
            import os as _os, sys as _sys
            _sd = _os.path.join(_os.getcwd(), ".github", "scripts")
            if _sd not in _sys.path:
                _sys.path.insert(0, _sd)
            from verdict_contract import map_policy_decision as _canon
            return _canon(decision)
        except Exception:
            pass
        action = decision.get("verdict")
        severity = decision.get("severity")
        if severity not in severity_rank:
            severity = {"FIX": "high", "ABSTAIN": "medium", "REVIEW": "medium", "GLANCE": "low", "MERGE": "none"}.get(action, "medium")
        if action == "FIX":
            verdict = "BLOCKED"
        elif action in {"REVIEW", "ABSTAIN"}:
            verdict = "REVIEW"
        elif action in {"MERGE", "GLANCE"}:
            # GLANCE = clean build/tests, only soft/missing-changelog uncertainty -> auto-clear
            # (Safe to merge / optional glance, Low). Mapping GLANCE->REVIEW was the #121->#128
            # review-wall regression. Keep in sync with verdict_contract._ACTION_TO_BUCKET.
            verdict = "SAFE"
        else:
            return None
        return {
            "verdict": verdict,
            "severity": severity,
            "confidence": confidence_to_level(decision.get("confidence"), action),
            "priority": priority(action, severity),
            "reason": decision.get("display_reason") or decision.get("reason_code") or "",
            "residual": {
                "summary": decision.get("display_reason") or decision.get("reason_code") or "",
                "check": decision.get("reason_code") or "",
            },
            "policyDecision": decision,
        }


    def stronger_review(existing, mapped):
        existing_sev = existing.get("severity")
        if existing_sev not in severity_rank:
            existing_sev = {"BLOCKED": "high", "REVIEW": "medium", "SAFE": "low"}.get(existing.get("verdict"), "medium")
        mapped_sev = mapped.get("severity")
        return severity_rank.get(mapped_sev, 2) >= severity_rank.get(existing_sev, 2)


    def policy_has_hard_fail(policy):
        bundle = policy.get("bundle") if isinstance(policy, dict) else None
        signals = bundle.get("signals") if isinstance(bundle, dict) else None
        if not isinstance(signals, dict):
            return False
        for name in ("build", "test", "api_diff", "probe", "security"):
            record = signals.get(name)
            if isinstance(record, dict) and record.get("status") == "fail":
                return True
        return False


    def policy_evidence_state(policy, existing_state):
        state = dict(existing_state) if isinstance(existing_state, dict) else {}
        bundle = policy.get("bundle") if isinstance(policy, dict) else None
        signals = bundle.get("signals") if isinstance(bundle, dict) else None
        if not isinstance(signals, dict):
            return state

        mapping = {
            "build": "build",
            "test": "test",
            "api_diff": "api_diff",
            "security": "vuln",
            "release_notes": "changelog",
            "reachability": "usage",
        }
        for source, target in mapping.items():
            record = signals.get(source)
            if not isinstance(record, dict):
                continue
            status = record.get("status")
            if status == "fail":
                state[target] = "POSITIVE"
            elif status == "pass":
                state[target] = "NEGATIVE"
            elif status == "not_applicable":
                state[target] = "N_A"
            elif status == "unavailable":
                state[target] = "UNAVAILABLE"
            elif status == "unknown":
                state[target] = "NONE"
        return state


    changed = False
    for pr in (data.get("prs") or {}).values():
        if not isinstance(pr, dict):
            continue
        policy = pr.get("policy_lowering") or {}
        # The AI arbiter (independent-first + deterministic-audit) is authoritative for the
        # break-reachable residue it resolved. Do not let the legacy policy overlay revert an
        # AI-applied downgrade/finding using the stale pre-reconcile policy decision.
        adj = pr.get("ai_adjudication")
        if isinstance(adj, dict) and adj.get("applied") in ("downgrade_to_safe", "needs_change"):
            continue
        decision = policy.get("decision") if isinstance(policy, dict) else None
        if not isinstance(decision, dict):
            continue
        mapped = map_policy(decision)
        if not mapped:
            continue

        existing = pr.get("verdict_v2")
        if not isinstance(existing, dict) or existing.get("verdict") not in valid_v2_verdicts:
            existing = {}
        existing_verdict = existing.get("verdict")

        if existing_verdict == "BLOCKED" and mapped["verdict"] != "BLOCKED":
            if not (
                mapped["verdict"] == "REVIEW"
                and decision.get("reason_code") == "review:uncertain-critical-signal"
                and not policy_has_hard_fail(policy)
            ):
                continue
        if mapped["verdict"] == "SAFE" and existing_verdict == "BLOCKED":
            continue
        if mapped["verdict"] == "REVIEW" and existing_verdict == "REVIEW" and not stronger_review(existing, mapped):
            existing_sev = existing.get("severity")
            allow_glance_lowering = (
                decision.get("verdict") == "GLANCE"
                and str(decision.get("reason_code") or "").startswith("glance:")
                and existing_sev != "high"
                and not policy_has_hard_fail(policy)
            )
            if not allow_glance_lowering:
                continue
        if mapped["verdict"] == "SAFE" and existing_verdict == "REVIEW" and decision.get("verdict") == "GLANCE":
            # A clean-build GLANCE auto-clears to SAFE, but it must NOT override an existing
            # high-risk REVIEW (e.g. a declared-break flagged by another layer). Only lower a
            # SOFT (non-high) glance-class REVIEW. MERGE (hard-clean) keeps its prior override.
            #
            # Exception: `glance:tests-pass-soft-api-uncertain` is emitted by the evidence contract
            # ONLY when build, tests AND release-notes are all clean and the API diff found just
            # non-breaking uncertainty (after structural-fallback noise suppression). When the JS
            # verdict-map still rates such a PR a high REVIEW, that high is a structural-fallback
            # false break-reachable (a `go doc` type_changed whose old==new definition) — the
            # tested policy layer has authoritatively cleared it, so it may lower even a high REVIEW.
            existing_sev = existing.get("severity")
            soft_api_glance = decision.get("reason_code") == "glance:tests-pass-soft-api-uncertain"
            allow_glance_lowering = (
                str(decision.get("reason_code") or "").startswith("glance:")
                and (existing_sev != "high" or soft_api_glance)
                and not policy_has_hard_fail(policy)
            )
            if not allow_glance_lowering:
                continue
        if mapped["verdict"] == "SAFE" and existing_verdict == "SAFE":
            continue

        merged = dict(existing)
        merged.update(mapped)
        merged["evidenceState"] = policy_evidence_state(policy, existing.get("evidenceState"))
        pr["verdict_v2"] = merged
        # When we auto-clear a REVIEW to SAFE, the raw JS-baked merge_risk may still shout a High
        # "BREAK-reachable ..." that the tested policy layer has cleared (a structural-fallback
        # false break — go-doc type_changed with old==new definition). Neutralize it so the
        # collapsed merge-risk detail block and the overclaim audit agree with the SAFE headline.
        if mapped["verdict"] == "SAFE" and existing_verdict == "REVIEW":
            mr = pr.get("merge_risk")
            if isinstance(mr, dict) and mr.get("tag") == "High" and "break-reachable" in str(mr.get("reason") or "").lower():
                mr.update({
                    "tag": "Low",
                    "reason": merged.get("reason") or "API diff found only non-breaking uncertainty; not break-reachable",
                    "evidenceAxis": "policy-cleared: structural API-diff noise, not a real break-reachable change",
                })
        changed = True

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)


if __name__ == "__main__":
    main()
