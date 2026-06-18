"""Reachability adjudication — the "is the changed symbol one I actually call?" layer.

This is the deterministic core of the agent-review layer. For a PR flagged
``review:break-reachable-api`` (a breaking dependency API surface on a package the
code imports), it answers the first question a human reviewer asks:

    "Do I actually call any of the symbols that changed?"

It cross-references the breaking changed symbols (from ``deterministic.api_changes_detail``)
against the symbols actually called in production code (from ``callsite_impact.callsites``)
and returns one of three verdicts:

* ``REACHED_RELEVANT`` — a changed breaking symbol IS called. Keep the review and cite
  the exact symbol + call site. This is a genuine "look at this" for a human.
* ``NOT_REACHED`` — the package is imported but NONE of the changed symbols are reached
  (reachability is positively ABSENT). Safe to downgrade to an optional glance.
* ``NEEDS_AGENT`` — symbol matching is inconclusive (no call sites resolved AND
  reachability is uncertain). A deterministic answer would risk a false-green, so this
  emits a *bounded* task for the AI agent: grep the repo for the exact changed symbols.

SAFETY: a PR that is security-sensitive, fixes/introduces a CVE, fails to build, or
introduces a new vulnerability is ``safety_locked`` — the adjudicator will NEVER recommend
lowering it, regardless of reachability. Lowering decisions are advisory; callers decide
whether to consume them, and must honour ``safety_locked``.

The module is ecosystem-agnostic in shape; symbol extraction is currently tuned for Go
(receiver-qualified names like ``Type.Method`` and ``e.Method``) but degrades gracefully.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

# Breaking change kinds — kept in sync with policy_lowering._has_breaking_api_change.
_BREAKING_KINDS = {
    "removed",
    "deleted",
    "type_changed",
    "return_type_changed",
    "signature_changed",
    "parameter_removed",
    "parameter_type_changed",
    "required_parameter_added",
    "incompatible",
}


def _norm_symbol(raw: Any) -> Optional[str]:
    """Normalise a symbol name to its matchable identifier.

    ``e.SearchV2JQL`` -> ``SearchV2JQL`` (strip a lowercase receiver var)
    ``Int2.ScanInt64`` -> ``ScanInt64`` keep the method; also keep ``Int2.ScanInt64``
    ``github.com/oklog/ulid.ULID.Compare`` -> ``Compare``
    ``package github.com/lib/pq/cmd/pqlisten`` -> None (not a callable symbol)
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or s.startswith("package "):
        return None
    # The last dotted component is the most specific callable identifier.
    tail = s.split(".")[-1].strip()
    if not tail or not tail[0].isalpha():
        return None
    return tail


def _symbol_variants(raw: Any) -> List[str]:
    """All matchable forms of a symbol: the full receiver-qualified name and the bare tail."""
    out: List[str] = []
    if not isinstance(raw, str):
        return out
    s = raw.strip()
    if not s or s.startswith("package "):
        return out
    parts = [p for p in s.split(".") if p]
    tail = _norm_symbol(s)
    if tail:
        out.append(tail)
    # Type.Method form (last two components) helps disambiguate method receivers.
    if len(parts) >= 2:
        recv = parts[-2]
        # Skip lowercase receiver-variable prefixes (e.g. "e", "s", "n").
        if recv[:1].isupper():
            out.append(f"{recv}.{parts[-1]}")
    return out


def _breaking_changed_symbols(api_changes_detail: Any) -> List[str]:
    if not isinstance(api_changes_detail, Sequence):
        return []
    out: List[str] = []
    seen = set()
    for item in api_changes_detail:
        if not isinstance(item, Mapping):
            continue
        kind = str(item.get("kind") or item.get("changeType") or "").lower()
        hard = bool(item.get("isHardBreak"))
        if kind not in _BREAKING_KINDS and not hard:
            continue
        sym = item.get("symbol") or item.get("name")
        norm = _norm_symbol(sym)
        if norm and sym not in seen:
            seen.add(sym)
            out.append(str(sym))
    return out


def _called_symbols(callsite_impact: Mapping[str, Any]) -> List[str]:
    calls = callsite_impact.get("callsites") if isinstance(callsite_impact, Mapping) else None
    out: List[str] = []
    if not isinstance(calls, Sequence):
        return out
    for c in calls:
        if isinstance(c, Mapping) and not c.get("is_test"):
            sym = c.get("symbol")
            if isinstance(sym, str) and sym:
                out.append(sym)
    return out


def _is_safety_locked(pr: Mapping[str, Any]) -> bool:
    if bool(pr.get("cves")):
        return True
    if bool(pr.get("vuln_new_findings")):
        return True
    if bool(pr.get("security_sensitive")):
        return True
    if str(pr.get("ci_tier") or "") == "secsens":
        return True
    build = pr.get("build") if isinstance(pr.get("build"), Mapping) else {}
    if str(build.get("verdict") or "").lower() in {"fail", "pre_existing_plus_new"}:
        return True
    return False


def _reachability_absent(callsite_impact: Mapping[str, Any], reachability: Mapping[str, Any]) -> bool:
    """True only when reachability is POSITIVELY absent (not merely unresolved)."""
    if isinstance(callsite_impact, Mapping):
        if str(callsite_impact.get("impact") or "").upper() == "NOT_REACHED":
            return True
        if str(callsite_impact.get("reachability_verdict") or "").upper() == "ABSENT":
            return True
    if isinstance(reachability, Mapping):
        if str(reachability.get("verdict") or reachability.get("status") or "").upper() == "ABSENT":
            return True
        # Imported nowhere in production code.
        if reachability.get("checked") and not reachability.get("import_sites") and not reachability.get("callsites"):
            return True
    return False


def adjudicate_reachability(
    pr: Mapping[str, Any],
    callsite_impact: Optional[Mapping[str, Any]] = None,
    reachability: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a reachability adjudication for a break-reachable-API PR.

    See module docstring for verdict semantics. Always safe to call; returns
    ``verdict='NEEDS_AGENT'`` when it cannot decide.
    """
    callsite_impact = callsite_impact if isinstance(callsite_impact, Mapping) else {}
    reachability = reachability if isinstance(reachability, Mapping) else {}
    det = pr.get("deterministic") if isinstance(pr.get("deterministic"), Mapping) else {}

    changed = _breaking_changed_symbols(det.get("api_changes_detail"))
    called = _called_symbols(callsite_impact)
    safety_locked = _is_safety_locked(pr)

    # Build a match set from called symbols' variants.
    called_variants = set()
    for c in called:
        called_variants.update(_symbol_variants(c))

    matched: List[str] = []
    for ch in changed:
        variants = set(_symbol_variants(ch))
        if variants & called_variants:
            matched.append(ch)

    base = {
        "changed_symbols": changed,
        "called_symbols": sorted(set(called)),
        "matched_symbols": matched,
        "safety_locked": safety_locked,
        "agent_task": None,
    }

    if not changed:
        # No breaking symbols to reason about — defer to the upstream verdict.
        base.update(verdict="NEEDS_AGENT", manual_review_required=True,
                    citation="no breaking changed symbols were extracted; defer to upstream evidence")
        return base

    if matched:
        syms = ", ".join(f"`{m}`" for m in matched[:6])
        base.update(
            verdict="REACHED_RELEVANT",
            manual_review_required=True,
            recommend_lower=False,
            citation=(f"your production code calls {syms}, which the upstream API diff marks as a "
                      f"breaking change — review the call site(s) against the new signature/behaviour"),
        )
        return base

    if _reachability_absent(callsite_impact, reachability):
        base.update(
            verdict="NOT_REACHED",
            manual_review_required=False,
            recommend_lower=not safety_locked,
            citation=("none of the breaking changed symbols are reached by production code "
                      f"(checked {len(changed)} changed symbol(s)); optional glance only"),
        )
        return base

    # Inconclusive: no resolved call sites and reachability not positively absent.
    # Scope a precise task for the AI agent rather than guessing (no false-green).
    task_syms = ", ".join(changed[:12])
    base.update(
        verdict="NEEDS_AGENT",
        manual_review_required=True,
        recommend_lower=False,
        citation=("symbol-level reachability is unresolved; an agent must confirm whether any "
                  "changed symbol is called in production code"),
        agent_task=(
            f"Package `{pr.get('package','?')}` has a breaking API change. Grep the repository "
            f"(excluding *_test.go) for any use of these changed symbols: {task_syms}. "
            "Report each production call site (file:line) or state definitively that none exist."
        ),
    )
    return base


__all__ = ["adjudicate_reachability"]
