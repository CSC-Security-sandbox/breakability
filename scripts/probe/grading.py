"""Proof-contract parsing and conservative grade derivation."""
import json
import re
import time

from .config import AGENT_CMD

__all__ = [
    "parse_contract", "derive_grade", "build_grade_from_contract",
    "derive_reasoning_grade", "build_reasoning_grade",
    # Private names exported for tests (test_differential_probe_provenance.py)
    "_as_bool", "_observed_output_is_real", "_evidence_grounded_in_sources",
    "_TRIVIAL_OUTPUT", "_MIN_OBSERVED_LEN",
    "_derive_grade_raw", "_derive_reasoning_grade_raw",
]


# ── proof-contract parsing + conservative grade derivation ──────────────────
def parse_contract(out_path):
    try:
        raw = open(out_path).read().strip()
    except Exception:
        return None
    if raw.startswith("```"):
        raw = raw.strip("`")
        i = raw.find("{")
        raw = raw[i:] if i >= 0 else raw
        j = raw.rfind("}")
        raw = raw[: j + 1] if j >= 0 else raw
    try:
        obj = json.loads(raw)
    except Exception as e:
        from .utils import log
        log(f"invalid contract json: {e}")
        return None
    return obj if isinstance(obj, dict) else None


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "yes"):
            return True
        if s in ("false", "no"):
            return False
    return None  # unknown / "unclear"


# ── provenance helpers ──────────────────────────────────────────────────────
# Strings the agent might write as placeholder "observed" values that do NOT
# constitute real probe output. Checked case-insensitively.
_TRIVIAL_OUTPUT = frozenset({
    "", "none", "n/a", "null", "undefined", "unknown", "false", "true",
    "0", "1", "[]", "{}", '""', "''", "pass", "ok", "error", "(none)", "n/a.",
    "no output", "no change", "-", "--",
})
# Minimum bytes for a non-trivial observed value.
_MIN_OBSERVED_LEN = 4


def _observed_output_is_real(ofrom: str, oto: str) -> bool:
    """Both observed_from/to are non-trivial, indicating the probe actually ran
    and captured concrete output rather than filling in placeholder text."""
    f = ofrom.strip()
    t = oto.strip()
    return (
        len(f) >= _MIN_OBSERVED_LEN
        and len(t) >= _MIN_OBSERVED_LEN
        and f.lower() not in _TRIVIAL_OUTPUT
        and t.lower() not in _TRIVIAL_OUTPUT
    )


def _evidence_grounded_in_sources(evidence_text: str, source_context) -> bool:
    """Return True iff evidence_text contains at least one verifiable anchor
    (a token of >=10 characters) drawn from the supplied changelog/bullet/callsite
    inputs that were fed to the agent.

    Conservative by design: if all source texts are too generic (no 10-char
    tokens), or if source_context is absent, this returns False (-> Medium floor).
    AI-authored prose that does not quote from the actual supplied inputs fails
    the check regardless of length.
    """
    if not source_context or not evidence_text:
        return False
    ev = evidence_text.lower()
    # Changelog text and bullet: shared technical tokens are reliable anchors.
    for key in ("bullet", "changelog_text"):
        src = (source_context.get(key) or "").strip().lower()
        for token in re.findall(r"[a-z0-9_/.\-]{10,}", src):
            if token in ev:
                return True
    # Call-site: the file path or symbol name are stable identifiers.
    cs = source_context.get("call_site") or {}
    for anchor_key in ("file", "symbol"):
        anchor = (cs.get(anchor_key) or "").strip()
        if len(anchor) >= 6 and anchor.lower() in ev:
            return True
    return False


def derive_grade(c, source_context=None):
    grade, reason = _derive_grade_raw(c)
    # PROOF FLOOR: the AI may only LOWER risk with grounded proof.
    # A probe-derived low/none must be backed by:
    #   (a) real observed from->to values (both non-trivial -- the probe ran), OR
    #   (b) evidence text containing a verifiable anchor from the supplied
    #       changelog/bullet/callsite inputs (not arbitrary prose).
    # Length of invented prose is NOT sufficient -- floored back to Medium.
    if grade in ("low", "none"):
        ofrom = str(c.get("observed_from", "")).strip()
        oto = str(c.get("observed_to", "")).strip()
        evidence = str(c.get("evidence", "")).strip()
        if not (_observed_output_is_real(ofrom, oto)
                or _evidence_grounded_in_sources(evidence, source_context)):
            return "medium", (
                "probe lowered risk but evidence lacked provenance: "
                "observed from/to were absent or trivial, and evidence contained "
                "no verifiable anchor from the supplied changelog/bullet/callsite; "
                "floored to Medium (no false-green)"
            )
    return grade, reason


def _derive_grade_raw(c):
    """Driver-owned conservative floors. Returns (grade, reason).

    Base for any declared-behavioral residual is MEDIUM. We only move OFF Medium with
    real evidence:
      - HIGH: trigger exercised, behavior changed, and our usage is exposed.
      - LOW : either (a) trigger exercised and behavior did NOT change for the named
              dimension, or (b) our usage is provably NOT exposed (reasoned mapping).
      - NONE: reserved -- requires (b) AND an explicit not-used mapping; otherwise floored to LOW.
    Anything incomplete (no probe / trigger not exercised / unknown) stays MEDIUM.
    """
    built = _as_bool(c.get("probe_built"))
    exercised = _as_bool(c.get("trigger_condition_exercised"))
    changed = _as_bool(c.get("behavior_changed"))
    exposed = _as_bool(c.get("our_usage_exposed"))
    mapping = (c.get("our_usage_mapping") or "").strip()

    # Incomplete proof -> Medium floor.
    if not built or exercised is not True:
        return "medium", "probe did not exercise the trigger; committed at Medium (no false-green)"

    if changed is True:
        if exposed is True:
            return "high", "probe exercised the trigger; behavior changed and our usage is exposed"
        if exposed is False and len(mapping) >= 12:
            return "low", "behavior changed but our usage is provably not exposed: " + mapping[:160]
        return "medium", "behavior changed; our exposure is unclear -> Medium"

    if changed is False:
        # Trigger was actually exercised and the named dimension did not change for us.
        if exposed is False and len(mapping) >= 12:
            return "none", "trigger exercised, no change, and our usage does not rely on it: " + mapping[:140]
        return "low", "trigger exercised; the named behavior did not change in a way that affects this call"

    return "medium", "inconclusive probe result; committed at Medium"


def build_grade_from_contract(c, source_context=None):
    grade, reason = derive_grade(c, source_context)
    return {
        "grade": grade,
        "source": "probe",
        "rationale": reason,
        "changed_behavior": str(c.get("changed_behavior_summary", ""))[:200],
        "observed_from": str(c.get("observed_from", ""))[:200],
        "observed_to": str(c.get("observed_to", ""))[:200],
        "trigger_condition": str(c.get("trigger_condition", ""))[:200],
        "trigger_exercised": _as_bool(c.get("trigger_condition_exercised")),
        "behavior_changed": _as_bool(c.get("behavior_changed")),
        "our_usage_exposed": _as_bool(c.get("our_usage_exposed")),
        "our_usage_mapping": str(c.get("our_usage_mapping", ""))[:300],
        "evidence": str(c.get("evidence", ""))[:600],
        "limitations": str(c.get("limitations", ""))[:300],
        "confidence": str(c.get("confidence", "low")).strip().lower()
        if str(c.get("confidence", "")).strip().lower() in ("low", "medium", "high") else "low",
        "probe_commands": [str(x)[:200] for x in (c.get("probe_commands") or [])][:8],
        "model": AGENT_CMD,
        "generated_at": int(time.time()),
        "honest_cap": "reproduced the documented change under a synthetic call configuration; "
                      "not a production guarantee",
    }


# ── not-observable reasoning oracle (release-notes + usage, no execution) ────
def derive_reasoning_grade(c, source_context=None):
    grade, reason = _derive_reasoning_grade_raw(c)
    # PROOF FLOOR: lowering to LOW requires evidence grounded in the supplied
    # changelog/bullet/callsite inputs, not arbitrary "structurally avoids" prose.
    # Length alone is not sufficient -- the reasoning path has no probe output to
    # rely on, so only source-text grounding counts. No anchor -> honest Medium.
    if grade == "low":
        evidence = str(c.get("evidence", "")).strip()
        if not _evidence_grounded_in_sources(evidence, source_context):
            return "medium", (
                "release-notes reasoning suggested low exposure but evidence "
                "lacked verifiable provenance (no anchor from supplied "
                "changelog/bullet/callsite); committed at Medium (no false-green)"
            )
    return grade, reason


def _derive_reasoning_grade_raw(c):
    """Conservative floors for the release-notes reasoning oracle.

    This oracle reasons about a break a probe CANNOT reproduce (cardinality, memory,
    latency, retry, concurrency, stateful). It cannot PROVE absence of a runtime break,
    so it NEVER returns None. It mirrors how a senior dev reads the release notes and
    maps them to our call sites:
      - HIGH  : our usage plausibly HITS the trigger condition, with cited reasoning.
      - LOW   : our usage STRUCTURALLY avoids the trigger, with cited reasoning.
      - MEDIUM: uncertain / under-justified (the honest default, never a shrug).
    """
    assess = str(c.get("exposure_assessment", "")).strip().lower()
    reasoning = str(c.get("exposure_reasoning", "")).strip()
    if assess == "hits" and len(reasoning) >= 24:
        return "high", "release-notes reasoning: our usage hits the trigger condition -- " + reasoning[:200]
    if assess in ("avoids", "structurally_avoids", "not_exposed") and len(reasoning) >= 24:
        return "low", "release-notes reasoning: our usage structurally avoids the trigger -- " + reasoning[:200]
    return "medium", "release-notes reasoning: exposure uncertain; committed at Medium"


def build_reasoning_grade(c, site, router, source_context=None):
    grade, reason = derive_reasoning_grade(c, source_context)
    loc = f"{site['file']}:{site['line']}" if site and site.get("line") else (site.get("file") if site else "")
    return {
        "grade": grade,
        "source": "reasoning",
        "rationale": reason,
        "trigger_condition": str(c.get("trigger_condition", ""))[:240],
        "our_relevant_usage": str(c.get("our_relevant_usage", ""))[:300],
        "exposure_assessment": str(c.get("exposure_assessment", ""))[:40],
        "guidance": str(c.get("guidance", ""))[:300] or (f"Affected package used at {loc}." if loc else ""),
        "evidence": str(c.get("evidence", ""))[:600],
        "behavior_changed": "declared",
        "call_site": loc,
        "router_class": router["class"],
        "router_markers": router.get("markers", []),
        "confidence": str(c.get("confidence", "low")).strip().lower()
        if str(c.get("confidence", "")).strip().lower() in ("low", "medium", "high") else "low",
        "limitations": str(c.get("limitations", ""))[:300]
        or "reasoned from release notes + static usage; not a runtime guarantee",
        "model": AGENT_CMD,
        "generated_at": int(time.time()),
    }
