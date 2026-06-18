#!/usr/bin/env python3
"""M8 -- Changelog comprehension: changelog/release-note text -> structured
``breaking_claims[]``.

The deterministic layer (release_notes_evidence.py) answers "does this text smell
breaking?" at the PROSE level (BREAKING / POSSIBLE / NONE). It does NOT tell you
*which symbols* changed, which is what M9 reachability needs to ask "do we call
any of them?". M8 closes that gap: it turns the changelog into a list of typed,
per-symbol claims:

    {symbol, kind, old, new, severity, source}

It is **call-graph free** and runs in two tiers behind the unified ai_backend, so
the same module works offline (replay/deterministic) and live (Cursor/Copilot):

  Tier A (deterministic, stdlib-only, always runs):
    regex/marker extraction of breaking bullets + Go-identifier symbols. Zero cost,
    zero network. This is the floor and the fail-safe.

  Tier B (AI enrichment, optional, behind ai_backend namespace="changelog"):
    when enabled, asks the model to normalize messy prose into the same schema and
    fill old/new for renames/signature changes the regex missed. In replay mode this
    is a cassette lookup (deterministic, sub-second). Any failure/miss falls back to
    Tier A -- the AI can only ADD claims or enrich them, never erase a deterministic
    breaking claim (fail-safe: never silently clears a break).

Output is a dict ready to merge into a build-results PR record under ``changelog_m8``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import release_notes_evidence as rne  # reuse text collection + markers

try:
    import ai_backend
except Exception:  # pragma: no cover - ai layer optional
    ai_backend = None


# Claim kinds (typed so M9/policy can route).
KIND_REMOVED = "removed"
KIND_RENAMED = "renamed"
KIND_SIGNATURE = "signature_changed"
KIND_BEHAVIORAL = "behavioral"
KIND_DEPRECATED = "deprecated"

SEV_HIGH = "high"
SEV_MEDIUM = "medium"
SEV_LOW = "low"

# A Go-ish exported identifier, optionally qualified: Foo, pkg.Bar, Client.Do, T.Method
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,2}"
_BACKTICK = re.compile(r"`([^`]+)`")
_RENAME = re.compile(
    r"\b(?:renamed?|rename)\s+`?(" + _IDENT + r")`?\s+(?:to|->|=>|into)\s+`?(" + _IDENT + r")`?",
    re.IGNORECASE,
)
_RENAME_ISNOW = re.compile(
    r"`?(" + _IDENT + r")`?\s+(?:is|are|has been|was)\s+now\s+(?:called|named)\s+`?(" + _IDENT + r")`?",
    re.IGNORECASE,
)
_REMOVED = re.compile(
    r"\b(?:removed?|remove|deleted?|dropped?)\s+(?:the\s+|support\s+for\s+)?`?(" + _IDENT + r")`?",
    re.IGNORECASE,
)
_SIGNATURE = re.compile(
    r"\b(?:signature|param(?:eter)?s?|argument?s?|return(?:s|\s+type)?)\b[^`\n]*?`(" + _IDENT + r")`",
    re.IGNORECASE,
)
_DEPRECATED = re.compile(
    r"\b(?:deprecat\w+)\s+`?(" + _IDENT + r")`?",
    re.IGNORECASE,
)

# Words that are not real symbols even if they pattern-match (reduce false symbols).
_STOP = {
    "the", "this", "that", "now", "for", "and", "with", "from", "support", "api",
    "all", "use", "using", "via", "see", "note", "changes", "change", "breaking",
    "go", "golang", "version", "release", "you", "your", "is", "are", "to", "in",
}


def _clean_symbol(tok: str) -> str:
    """Normalize an extracted token to a bare identifier path (drop call parens,
    surrounding punctuation) so downstream reachability can match it."""
    tok = tok.strip().strip("`'\"")
    tok = re.sub(r"\(\s*\)$", "", tok)        # Search() -> Search
    tok = tok.strip(".,;:!? ")
    return tok


def _looks_like_symbol(tok: str) -> bool:
    if not tok:
        return False
    base = tok.split(".")[-1]
    if base.lower() in _STOP or tok.lower() in _STOP:
        return False
    # Exported Go symbols start uppercase, or it's a qualified path (has a dot).
    if "." in tok:
        return True
    return base[:1].isupper() and len(base) >= 2


def _split_bullets(bullets: List[str], prose: str) -> List[str]:
    """One scannable line per candidate claim."""
    lines: List[str] = list(bullets)
    for ln in re.split(r"[\n\r]+|(?<=[.;])\s+|\u2022|^\s*[-*]\s+", prose, flags=re.MULTILINE):
        ln = ln.strip(" \t-*\u2022")
        if ln:
            lines.append(ln)
    # de-dup, preserve order
    seen, out = set(), []
    for ln in lines:
        k = ln.lower().strip()
        if k and k not in seen:
            seen.add(k)
            out.append(ln.strip())
    return out


def _claim(symbol, kind, severity, source, old="", new=""):
    return {
        "symbol": symbol,
        "kind": kind,
        "old": old,
        "new": new,
        "severity": severity,
        "source": source[:240],
    }


def extract_deterministic(bullets: List[str], prose: str) -> List[Dict[str, Any]]:
    """Tier A: regex/marker extraction. Conservative -- a line that is clearly
    breaking but yields no clean symbol still produces ONE behavioral claim so the
    break is never dropped."""
    claims: List[Dict[str, Any]] = []
    seen = set()

    def add(symbol, kind, severity, source, old="", new=""):
        symbol = _clean_symbol(symbol)
        old = _clean_symbol(old) if old else old
        new = _clean_symbol(new) if new else new
        if not symbol:
            return
        key = (symbol, kind)
        if key in seen:
            return
        seen.add(key)
        claims.append(_claim(symbol, kind, severity, source, old, new))

    for line in _split_bullets(bullets, prose):
        norm = rne._norm(line)
        is_breaking = bool(rne._hits(norm, rne.BREAKING_MARKERS))
        is_possible = bool(rne._hits(norm, rne.POSSIBLE_MARKERS)) or ("deprecat" in norm)
        if not (is_breaking or is_possible):
            continue
        sev = SEV_HIGH if is_breaking else SEV_MEDIUM

        matched = False
        for m in _RENAME.finditer(line):
            old, new = m.group(1), m.group(2)
            if _looks_like_symbol(old):
                add(old, KIND_RENAMED, sev, line, old=old, new=new); matched = True
        for m in _RENAME_ISNOW.finditer(line):
            old, new = m.group(1), m.group(2)
            if _looks_like_symbol(old):
                add(old, KIND_RENAMED, sev, line, old=old, new=new); matched = True
        for m in _REMOVED.finditer(line):
            sym = m.group(1)
            if _looks_like_symbol(sym):
                add(sym, KIND_REMOVED, sev, line, old=sym); matched = True
        for m in _DEPRECATED.finditer(line):
            sym = m.group(1)
            if _looks_like_symbol(sym):
                add(sym, KIND_DEPRECATED, SEV_LOW if not is_breaking else SEV_MEDIUM, line, old=sym)
                matched = True
        for m in _SIGNATURE.finditer(line):
            sym = m.group(1)
            if _looks_like_symbol(sym):
                add(sym, KIND_SIGNATURE, sev, line); matched = True

        if not matched:
            # Pull any backticked identifiers as behavioral claims so the break isn't lost.
            for bt in _BACKTICK.findall(line):
                bt = bt.strip()
                if _looks_like_symbol(bt):
                    add(bt, KIND_BEHAVIORAL, sev, line); matched = True
        if not matched and is_breaking:
            # Breaking text with no extractable symbol -> one anonymous behavioral claim.
            add("(unspecified)", KIND_BEHAVIORAL, SEV_HIGH, line)

    return claims


_AI_PROMPT = """You extract structured breaking-change claims from a dependency changelog or release notes.
Return ONLY a JSON array. Each element: {"symbol","kind","old","new","severity","source"}.
kind in [removed,renamed,signature_changed,behavioral,deprecated]; severity in [high,medium,low].
Only include claims supported by the text. If none, return []. Do not invent symbols.

CHANGELOG:
%s
"""


def _parse_ai_array(raw: str) -> Optional[List[Dict[str, Any]]]:
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    if "```" in s:
        parts = s.split("```")
        s = max(parts, key=len)
        s = s.lstrip("json").strip()
    start, end = s.find("["), s.rfind("]")
    if start < 0 or end < 0:
        return None
    try:
        arr = json.loads(s[start:end + 1])
    except ValueError:
        return None
    if not isinstance(arr, list):
        return None
    out = []
    for e in arr:
        if not isinstance(e, dict) or not e.get("symbol"):
            continue
        out.append(_claim(
            str(e.get("symbol")),
            e.get("kind", KIND_BEHAVIORAL),
            e.get("severity", SEV_MEDIUM),
            str(e.get("source", "")),
            old=str(e.get("old", "")),
            new=str(e.get("new", "")),
        ))
    return out


def enrich_with_ai(prose: str, pr_id: str, det_claims: List[Dict[str, Any]],
                   enable: bool) -> List[Dict[str, Any]]:
    """Tier B: merge AI-extracted claims ON TOP of deterministic ones. The AI may
    ADD or fill old/new, never delete a deterministic breaking claim (fail-safe)."""
    if not enable or ai_backend is None or not prose.strip():
        return det_claims
    raw = ai_backend.invoke(_AI_PROMPT % prose[:6000],
                            namespace="changelog", key="pr-%s" % pr_id)
    ai_claims = _parse_ai_array(raw)
    if not ai_claims:
        return det_claims

    merged = {(c["symbol"], c["kind"]): c for c in det_claims}
    for c in ai_claims:
        key = (c["symbol"], c["kind"])
        if key in merged:
            # enrich missing old/new only; keep deterministic severity floor
            if not merged[key].get("old") and c.get("old"):
                merged[key]["old"] = c["old"]
            if not merged[key].get("new") and c.get("new"):
                merged[key]["new"] = c["new"]
        else:
            merged[key] = c
    return list(merged.values())


def comprehend(pr: Dict[str, Any], pr_id: str = "", use_ai: Optional[bool] = None) -> Dict[str, Any]:
    """Top-level: PR record -> structured changelog comprehension."""
    if use_ai is None:
        use_ai = os.environ.get("M8_USE_AI", "0") not in ("0", "", "false", "no")
    bullets, prose = rne._collect_text(pr)
    available = bool(prose.strip())
    det = extract_deterministic(bullets, prose)
    claims = enrich_with_ai(prose, pr_id or str(pr.get("number") or pr.get("pr_id") or ""),
                            det, use_ai)
    severities = [c["severity"] for c in claims]
    top = SEV_HIGH if SEV_HIGH in severities else (SEV_MEDIUM if SEV_MEDIUM in severities
                                                   else (SEV_LOW if severities else "none"))
    return {
        "available": available,
        "breaking_claims": claims,
        "claim_count": len(claims),
        "max_severity": top,
        "tier": "ai+det" if use_ai else "det",
    }


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="M8 changelog comprehension")
    ap.add_argument("results", help="build-results.json")
    ap.add_argument("--pr", default=None, help="only this PR id")
    ap.add_argument("--ai", action="store_true", help="enable AI enrichment (Tier B)")
    ap.add_argument("--write", action="store_true", help="write changelog_m8 back into results")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    data = json.load(open(args.results))
    prs = data.get("prs", {})
    if isinstance(prs, list):
        prs = {str(p.get("pr_id") or p.get("number")): p for p in prs}

    out = {}
    for pid, pr in prs.items():
        if args.pr and str(args.pr) != str(pid):
            continue
        res = comprehend(pr, pr_id=pid, use_ai=args.ai)
        out[pid] = res
        if args.write:
            pr["changelog_m8"] = res

    if args.write:
        dst = args.out or args.results
        json.dump(data, open(dst, "w"), indent=2)
        print("wrote changelog_m8 for %d PR(s) -> %s" % (len(out), dst), file=sys.stderr)
    else:
        print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
