#!/usr/bin/env python3
"""Release-note evidence agent (stdlib-only, no network required for MVP).

Extracts structured, contract-aligned evidence from the fields already present
in a build-results PR record.  The output is an ``EvidenceRecord`` with
``name=SignalName.RELEASE_NOTES`` that plugs directly into ``EvidenceBundle``
and the ``decide()`` function in ``evidence_contract.py``.

Classification hierarchy (conservative — never silently clear uncertain text):

  BREAKING_CHANGE    explicit incompatible/migration/breaking framing
  POSSIBLE_CHANGE    behavioral/removed/renamed signals, or call-observable change
  NO_RELEVANT_CHANGE only bugfix/doc/internal text, zero behavioral signals
  UNAVAILABLE        no usable text at all

Mapping to EvidenceRecord:
  NO_RELEVANT_CHANGE -> status=PASS,        relevant=False,  severity=NONE
  POSSIBLE_CHANGE    -> status=UNKNOWN,     relevant=True,   severity=LOW
  BREAKING_CHANGE    -> status=FAIL,        relevant=True,   severity=HIGH,  residual_risk=HIGH
  UNAVAILABLE        -> status=UNAVAILABLE, relevant=None

Prompt-injection guard:
  Classification is performed on normalised lowercase token matches against
  maintainer-authored keyword lists.  Words like "MERGE", "FIX", "SAFE",
  "verdict", or any instruction-like prose from the release notes CANNOT
  influence the classification outcome: only the keyword tables below do.

Plug-in callsite (policy lowering):
  Build a full EvidenceBundle and call decide():

    from release_notes_evidence import analyse_pr
    from evidence_contract import EvidenceBundle, decide

    rn_record = analyse_pr(pr_dict)
    bundle = EvidenceBundle(
        package=pr_dict["package"], ecosystem=pr_dict["ecosystem"],
        from_version=pr_dict["from"], to_version=pr_dict["to"],
        signals={
            SignalName.BUILD: ..., SignalName.TEST: ..., SignalName.API_DIFF: ...,
            SignalName.RELEASE_NOTES: rn_record,
        },
    )
    verdict = decide(bundle)   # REVIEW -> MERGE when rn_record is PASS + not relevant

CLI (optional):
  python3 release_notes_evidence.py build-results.json <pr_number>
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Contract imports (same directory).  Keep the try/except so the module can
# be unit-tested standalone even if evidence_contract hasn't been imported yet.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from evidence_contract import (  # noqa: E402
    Citation,
    Confidence,
    EvidenceRecord,
    SafetySeverity,
    SignalName,
    SignalStatus,
)

# ---------------------------------------------------------------------------
# Internal classification enum (not part of the public contract)
# ---------------------------------------------------------------------------

class _RNClass:
    NO_RELEVANT_CHANGE = "no_relevant_change"
    POSSIBLE_CHANGE    = "possible_change"
    BREAKING_CHANGE    = "breaking_change"
    UNAVAILABLE        = "unavailable"


# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

# Phrases that unambiguously declare a breaking / incompatible change.
# Any single hit here → BREAKING_CHANGE (highest priority).
BREAKING_MARKERS: List[str] = [
    "breaking change", "breaking:", "!breaking", "[breaking]",
    "⚠ breaking", "⚠️ breaking",
    "incompatible change", "backward incompatible", "backward-incompatible",
    "not backward compatible", "backwards incompatible",
    "migration required", "migration guide", "requires migration",
    "major breaking", "api break", "api breaking",
    "breaks existing", "will break", "this breaks",
    "dropped support for", "removed support for",
    "no longer supported",
]

# Phrases that signal a behavioral / structural change but stop short of an
# explicit "breaking" declaration.  These give POSSIBLE_CHANGE.
POSSIBLE_MARKERS: List[str] = [
    # explicit behavioral change language
    "behavior changed", "behaviour changed",
    "behavior has changed", "behaviour has changed",
    "behavioral change", "behavioural change",
    # removal / rename
    "removed", "has been removed", "no longer available",
    "renamed", "was renamed",
    "deprecated and removed", "previously deprecated",
    # call-observable signal (reused from break_class_router vocabulary)
    "now returns", "returns an error", "returns a", "return value",
    "no longer returns", "returns nil", "returns empty",
    "signature changed", "now takes", "added a parameter",
    "removed the parameter", "removed parameter",
    "output format changed", "format changed",
    "serialization changed", "serialisation changed",
    "encoding changed",
    "now rejects", "now raises", "now throws", "now panics",
    "validation error", "now validates",
    "default changed", "changed the default", "default is now",
    "new default",
]

# Phrases that indicate a clean, low-risk release (bugfix / docs / internal).
# Hits here are considered only when NO behavioral / breaking markers are present.
CLEAN_MARKERS: List[str] = [
    "bug fix", "bugfix", "bug-fix",
    "fixes a bug", "fix for", "fixes #", "fixes gh-", "fix:",
    "security fix", "security patch", "cve fix", "cve-",
    "documentation", "docs:", "doc fix", "readme",
    "internal refactor", "refactoring", "internal cleanup",
    "code cleanup", "no api change", "no behavior change", "no behaviour change",
    "no functional change", "no-op", "no observable change",
    "test only", "tests only", "only tests",
    "typo", "style fix", "lint",
    "dependency update", "bump version", "chore:",
]

# Thin text that should NOT be treated as meaningful evidence on its own.
_THIN_PATTERNS = [
    re.compile(r"^v?\d+\.\d+[\.\d]*$"),             # bare version string
    re.compile(r"^release\s+v?\d+[\.\d]*$", re.I),  # "Release 1.2.3"
    re.compile(r"^no\s+changelog\s*(entry)?$", re.I),
    re.compile(r"^changelog\s+(unavailable|not available)$", re.I),
    re.compile(r"^see\s+(github|releases|commits)$", re.I),
]

_MIN_TOKENS = 5  # fewer tokens than this → treat as thin / no-signal text

_COMPAT_ONLY_MARKERS: List[str] = [
    "made to be compatible with",
    "made to be compatible",
]

_OWN_BREAK_MARKERS: List[str] = [
    "breaking change:",
    "removed ",
    "removes ",
    "dropped support",
    "no longer supports",
    "migration required",
    "requires migration",
]

_INSTRUCTION_MARKERS: List[str] = [
    "ignore prior instruction",
    "ignore previous instruction",
    "ignore all rules",
    "system override",
    "auto-approve",
    "safe_to_merge",
    "verdict:",
    "status:",
    "relevant:",
    "action=",
    "grade this",
    "please merge",
    "do not flag",
]


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _safe_str(value: Any) -> str:
    """Coerce a value to a string safely; return '' for None/falsy."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(_safe_str(v) for v in value)
    if isinstance(value, dict):
        return " ".join(_safe_str(v) for v in value.values())
    return str(value)


def _extract_bullets(pr: Dict[str, Any]) -> List[str]:
    det = pr.get("deterministic") or {}
    sig = det.get("changelogSignal") or {}
    raw = sig.get("bullets") or []
    return [b for b in raw if isinstance(b, str) and b.strip()]


def _extract_changelog_text(pr: Dict[str, Any]) -> str:
    det = pr.get("deterministic") or {}
    return _safe_str(det.get("changelogText") or "")


def _extract_release_notes_field(pr: Dict[str, Any]) -> str:
    """Check top-level ``release_notes`` field (string or nested dict)."""
    rn = pr.get("release_notes")
    if not rn:
        return ""
    if isinstance(rn, str):
        return rn
    if isinstance(rn, dict):
        # Accept common sub-keys.
        for key in ("body", "text", "notes", "changelog", "description"):
            v = rn.get(key)
            if isinstance(v, str) and v.strip():
                return v
        return _safe_str(rn)
    return ""


def _collect_text(pr: Dict[str, Any]) -> Tuple[List[str], str]:
    """Return (bullets, combined_prose) from all available fields."""
    bullets = _extract_bullets(pr)
    parts: List[str] = list(bullets)
    for getter in (_extract_changelog_text, _extract_release_notes_field):
        t = getter(pr)
        if t:
            parts.append(t)
    return bullets, " ".join(parts)


# ---------------------------------------------------------------------------
# Classification helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def _hits(text: str, markers: List[str]) -> List[str]:
    return [m for m in markers if m in text]


def _is_thin(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped.split()) < _MIN_TOKENS:
        for pat in _THIN_PATTERNS:
            if pat.match(stripped):
                return True
    return False


def _first_matching_snippet(raw_text: str, markers: List[str], window: int = 120) -> str:
    """Return a short snippet of raw text surrounding the first keyword match."""
    norm = _norm(raw_text)
    for m in markers:
        idx = norm.find(m)
        if idx >= 0:
            start = max(0, idx - 20)
            end = min(len(raw_text), idx + window)
            snippet = raw_text[start:end].strip()
            return snippet[:200]
    return ""


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _classify(bullets: List[str], prose: str) -> Tuple[str, List[str], str]:
    """
    Return (rn_class, matched_markers, snippet).

    Never reads verdict / action words from prose — classification is keyword-
    table driven only.  Prompt-injected phrases like "MERGE this" or "status=PASS"
    in the release notes body cannot influence this function's output.
    """
    # Build the combined search corpus from both the deduplicated bullets and
    # prose; bullets are always included so direct bullet-only callers work.
    combined_parts = list(bullets) + ([prose] if prose else [])
    combined = " ".join(combined_parts)

    if _is_thin(combined) and not bullets:
        return _RNClass.UNAVAILABLE, [], ""

    # Use `combined` for marker lookups; use `prose` (may be richer) for snippets.
    norm_prose = _norm(combined)

    breaking = _hits(norm_prose, BREAKING_MARKERS)
    if breaking:
        clean = _hits(norm_prose, CLEAN_MARKERS)
        own_break = any(marker in norm_prose for marker in _OWN_BREAK_MARKERS)
        if clean and not own_break and any(marker in norm_prose for marker in _COMPAT_ONLY_MARKERS):
            snippet = _first_matching_snippet(combined, clean)
            return _RNClass.NO_RELEVANT_CHANGE, clean, snippet
        snippet = _first_matching_snippet(combined, breaking)
        return _RNClass.BREAKING_CHANGE, breaking, snippet

    possible = _hits(norm_prose, POSSIBLE_MARKERS)
    clean = _hits(norm_prose, CLEAN_MARKERS)
    instruction = _hits(norm_prose, _INSTRUCTION_MARKERS)

    if possible:
        snippet = _first_matching_snippet(combined, possible)
        return _RNClass.POSSIBLE_CHANGE, possible, snippet

    if instruction:
        return _RNClass.UNAVAILABLE, [], ""

    if clean:
        snippet = _first_matching_snippet(combined, clean)
        return _RNClass.NO_RELEVANT_CHANGE, clean, snippet

    # Text present but no recognisable signal.
    return _RNClass.UNAVAILABLE, [], ""


# ---------------------------------------------------------------------------
# EvidenceRecord builder
# ---------------------------------------------------------------------------

_CLASS_TO_STATUS: Dict[str, SignalStatus] = {
    _RNClass.NO_RELEVANT_CHANGE: SignalStatus.PASS,
    _RNClass.POSSIBLE_CHANGE:    SignalStatus.UNKNOWN,
    _RNClass.BREAKING_CHANGE:    SignalStatus.FAIL,
    _RNClass.UNAVAILABLE:        SignalStatus.UNAVAILABLE,
}

_CLASS_TO_SEVERITY: Dict[str, SafetySeverity] = {
    _RNClass.NO_RELEVANT_CHANGE: SafetySeverity.NONE,
    _RNClass.POSSIBLE_CHANGE:    SafetySeverity.LOW,
    _RNClass.BREAKING_CHANGE:    SafetySeverity.HIGH,
    _RNClass.UNAVAILABLE:        SafetySeverity.NONE,
}

_CLASS_TO_RESIDUAL: Dict[str, SafetySeverity] = {
    _RNClass.NO_RELEVANT_CHANGE: SafetySeverity.NONE,
    _RNClass.POSSIBLE_CHANGE:    SafetySeverity.LOW,
    _RNClass.BREAKING_CHANGE:    SafetySeverity.HIGH,
    _RNClass.UNAVAILABLE:        SafetySeverity.MEDIUM,
}

_CLASS_TO_RELEVANT: Dict[str, Optional[bool]] = {
    _RNClass.NO_RELEVANT_CHANGE: False,
    _RNClass.POSSIBLE_CHANGE:    True,
    _RNClass.BREAKING_CHANGE:    True,
    _RNClass.UNAVAILABLE:        None,
}

_CLASS_TO_CONFIDENCE: Dict[str, Confidence] = {
    _RNClass.NO_RELEVANT_CHANGE: Confidence.HIGH,
    _RNClass.POSSIBLE_CHANGE:    Confidence.MEDIUM,
    _RNClass.BREAKING_CHANGE:    Confidence.HIGH,
    _RNClass.UNAVAILABLE:        Confidence.LOW,
}


def _build_record(
    rn_class: str,
    matched: List[str],
    snippet: str,
    source_desc: str,
    bullets: List[str],
) -> EvidenceRecord:
    citations: List[Citation] = []
    if snippet:
        citations.append(Citation(
            source=source_desc,
            locator="release_notes_text",
            text=snippet[:200],
        ))
    for b in bullets[:3]:
        citations.append(Citation(
            source=source_desc,
            locator="changelog_bullet",
            text=b[:200],
        ))

    rationale = (
        f"release-notes classification: {rn_class}; "
        f"matched markers: {matched[:5]}; "
        f"sources: {source_desc}"
    )

    return EvidenceRecord(
        name=SignalName.RELEASE_NOTES,
        status=_CLASS_TO_STATUS[rn_class],
        severity=_CLASS_TO_SEVERITY[rn_class],
        relevant=_CLASS_TO_RELEVANT[rn_class],
        residual_risk=_CLASS_TO_RESIDUAL[rn_class],
        confidence=_CLASS_TO_CONFIDENCE[rn_class],
        citations=tuple(citations),
        rationale=rationale,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyse_pr(pr: Dict[str, Any]) -> EvidenceRecord:
    """Analyse a single PR record dict and return an EvidenceRecord.

    Args:
        pr: A PR record from build-results JSON.  Expected fields (all optional):
            - deterministic.changelogSignal.bullets  (list[str])
            - deterministic.changelogText            (str)
            - release_notes                          (str | dict)
            - package, ecosystem, from, to           (str — for rationale only)

    Returns:
        EvidenceRecord with name=SignalName.RELEASE_NOTES.
    """
    bullets, prose = _collect_text(pr)

    # Build a human-readable source descriptor (for citations).
    sources: List[str] = []
    if bullets:
        sources.append(f"changelogSignal.bullets({len(bullets)})")
    if _extract_changelog_text(pr):
        sources.append("changelogText")
    if _extract_release_notes_field(pr):
        sources.append("release_notes")
    source_desc = ", ".join(sources) if sources else "none"

    rn_class, matched, snippet = _classify(bullets, prose)
    return _build_record(rn_class, matched, snippet, source_desc, bullets)


def analyse_build_results(data: Any, pr_number: int) -> Optional[Dict[str, Any]]:
    """Find PR ``pr_number`` in a build-results structure and analyse it.

    Returns the EvidenceRecord serialised as a dict, or None if not found.
    """
    prs_section = None
    if isinstance(data, dict):
        for key in ("prs", "pr_results", "results"):
            val = data.get(key)
            if isinstance(val, dict):
                prs_section = val
                break
        if prs_section is None and all(
            isinstance(v, dict) for v in data.values()
        ):
            prs_section = data

    if isinstance(prs_section, dict):
        pr = prs_section.get(str(pr_number)) or prs_section.get(pr_number)
        if pr:
            return analyse_pr(pr).to_dict()

    # Flat list variant.
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            for key in ("pr", "pr_number", "number"):
                if item.get(key) == pr_number or item.get(key) == str(pr_number):
                    return analyse_pr(item).to_dict()

    return None


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _cli(argv: Sequence[str]) -> int:
    if len(argv) < 3:
        print(
            "Usage: release_notes_evidence.py <build-results.json> <pr_number>",
            file=sys.stderr,
        )
        return 2
    path, pr_arg = argv[1], argv[2]
    try:
        pr_number = int(pr_arg)
    except ValueError:
        print(f"Invalid PR number: {pr_arg!r}", file=sys.stderr)
        return 2
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Error reading {path}: {exc}", file=sys.stderr)
        return 1

    result = analyse_build_results(data, pr_number)
    if result is None:
        print(f"PR #{pr_number} not found in {path}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
