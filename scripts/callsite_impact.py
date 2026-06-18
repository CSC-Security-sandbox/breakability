#!/usr/bin/env python3
"""Callsite impact analyzer for the breakability pipeline.

Given a PR record, optional release-note evidence, and optional pre-computed
reachability evidence (from lite.py), decides whether production call sites are
affected enough to require human review or whether the verdict can be lowered.

Four impact verdicts
--------------------
NOT_REACHED       — Deterministic layer confirmed the package is not imported
                    (reachability verdict == ABSENT, checked=True,
                    kind='not_imported').  Verdict may be lowered to MERGE/GLANCE.

REACHED_RELEVANT  — At least one production callsite was found AND its symbol
                    matches a symbol claimed as breaking in the release notes.
                    Human review is required; verdict must not be lowered.

REACHED_UNKNOWN   — Production callsite evidence exists but cannot be aligned to
                    the specific symbols claimed in the release notes (e.g. no
                    symbol information in the callsite records, or no release-note
                    symbol list was provided).  Cannot lower verdict.

UNCERTAIN         — Data is insufficient or ambiguous: reachability returned
                    UNCERTAIN, dynamic hazards are present, or evidence is missing.
                    Cannot lower verdict.

Evidence contract coupling
--------------------------
Impact is mapped to an ``EvidenceRecord``-compatible ``signal`` sub-dict so that
downstream consumers (differential-probe, policy engine) can feed it directly into
``evidence_contract.EvidenceBundle`` without further transformation:

  NOT_REACHED       → status=pass,    relevant=False  — safe to lower
  REACHED_RELEVANT  → status=fail,    relevant=True   — requires REVIEW
  REACHED_UNKNOWN   → status=unknown, relevant=True   — cannot lower
  UNCERTAIN         → status=unknown, relevant=None   — cannot lower

Injection safety
----------------
Free-text fields (rationale, snippets, release-note prose) are NEVER read by the
decision logic.  Only structured/typed fields drive the impact verdict:
  - reachability_evidence["verdict"] / ["callsites"] / ["dynamic_hazards"]
  - release_note_evidence["affected_symbols"] / ["claims"][*]["symbols"] / ["has_breaking_change"]

Limitations (Go MVP)
--------------------
- Symbol matching is leaf-name only; aliased or dot-imported packages are not
  resolved (deferred: deep.go callgraph).
- ABSENT is only asserted when the deterministic layer explicitly confirmed it
  (checked=True, kind='not_imported').  A missing reachability field → UNCERTAIN.
- Indirect/transitive callsites are not traced; use deep.go for that.
- Dynamic hazards degrade confidence to 'low' but do not override REACHED_RELEVANT
  — that would require human review regardless.
- Release-note symbol extraction reads only typed list fields, never prose.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Impact constants — only these four values may appear in output["impact"]
# ---------------------------------------------------------------------------

NOT_REACHED: str = "NOT_REACHED"
REACHED_RELEVANT: str = "REACHED_RELEVANT"
REACHED_UNKNOWN: str = "REACHED_UNKNOWN"
UNCERTAIN: str = "UNCERTAIN"

# Map impact → (evidence_contract status, relevant flag)
# These values mirror evidence_contract.SignalStatus and are kept as plain strings
# so that this module remains stdlib-only and importable without evidence_contract.
_IMPACT_TO_SIGNAL: dict[str, tuple[str, Optional[bool]]] = {
    NOT_REACHED:      ("pass",    False),
    REACHED_RELEVANT: ("fail",    True),
    REACHED_UNKNOWN:  ("unknown", True),
    UNCERTAIN:        ("unknown", None),
}

# ---------------------------------------------------------------------------
# Lazy import of lite.py (avoids hard dependency path coupling)
# ---------------------------------------------------------------------------

_LITE_MODULE: Any = None


def _get_lite() -> Any:
    global _LITE_MODULE
    if _LITE_MODULE is None:
        _lite_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools", "reachability", "lite.py",
        )
        spec = importlib.util.spec_from_file_location("_lite_reachability", _lite_path)
        mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _LITE_MODULE = mod
    return _LITE_MODULE


# ---------------------------------------------------------------------------
# Internal helpers — only typed fields are read; prose fields are ignored
# ---------------------------------------------------------------------------

def _extract_claimed_symbols(rn_evidence: Any) -> list[str]:
    """Extract symbol names from release-note evidence (typed fields only).

    Accepted schema variants:
      {"affected_symbols": ["Foo", "Bar"]}           # preferred
      {"changed_symbols": ["Foo"]}                   # legacy alias
      {"claims": [{"symbols": ["Foo"]}, ...]}        # per-claim

    Prose fields such as "rationale", "description", and "text" are intentionally
    ignored — they must not influence structured output.
    """
    if not isinstance(rn_evidence, dict):
        return []
    syms: set[str] = set()
    for key in ("affected_symbols", "changed_symbols"):
        v = rn_evidence.get(key)
        if isinstance(v, list):
            for s in v:
                if isinstance(s, str) and s.strip():
                    syms.add(s.strip())
    for claim in rn_evidence.get("claims") or []:
        if not isinstance(claim, dict):
            continue
        for s in claim.get("symbols") or []:
            if isinstance(s, str) and s.strip():
                syms.add(s.strip())
    return sorted(syms)


def _is_breaking_claim(rn_evidence: Any) -> bool:
    """Return True only if release-note evidence has a typed breaking=True flag."""
    if not isinstance(rn_evidence, dict):
        return False
    if rn_evidence.get("has_breaking_change") is True:
        return True
    for claim in rn_evidence.get("claims") or []:
        if isinstance(claim, dict) and claim.get("breaking") is True:
            return True
    return False


def _prod_callsites(callsites: list[dict]) -> list[dict]:
    return [c for c in callsites if not c.get("is_test", False)]


def _callsites_matching_symbols(callsites: list[dict], sym_set: set[str]) -> list[dict]:
    """Exact leaf-name match between callsite symbol and claimed symbol set."""
    return [c for c in callsites if c.get("symbol") in sym_set]


def _build_result(
    *,
    impact: str,
    confidence: str,
    callsites: list[dict],
    matched_claims: list[str],
    unmatched_claims: list[str],
    dynamic_hazards: list[str],
    reason_code: str,
    claimed_symbols: list[str],
    is_breaking: bool,
    sources: list[str],
    reach_verdict: str,
) -> dict:
    status, relevant = _IMPACT_TO_SIGNAL[impact]
    return {
        "impact": impact,
        "confidence": confidence,
        "callsites": callsites,
        "matched_claims": matched_claims,
        "unmatched_claims": unmatched_claims,
        "dynamic_hazards": dynamic_hazards,
        "reason_code": reason_code,
        "claimed_symbols": claimed_symbols,
        "is_breaking": is_breaking,
        "reachability_verdict": reach_verdict,
        "sources_used": sources,
        # Pre-mapped sub-dict for direct consumption by evidence_contract.EvidenceRecord
        # name="reachability" aligns with evidence_contract.SignalName.REACHABILITY
        "signal": {
            "name": "reachability",
            "status": status,
            "relevant": relevant,
            "confidence": confidence,
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(
    pr_record: dict,
    release_note_evidence: Optional[dict] = None,
    reachability_evidence: Optional[dict] = None,
) -> dict:
    """Determine callsite impact for a single PR.

    Args:
        pr_record: A single PR record from build-results JSON.
        release_note_evidence: Optional structured release-note evidence dict.
            Only typed fields are consumed (affected_symbols, claims[].symbols,
            has_breaking_change).  Prose fields (rationale, description, text)
            are intentionally ignored — they cannot affect the verdict.
        reachability_evidence: Optional pre-computed reachability evidence dict
            as returned by ``lite.analyze()``.  When omitted, lite.py is called
            automatically using the PR record.

    Returns:
        Structured impact dict with keys:
          impact, confidence, callsites, matched_claims, unmatched_claims,
          dynamic_hazards, reason_code, claimed_symbols, is_breaking,
          reachability_verdict, sources_used, signal
    """
    if not isinstance(pr_record, dict):
        raise TypeError("pr_record must be a dict")

    # ── Step 1: Extract typed claims from release notes ───────────────────────
    claimed_symbols = _extract_claimed_symbols(release_note_evidence)
    is_breaking = _is_breaking_claim(release_note_evidence)

    # ── Step 2: Obtain reachability evidence ──────────────────────────────────
    if reachability_evidence is None:
        lite = _get_lite()
        reach = lite.analyze(pr_record, claimed_symbols if claimed_symbols else None)
    else:
        if not isinstance(reachability_evidence, dict):
            raise TypeError("reachability_evidence must be a dict or None")
        reach = reachability_evidence

    reach_verdict: str = reach.get("verdict") or "UNCERTAIN"
    reach_confidence: str = reach.get("confidence") or "low"
    reach_callsites: list[dict] = reach.get("callsites") or []
    dynamic_hazards: list[str] = reach.get("dynamic_hazards") or []
    sources: list[str] = reach.get("sources_used") or []

    prod_cs = _prod_callsites(reach_callsites)

    # ── Step 3: Apply deterministic impact rules ──────────────────────────────

    # Rule 1: ABSENT → NOT_REACHED
    # Only valid when the deterministic layer explicitly confirmed not_imported.
    if reach_verdict == "ABSENT":
        return _build_result(
            impact=NOT_REACHED,
            confidence="high",
            callsites=[],
            matched_claims=[],
            unmatched_claims=claimed_symbols,
            dynamic_hazards=[],
            reason_code="reachability:absent:deterministic_not_imported",
            claimed_symbols=claimed_symbols,
            is_breaking=is_breaking,
            sources=sources,
            reach_verdict=reach_verdict,
        )

    # Rule 2: PRESENT → align with release-note claims
    if reach_verdict == "PRESENT":
        if claimed_symbols:
            sym_set = set(claimed_symbols)
            matched_cs = _callsites_matching_symbols(prod_cs, sym_set)
            matched_sym_names = sorted({c.get("symbol", "") for c in matched_cs if c.get("symbol")})
            unmatched = [s for s in claimed_symbols if s not in {c.get("symbol") for c in matched_cs}]

            if matched_cs:
                # Production callsite found for a claimed breaking symbol.
                # Dynamic hazards degrade confidence but do NOT clear the REACHED_RELEVANT
                # verdict — that would require human sign-off regardless.
                confidence = "medium" if dynamic_hazards else "high"
                return _build_result(
                    impact=REACHED_RELEVANT,
                    confidence=confidence,
                    callsites=prod_cs,
                    matched_claims=matched_sym_names,
                    unmatched_claims=unmatched,
                    dynamic_hazards=dynamic_hazards,
                    reason_code="callsite:present:symbol_matches_breaking_claim",
                    claimed_symbols=claimed_symbols,
                    is_breaking=is_breaking,
                    sources=sources,
                    reach_verdict=reach_verdict,
                )
            else:
                # Production callsites exist but none match the claimed symbols.
                # Cannot assert NOT_REACHED; caller must investigate further.
                confidence = "low" if dynamic_hazards else "medium"
                return _build_result(
                    impact=REACHED_UNKNOWN,
                    confidence=confidence,
                    callsites=prod_cs,
                    matched_claims=[],
                    unmatched_claims=claimed_symbols,
                    dynamic_hazards=dynamic_hazards,
                    reason_code="callsite:present:no_symbol_match_for_claims",
                    claimed_symbols=claimed_symbols,
                    is_breaking=is_breaking,
                    sources=sources,
                    reach_verdict=reach_verdict,
                )
        else:
            # PRESENT but no symbol claims from release notes — cannot qualify.
            confidence = "low" if dynamic_hazards else "medium"
            return _build_result(
                impact=REACHED_UNKNOWN,
                confidence=confidence,
                callsites=prod_cs,
                matched_claims=[],
                unmatched_claims=[],
                dynamic_hazards=dynamic_hazards,
                reason_code="callsite:present:no_release_note_symbol_claims",
                claimed_symbols=[],
                is_breaking=is_breaking,
                sources=sources,
                reach_verdict=reach_verdict,
            )

    # Rule 3: UNCERTAIN (or any unrecognised verdict) → propagate
    uncertain_reason = reach.get("uncertain_reason") or "reachability_uncertain"
    if dynamic_hazards:
        reason_code = "uncertain:dynamic_hazards:" + ",".join(sorted(dynamic_hazards))
    else:
        reason_code = f"uncertain:{uncertain_reason}"

    return _build_result(
        impact=UNCERTAIN,
        confidence="low",
        callsites=reach_callsites,
        matched_claims=[],
        unmatched_claims=claimed_symbols,
        dynamic_hazards=dynamic_hazards,
        reason_code=reason_code,
        claimed_symbols=claimed_symbols,
        is_breaking=is_breaking,
        sources=sources,
        reach_verdict=reach_verdict,
    )


def analyze_build_results(
    results: dict,
    release_note_evidence_by_pr: Optional[dict] = None,
    reachability_evidence_by_pr: Optional[dict] = None,
) -> dict:
    """Analyze every PR in a build-results JSON file.

    Args:
        results: Full build-results JSON dict with a top-level "prs" key.
        release_note_evidence_by_pr: Optional {pr_number_str: rn_evidence_dict}.
        reachability_evidence_by_pr: Optional {pr_number_str: reach_evidence_dict}.

    Returns:
        {pr_number_str: impact_dict}
    """
    prs = results.get("prs") or {}
    out: dict = {}
    for pr_num, pr_record in prs.items():
        if not isinstance(pr_record, dict):
            continue
        key = str(pr_num)
        rn_ev = (release_note_evidence_by_pr or {}).get(key)
        reach_ev = (reachability_evidence_by_pr or {}).get(key)
        out[key] = analyze(pr_record, rn_ev, reach_ev)
    return out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli() -> int:
    """Read build-results JSON + optional evidence files, emit callsite-impact JSON.

    Usage:
      python3 callsite_impact.py [build-results.json] \\
          [--pr N] \\
          [--rn-evidence rn_by_pr.json] \\
          [--reach-evidence reach_by_pr.json]

    All evidence files must be keyed by PR number string at the top level.
    Pass '-' as the results file (or omit) to read build-results from stdin.
    """
    import argparse

    p = argparse.ArgumentParser(description="Callsite impact analyzer")
    p.add_argument(
        "results_file",
        nargs="?",
        default="-",
        help="Path to build-results JSON, or '-' for stdin (default: stdin)",
    )
    p.add_argument("--pr", default=None, help="Analyze only this PR number")
    p.add_argument(
        "--rn-evidence",
        default=None,
        metavar="FILE",
        help="Path to release-note evidence JSON (keyed by PR number)",
    )
    p.add_argument(
        "--reach-evidence",
        default=None,
        metavar="FILE",
        help="Path to pre-computed reachability evidence JSON (lite.py output, keyed by PR number)",
    )
    args = p.parse_args()

    if args.results_file == "-":
        raw = sys.stdin.read()
    else:
        with open(args.results_file) as fh:
            raw = fh.read()
    data = json.loads(raw)

    rn_ev_all: Optional[dict] = None
    if args.rn_evidence:
        with open(args.rn_evidence) as fh:
            rn_ev_all = json.loads(fh.read())

    reach_ev_all: Optional[dict] = None
    if args.reach_evidence:
        with open(args.reach_evidence) as fh:
            reach_ev_all = json.loads(fh.read())

    if args.pr:
        prs = data.get("prs") or {}
        pr_record = prs.get(str(args.pr)) or prs.get(args.pr)
        if pr_record is None:
            print(json.dumps({"error": f"PR {args.pr!r} not found"}), file=sys.stderr)
            return 1
        rn_ev = (rn_ev_all or {}).get(str(args.pr)) if rn_ev_all else None
        reach_ev = (reach_ev_all or {}).get(str(args.pr)) if reach_ev_all else None
        result = {str(args.pr): analyze(pr_record, rn_ev, reach_ev)}
    else:
        result = analyze_build_results(data, rn_ev_all, reach_ev_all)

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
