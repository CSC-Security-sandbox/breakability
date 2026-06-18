#!/usr/bin/env python3
"""Provenance tests for differential-probe grade floors (stdlib only, no pytest).

Invariant under test: an agent cannot manufacture a Low or None grade by writing
plausible-sounding prose in the ``evidence`` field.  Evidence that lowers risk
must be verifiably grounded in the supplied changelog text, input bullet, call-site
identifier, or concrete probe observed_from/observed_to output.

Run:  python3 .github/scripts/test_differential_probe_provenance.py
Exits non-zero on any failure.
"""
import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_dp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "differential-probe.py")
_spec = importlib.util.spec_from_file_location("differential_probe", _dp_path)
dp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dp)

derive_grade = dp.derive_grade
derive_reasoning_grade = dp.derive_reasoning_grade
_observed_output_is_real = dp._observed_output_is_real
_evidence_grounded_in_sources = dp._evidence_grounded_in_sources

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _probe_contract(changed, exposed=None, mapping="", evidence="", ofrom="", oto=""):
    """Minimal valid probe contract that would reach Low/None without the floor."""
    return {
        "probe_built": True,
        "trigger_condition_exercised": True,
        "behavior_changed": changed,
        "our_usage_exposed": exposed,
        "our_usage_mapping": mapping,
        "evidence": evidence,
        "observed_from": ofrom,
        "observed_to": oto,
    }


def _reasoning_contract(assessment, reasoning, evidence=""):
    """Minimal valid reasoning contract that would reach Low without the floor."""
    return {
        "exposure_assessment": assessment,
        "exposure_reasoning": reasoning,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# _observed_output_is_real unit tests
# ---------------------------------------------------------------------------

def test_observed_trivial_strings_are_not_real():
    for v in ("", "none", "n/a", "null", "undefined", "0", "[]", "{}"):
        assert not _observed_output_is_real(v, "real output value here"), (
            f"trivial '{v}' must not count as real observed output"
        )
    print("✓ test_observed_trivial_strings_are_not_real passed")


def test_observed_short_strings_are_not_real():
    assert not _observed_output_is_real("ab", "cd"), "too-short strings must not count"
    print("✓ test_observed_short_strings_are_not_real passed")


def test_observed_real_values_pass():
    assert _observed_output_is_real("exit_code=0", "exit_code=0"), (
        "identical non-trivial observed values (no change) must be real"
    )
    assert _observed_output_is_real("result=42", "result=99"), (
        "distinct non-trivial observed values must be real"
    )
    assert _observed_output_is_real(
        "output: 'hello world'", "output: 'hello world'"
    ), "quoted output strings must be real"
    print("✓ test_observed_real_values_pass passed")


# ---------------------------------------------------------------------------
# _evidence_grounded_in_sources unit tests
# ---------------------------------------------------------------------------

def test_grounding_no_context_returns_false():
    assert not _evidence_grounded_in_sources("long invented text that matches nothing", None)
    assert not _evidence_grounded_in_sources("long invented text that matches nothing", {})
    print("✓ test_grounding_no_context_returns_false passed")


def test_grounding_generic_short_tokens_returns_false():
    ctx = {"bullet": "rate change", "changelog_text": "new version"}
    # No token ≥10 chars → no anchor → False
    assert not _evidence_grounded_in_sources("rate change in new version", ctx)
    print("✓ test_grounding_generic_short_tokens_returns_false passed")


def test_grounding_bullet_anchor():
    ctx = {"bullet": "prometheus.NewCounterVec signature changed"}
    evidence = "prometheus.NewCounterVec is not called anywhere in production"
    assert _evidence_grounded_in_sources(evidence, ctx), (
        "evidence quoting a ≥10-char token from bullet must be grounded"
    )
    print("✓ test_grounding_bullet_anchor passed")


def test_grounding_changelog_anchor():
    ctx = {"changelog_text": "Breaking: prometheus.NewCounterVec now requires explicit registry"}
    evidence = "prometheus.NewCounterVec usage in our codebase does not rely on the registry"
    assert _evidence_grounded_in_sources(evidence, ctx)
    print("✓ test_grounding_changelog_anchor passed")


def test_grounding_callsite_file():
    ctx = {"call_site": {"file": "internal/metrics.go", "symbol": "NewCounterVec"}}
    evidence = "internal/metrics.go does not invoke the changed code path"
    assert _evidence_grounded_in_sources(evidence, ctx)
    print("✓ test_grounding_callsite_file passed")


def test_grounding_callsite_symbol():
    ctx = {"call_site": {"file": "cmd/server/main.go", "symbol": "NewCounterVec"}}
    evidence = "NewCounterVec is only referenced as a label, not as a constructor here"
    assert _evidence_grounded_in_sources(evidence, ctx)
    print("✓ test_grounding_callsite_symbol passed")


def test_grounding_short_symbol_not_an_anchor():
    ctx = {"call_site": {"file": "x.go", "symbol": "New"}}
    evidence = "New is not called"
    # symbol < 6 chars → not a reliable anchor
    assert not _evidence_grounded_in_sources(evidence, ctx)
    print("✓ test_grounding_short_symbol_not_an_anchor passed")


# ---------------------------------------------------------------------------
# derive_grade provenance tests (probe path)
# ---------------------------------------------------------------------------

def test_invented_long_evidence_cannot_produce_low():
    """AI-authored prose (>20 chars) with no real probe output → floors to Medium."""
    c = _probe_contract(
        changed=False,
        evidence="The code structurally avoids the changed function by design and never reaches it",
        ofrom="",
        oto="",
    )
    grade, reason = derive_grade(c, source_context=None)
    assert grade == "medium", f"invented evidence must floor to medium, got {grade!r}: {reason}"
    assert "provenance" in reason.lower() or "floored" in reason.lower(), (
        f"floor reason must mention provenance, got: {reason}"
    )
    print("✓ test_invented_long_evidence_cannot_produce_low passed")


def test_invented_evidence_cannot_produce_none():
    """AI prose alone cannot produce None; must have real probe output or grounded evidence."""
    c = _probe_contract(
        changed=False,
        exposed=False,
        mapping="not used in production at all",
        evidence="Release notes confirm this function is exclusively internal and unused by callers",
        ofrom="",
        oto="",
    )
    grade, reason = derive_grade(c, source_context=None)
    assert grade == "medium", f"invented evidence must floor to medium, got {grade!r}"
    print("✓ test_invented_evidence_cannot_produce_none passed")


def test_trivial_observed_values_floor_to_medium():
    """Trivial placeholder observed values are not real probe output."""
    c = _probe_contract(
        changed=False,
        evidence="long enough evidence text to pass the old length check easily",
        ofrom="none",
        oto="none",
    )
    grade, _ = derive_grade(c, source_context=None)
    assert grade == "medium", f"trivial observed values must floor to medium, got {grade!r}"
    print("✓ test_trivial_observed_values_floor_to_medium passed")


def test_real_probe_output_can_produce_low():
    """Non-trivial observed_from/to (the probe ran) is sufficient to allow Low."""
    c = _probe_contract(
        changed=False,
        evidence="",
        ofrom="exit_code=0, output='counter registered'",
        oto="exit_code=0, output='counter registered'",
    )
    grade, reason = derive_grade(c, source_context=None)
    assert grade == "low", f"real probe output must allow low, got {grade!r}: {reason}"
    print("✓ test_real_probe_output_can_produce_low passed")


def test_real_probe_output_can_produce_none():
    """Real probe output with not-exposed mapping allows None."""
    c = _probe_contract(
        changed=False,
        exposed=False,
        mapping="our code uses the counter only as a label key, not relying on this path",
        evidence="",
        ofrom="output='value registered'",
        oto="output='value registered'",
    )
    grade, reason = derive_grade(c, source_context=None)
    assert grade == "none", f"real probe output + not-exposed mapping must allow none, got {grade!r}: {reason}"
    print("✓ test_real_probe_output_can_produce_none passed")


def test_evidence_grounded_in_bullet_can_produce_low():
    """Evidence quoting a token from the supplied bullet is grounded → Low allowed."""
    c = _probe_contract(
        changed=False,
        evidence="prometheus.NewCounterVec is never called in our production paths",
        ofrom="",
        oto="",
    )
    ctx = {"bullet": "prometheus.NewCounterVec signature changed to require opts"}
    grade, reason = derive_grade(c, source_context=ctx)
    assert grade == "low", f"bullet-anchored evidence must allow low, got {grade!r}: {reason}"
    print("✓ test_evidence_grounded_in_bullet_can_produce_low passed")


def test_evidence_grounded_in_callsite_can_produce_low():
    """Evidence quoting the call-site file path is grounded → Low allowed."""
    c = _probe_contract(
        changed=False,
        evidence="internal/metrics.go does not invoke the changed signature at all",
        ofrom="",
        oto="",
    )
    ctx = {"call_site": {"file": "internal/metrics.go", "symbol": "NewCounterVec"}}
    grade, reason = derive_grade(c, source_context=ctx)
    assert grade == "low", f"callsite-anchored evidence must allow low, got {grade!r}: {reason}"
    print("✓ test_evidence_grounded_in_callsite_can_produce_low passed")


def test_no_source_context_but_real_probe_output_allows_low():
    """source_context=None is fine when probe output itself is the ground truth."""
    c = _probe_contract(
        changed=False,
        evidence="",
        ofrom="probe_result=stable",
        oto="probe_result=stable",
    )
    grade, reason = derive_grade(c, source_context=None)
    assert grade == "low", f"real probe output with no source_context must still allow low, got {grade!r}: {reason}"
    print("✓ test_no_source_context_but_real_probe_output_allows_low passed")


# ---------------------------------------------------------------------------
# derive_reasoning_grade provenance tests (reasoning / not-observable path)
# ---------------------------------------------------------------------------

def test_invented_reasoning_evidence_floors_to_medium():
    """Long invented prose in the reasoning path cannot produce Low."""
    c = _reasoning_contract(
        assessment="avoids",
        reasoning="Our code is structurally isolated from this change and never exercises it",
        evidence="Our code does not use this API surface at all in production anywhere",
    )
    grade, reason = derive_reasoning_grade(c, source_context=None)
    assert grade == "medium", f"invented reasoning evidence must floor to medium, got {grade!r}"
    assert "provenance" in reason.lower() or "false-green" in reason.lower(), (
        f"floor reason must mention provenance or false-green, got: {reason}"
    )
    print("✓ test_invented_reasoning_evidence_floors_to_medium passed")


def test_empty_reasoning_evidence_floors_to_medium():
    """Empty evidence in the reasoning path always floors to Medium."""
    c = _reasoning_contract(
        assessment="avoids",
        reasoning="Our code is structurally isolated from this change and never exercises it",
        evidence="",
    )
    grade, _ = derive_reasoning_grade(c, source_context=None)
    assert grade == "medium", f"empty reasoning evidence must floor to medium, got {grade!r}"
    print("✓ test_empty_reasoning_evidence_floors_to_medium passed")


def test_reasoning_evidence_grounded_in_bullet_allows_low():
    """Reasoning evidence quoting the supplied bullet is grounded → Low allowed."""
    c = _reasoning_contract(
        assessment="avoids",
        reasoning="Our code is structurally isolated from this change and never exercises it",
        evidence="prometheus.NewCounterVec is never referenced in any production file",
    )
    ctx = {"bullet": "prometheus.NewCounterVec opts parameter is now required"}
    grade, reason = derive_reasoning_grade(c, source_context=ctx)
    assert grade == "low", f"bullet-anchored reasoning evidence must allow low, got {grade!r}: {reason}"
    print("✓ test_reasoning_evidence_grounded_in_bullet_allows_low passed")


def test_reasoning_evidence_grounded_in_changelog_allows_low():
    """Reasoning evidence quoting the changelog text is grounded → Low allowed."""
    c = _reasoning_contract(
        assessment="avoids",
        reasoning="Our code is structurally isolated from this change and never exercises it",
        evidence="prometheus.NewCounterVec breaking change does not affect our gauge usage",
    )
    ctx = {"changelog_text": "Breaking: prometheus.NewCounterVec now requires explicit registry object"}
    grade, reason = derive_reasoning_grade(c, source_context=ctx)
    assert grade == "low", f"changelog-anchored reasoning evidence must allow low, got {grade!r}: {reason}"
    print("✓ test_reasoning_evidence_grounded_in_changelog_allows_low passed")


def test_reasoning_evidence_grounded_in_callsite_allows_low():
    """Reasoning evidence citing the call-site file is grounded → Low allowed."""
    c = _reasoning_contract(
        assessment="avoids",
        reasoning="Our code is structurally isolated from this change and never exercises it",
        evidence="internal/metrics.go only calls the gauge constructor, not NewCounterVec",
    )
    ctx = {"call_site": {"file": "internal/metrics.go", "symbol": "NewGaugeVec"}}
    grade, reason = derive_reasoning_grade(c, source_context=ctx)
    assert grade == "low", f"callsite-anchored reasoning evidence must allow low, got {grade!r}: {reason}"
    print("✓ test_reasoning_evidence_grounded_in_callsite_allows_low passed")


def test_reasoning_high_path_not_affected_by_provenance_check():
    """The HIGH path (hits trigger) is not gated by provenance — no floor needed."""
    c = _reasoning_contract(
        assessment="hits",
        reasoning="Our code calls this function in the hot path and would observe the change",
        evidence="",
    )
    grade, _ = derive_reasoning_grade(c, source_context=None)
    assert grade == "high", f"HIGH reasoning path must not be floored, got {grade!r}"
    print("✓ test_reasoning_high_path_not_affected_by_provenance_check passed")


def test_reasoning_medium_unchanged_by_provenance_check():
    """An inconclusive/uncertain reasoning result stays at Medium regardless."""
    c = _reasoning_contract(assessment="unclear", reasoning="not enough info")
    grade, _ = derive_reasoning_grade(c, source_context=None)
    assert grade == "medium", f"uncertain reasoning must stay medium, got {grade!r}"
    print("✓ test_reasoning_medium_unchanged_by_provenance_check passed")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    tests = [
        # _observed_output_is_real
        test_observed_trivial_strings_are_not_real,
        test_observed_short_strings_are_not_real,
        test_observed_real_values_pass,
        # _evidence_grounded_in_sources
        test_grounding_no_context_returns_false,
        test_grounding_generic_short_tokens_returns_false,
        test_grounding_bullet_anchor,
        test_grounding_changelog_anchor,
        test_grounding_callsite_file,
        test_grounding_callsite_symbol,
        test_grounding_short_symbol_not_an_anchor,
        # derive_grade (probe path)
        test_invented_long_evidence_cannot_produce_low,
        test_invented_evidence_cannot_produce_none,
        test_trivial_observed_values_floor_to_medium,
        test_real_probe_output_can_produce_low,
        test_real_probe_output_can_produce_none,
        test_evidence_grounded_in_bullet_can_produce_low,
        test_evidence_grounded_in_callsite_can_produce_low,
        test_no_source_context_but_real_probe_output_allows_low,
        # derive_reasoning_grade (reasoning path)
        test_invented_reasoning_evidence_floors_to_medium,
        test_empty_reasoning_evidence_floors_to_medium,
        test_reasoning_evidence_grounded_in_bullet_allows_low,
        test_reasoning_evidence_grounded_in_changelog_allows_low,
        test_reasoning_evidence_grounded_in_callsite_allows_low,
        test_reasoning_high_path_not_affected_by_provenance_check,
        test_reasoning_medium_unchanged_by_provenance_check,
    ]
    failed = []
    for test in tests:
        try:
            test()
        except Exception as e:
            failed.append((test.__name__, e))
            print(f"✗ {test.__name__} FAILED: {e}", file=sys.stderr)

    if failed:
        print(f"\n{len(failed)} test(s) failed:", file=sys.stderr)
        for name, e in failed:
            print(f"  - {name}: {e}", file=sys.stderr)
        return 1

    print(f"\n✓ All {len(tests)} provenance tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
