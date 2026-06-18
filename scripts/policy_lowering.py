#!/usr/bin/env python3
"""Build typed policy-lowering evidence bundles from build-results.

This is the Go-MVP integration seam: deterministic build/test/API evidence,
release-note evidence, lightweight reachability, callsite impact, and optional
dynamic-probe evidence are converted into ``EvidenceBundle`` objects, then the
pure ``decide()`` policy is applied.

The module is intentionally stdlib-only and side-effect free unless ``--output``
is provided. It never runs networked probes; it only consumes probe evidence
already present in the PR record.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from typing import Any, Dict, Iterable, Mapping, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from evidence_contract import (  # noqa: E402
    Confidence,
    EvidenceBundle,
    EvidenceRecord,
    SafetySeverity,
    SignalName,
    SignalStatus,
    VerdictDecision,
    decide,
)
from release_notes_evidence import analyse_pr as analyse_release_notes  # noqa: E402
from callsite_impact import analyze as analyze_callsite_impact  # noqa: E402
from agent_adjudicator import adjudicate_reachability  # noqa: E402
from ci_classifier import ci_security_sensitive as _ci_security_sensitive  # noqa: E402


def _load_lite_module() -> Any:
    path = os.path.join(os.path.dirname(SCRIPT_DIR), "tools", "reachability", "lite.py")
    spec = importlib.util.spec_from_file_location("_lite_reachability", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reachability lite module from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LITE = None


def _lite() -> Any:
    global _LITE
    if _LITE is None:
        _LITE = _load_lite_module()
    return _LITE


def _prs(results: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = results.get("prs") or results.get("pr_results") or results.get("results") or {}
    return raw if isinstance(raw, Mapping) else {}


def _first_str(pr: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = pr.get(key)
        if isinstance(value, str) and value:
            return value
        if value is not None and not isinstance(value, (dict, list)):
            return str(value)
    return default


def _exit_status(exit_code: Any) -> Optional[int]:
    try:
        return int(str(exit_code))
    except Exception:
        return None


def _record(name: SignalName, status: SignalStatus = SignalStatus.UNKNOWN, **kwargs: Any) -> EvidenceRecord:
    return EvidenceRecord(name=name, status=status, **kwargs)


def _build_record(pr: Mapping[str, Any]) -> EvidenceRecord:
    build = pr.get("build") if isinstance(pr.get("build"), Mapping) else {}
    exit_code = _exit_status(build.get("pr_exit"))
    main_exit = _exit_status(build.get("main_exit"))
    verdict = str(build.get("verdict") or "").lower()
    if verdict == "pass" or exit_code == 0:
        return _record(SignalName.BUILD, SignalStatus.PASS, confidence=Confidence.HIGH)
    if exit_code is not None and exit_code > 0:
        if verdict == "pre_existing_plus_new" or bool(build.get("new_errors")):
            return _record(
                SignalName.BUILD,
                SignalStatus.FAIL,
                severity=SafetySeverity.HIGH,
                residual_risk=SafetySeverity.HIGH,
                confidence=Confidence.HIGH,
            )
        if verdict == "pre_existing" or (main_exit is not None and main_exit > 0 and not build.get("new_errors")):
            return _record(
                SignalName.BUILD,
                SignalStatus.PASS,
                confidence=Confidence.MEDIUM,
                rationale="build failure also occurs on main; not introduced by this PR",
            )
        if main_exit not in (0,):
            return _record(
                SignalName.BUILD,
                SignalStatus.UNKNOWN,
                residual_risk=SafetySeverity.MEDIUM,
                confidence=Confidence.LOW,
                rationale="build failed but main baseline was unavailable",
            )
        return _record(
            SignalName.BUILD,
            SignalStatus.FAIL,
            severity=SafetySeverity.HIGH,
            residual_risk=SafetySeverity.HIGH,
            confidence=Confidence.HIGH,
        )
    return _record(SignalName.BUILD, SignalStatus.UNAVAILABLE, tool_failure=True, confidence=Confidence.LOW)


def _test_record(pr: Mapping[str, Any], global_test_exit: Optional[int] = None) -> EvidenceRecord:
    test = pr.get("test") if isinstance(pr.get("test"), Mapping) else {}
    if not test.get("ran"):
        return _record(SignalName.TEST, SignalStatus.UNAVAILABLE, confidence=Confidence.LOW)

    exit_code = _exit_status(test.get("exit"))
    main_exit = _exit_status(test.get("main_test_exit"))
    # The repo-wide baseline `go test -race` result. When this is non-zero the suite does NOT
    # pass on `main` (e.g. a pre-existing data race), so it is non-deterministic and a per-PR
    # `main_test_exit == 0` cannot be trusted to mean "the PR introduced this failure".
    baseline_unreliable = global_test_exit is not None and global_test_exit != 0
    if exit_code == 0:
        return _record(SignalName.TEST, SignalStatus.PASS, confidence=Confidence.HIGH)
    if exit_code is not None and exit_code > 0:
        if main_exit is not None and main_exit > 0:
            return _record(
                SignalName.TEST,
                SignalStatus.PASS,
                confidence=Confidence.MEDIUM,
                rationale="test failure also occurs on main; not introduced by this PR",
            )
        if baseline_unreliable:
            return _record(
                SignalName.TEST,
                SignalStatus.UNKNOWN,
                residual_risk=SafetySeverity.MEDIUM,
                confidence=Confidence.LOW,
                rationale="baseline test suite is unreliable (pre-existing failure on main); PR test failure not confirmed as introduced",
            )
        if main_exit == 0:
            return _record(
                SignalName.TEST,
                SignalStatus.FAIL,
                severity=SafetySeverity.HIGH,
                residual_risk=SafetySeverity.HIGH,
                confidence=Confidence.HIGH,
            )
        return _record(
            SignalName.TEST,
            SignalStatus.UNKNOWN,
            residual_risk=SafetySeverity.MEDIUM,
            confidence=Confidence.LOW,
            rationale="test failed but main baseline was unavailable",
        )
    return _record(SignalName.TEST, SignalStatus.UNKNOWN, residual_risk=SafetySeverity.MEDIUM, confidence=Confidence.LOW)


def _api_record(pr: Mapping[str, Any]) -> EvidenceRecord:
    det = pr.get("deterministic") if isinstance(pr.get("deterministic"), Mapping) else {}
    details = det.get("api_changes_detail")
    changes = _exit_status(det.get("api_changes"))
    tool_raw = det.get("api_diff_tool")
    tool_mode = str(tool_raw.get("mode") or "") if isinstance(tool_raw, Mapping) else ""
    tool = str(tool_raw or "")
    structural = tool_mode.startswith("structural")
    if changes == 0:
        status_str = str(tool_raw.get("status") or "") if isinstance(tool_raw, Mapping) else ""
        # A SEMANTIC apidiff (module mode) reporting zero changes is HIGH-confidence proof of
        # API backward-compatibility — it understands signatures/types. A structural go-doc
        # fallback (or an absent tool) reporting zero is only weak corroboration: it cannot see
        # signatures, so it passes at MEDIUM confidence and must NOT, on its own, trigger the
        # test-independent API-compatibility clearance in decide().
        semantic = status_str == "semantic" or tool_mode == "module"
        return _record(
            SignalName.API_DIFF,
            SignalStatus.PASS,
            confidence=Confidence.HIGH if semantic else Confidence.MEDIUM,
        )
    if changes is not None and changes > 0:
        severity = SafetySeverity.HIGH if _has_breaking_api_change(details, structural=structural) else SafetySeverity.LOW
        status = SignalStatus.FAIL if severity == SafetySeverity.HIGH else SignalStatus.UNKNOWN
        return _record(
            SignalName.API_DIFF,
            status,
            severity=severity,
            residual_risk=severity,
            relevant=True,
            confidence=Confidence.HIGH,
        )
    return _record(
        SignalName.API_DIFF,
        SignalStatus.UNAVAILABLE,
        residual_risk=SafetySeverity.MEDIUM,
        confidence=Confidence.LOW,
        rationale=f"api diff unavailable ({tool or 'missing deterministic.api_changes'})",
    )


def _has_breaking_api_change(details: Any, structural: bool = False) -> bool:
    if not isinstance(details, list):
        return False
    hard_change_types = {
        "removed",
        "deleted",
        "type_changed",
        "return_type_changed",
        "signature_changed",
        "parameter_removed",
        "parameter_type_changed",
        "required_parameter_added",
        "incompatible",
        "breaking",
    }
    breaking_words = ("removed", "deleted", "signature", "incompatible", "breaking", "type_changed", "return_type_changed")
    for item in details:
        if isinstance(item, Mapping):
            change_type = str(item.get("changeType") or item.get("kind") or item.get("type") or "").strip().lower()
            # Structural-fallback noise: the go-doc structural diff only captures the SYMBOL KIND
            # (function/method/struct/interface), never the signature. When it reports a
            # `type_changed` whose old and new definitions are identical (e.g. function->function),
            # it has observed no actual change — it cannot see signatures — so this carries zero
            # evidence of a real break and must not be treated as a hard break. Genuine removals
            # surface as changeType=removed (old->None), and real kind changes have old!=new, so
            # those are preserved. Semantic (apidiff module) mode is never suppressed.
            if structural and change_type in {"type_changed", "return_type_changed"}:
                old_def = item.get("oldDefinition")
                new_def = item.get("newDefinition")
                if old_def is not None and new_def is not None and old_def == new_def:
                    continue
            if item.get("isHardBreak") is True or item.get("hard_break") is True:
                return True
            if change_type in hard_change_types:
                return True
        blob = json.dumps(item, sort_keys=True).lower() if isinstance(item, (dict, list)) else str(item).lower()
        if any(word in blob for word in breaking_words):
            return True
    return False


def _security_record(pr: Mapping[str, Any]) -> EvidenceRecord:
    new_findings = pr.get("vuln_new_findings")
    if isinstance(new_findings, list) and new_findings:
        return _record(
            SignalName.SECURITY,
            SignalStatus.FAIL,
            severity=SafetySeverity.HIGH,
            residual_risk=SafetySeverity.HIGH,
            introduced=True,
            confidence=Confidence.HIGH,
        )
    status = str(pr.get("vuln_status") or "").strip().lower()
    if status in {"skipped_disabled", "disabled", "not_applicable", "n/a"}:
        return _record(SignalName.SECURITY, SignalStatus.NOT_APPLICABLE, confidence=Confidence.MEDIUM)
    if status in {"ok", "ok_preexisting", "clean", "pass", "passed", "no_findings", "no_new_findings"}:
        return _record(SignalName.SECURITY, SignalStatus.PASS, confidence=Confidence.MEDIUM)
    # npm: `npm audit` ran (baseline vs PR). When the audit completed and the upgrade
    # introduced no NEW findings, the security axis is clean for breakability purposes
    # even if pre-existing repo vulns remain (those are not caused by this PR). This is
    # ecosystem-neutral: non-npm results carry no npm_audit key and fall through.
    npm_audit = pr.get("npm_audit")
    if isinstance(npm_audit, Mapping) and not (isinstance(new_findings, list) and new_findings):
        return _record(
            SignalName.SECURITY,
            SignalStatus.PASS,
            confidence=Confidence.MEDIUM,
            rationale="npm audit ran; upgrade introduces no new advisories",
        )
    return _record(
        SignalName.SECURITY,
        SignalStatus.UNAVAILABLE,
        residual_risk=SafetySeverity.MEDIUM,
        tool_failure=True,
        confidence=Confidence.LOW,
        rationale=f"vulnerability scan unavailable ({status or 'missing vuln_status'})",
    )


def _changed_symbols_from_release_notes(release_notes: EvidenceRecord) -> list[str]:
    # release_notes_evidence deliberately does not infer symbols from prose yet.
    # Keep hook for future first-class declared-break extractor.
    return []


def _reachability_record(callsite: Mapping[str, Any]) -> EvidenceRecord:
    signal = callsite.get("signal") if isinstance(callsite.get("signal"), Mapping) else {}
    status = SignalStatus(signal.get("status", SignalStatus.UNKNOWN.value))
    confidence = Confidence(signal.get("confidence", Confidence.LOW.value))
    relevant = signal.get("relevant") if isinstance(signal.get("relevant"), bool) else None
    impact = str(callsite.get("impact") or "")
    kwargs: Dict[str, Any] = {"relevant": relevant, "confidence": confidence}
    if impact == "REACHED_RELEVANT":
        kwargs.update(severity=SafetySeverity.HIGH, residual_risk=SafetySeverity.HIGH)
    elif impact in {"REACHED_UNKNOWN", "UNCERTAIN"}:
        kwargs.update(residual_risk=SafetySeverity.MEDIUM)
    return _record(SignalName.REACHABILITY, status, **kwargs)


def _probe_record_from_pr(pr: Mapping[str, Any]) -> Optional[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    behavioral = pr.get("behavioral_grade")
    if isinstance(behavioral, Mapping):
        record = _probe_record_from_behavioral_grade(behavioral)
        if record is not None:
            records.append(record)

    for key in ("dynamic_probe_result", "dynamic_probe", "probe_result", "probe"):
        raw = pr.get(key)
        if not isinstance(raw, Mapping):
            continue
        records.append(_probe_record_from_raw_probe(raw))
    return _dominant_probe_record(records)


def _probe_record_from_raw_probe(raw: Mapping[str, Any]) -> EvidenceRecord:
    evidence = raw.get("evidence")
    if isinstance(evidence, Mapping):
        try:
            return EvidenceRecord.from_dict({**evidence, "name": evidence.get("name", SignalName.PROBE.value)})
        except Exception:
            return _record(SignalName.PROBE, SignalStatus.UNAVAILABLE, tool_failure=True, confidence=Confidence.LOW)
    classification = str(raw.get("classification") or "").upper()
    if classification == "SAME_BEHAVIOR":
        return _record(SignalName.PROBE, SignalStatus.PASS, same_behavior=True, relevant=False, confidence=Confidence.HIGH)
    if classification == "CHANGED_BEHAVIOR":
        return _record(
            SignalName.PROBE,
            SignalStatus.FAIL,
            same_behavior=False,
            relevant=True,
            severity=SafetySeverity.MEDIUM,
            residual_risk=SafetySeverity.MEDIUM,
            confidence=Confidence.HIGH,
        )
    return _record(SignalName.PROBE, SignalStatus.UNKNOWN, residual_risk=SafetySeverity.MEDIUM, confidence=Confidence.LOW)


def _dominant_probe_record(records: list[EvidenceRecord]) -> Optional[EvidenceRecord]:
    if not records:
        return None
    for status in (SignalStatus.FAIL, SignalStatus.UNAVAILABLE, SignalStatus.UNKNOWN, SignalStatus.PASS):
        for record in records:
            if record.status == status:
                return record
    return records[0]


def _probe_record_from_behavioral_grade(bg: Mapping[str, Any]) -> Optional[EvidenceRecord]:
    grade = str(bg.get("grade") or "").strip().lower()
    source = str(bg.get("source") or "").strip().lower()
    changed = bg.get("behavior_changed")
    if changed is None:
        changed = bg.get("changed_behavior")
    changed_bool = changed is True or str(changed).strip().lower() == "true"
    exposed = bg.get("our_usage_exposed")
    exposed_bool = exposed is True or str(exposed).strip().lower() == "true"

    # We are demonstrably hit, or the probe graded the break high -> FAIL.
    if grade == "high" or exposed_bool:
        return _record(
            SignalName.PROBE,
            SignalStatus.FAIL,
            same_behavior=False,
            relevant=True,
            severity=SafetySeverity.MEDIUM,
            residual_risk=SafetySeverity.MEDIUM,
            confidence=_confidence_from_behavioral_grade(bg),
            rationale=f"behavioral grade {grade or 'unknown'} from {source or 'unknown'}",
        )
    # An executed probe that graded none/low and proved our usage is NOT exposed
    # clears, even when the dependency's behaviour changed globally -- the whole
    # point of the differential probe is to map the change to our call site. The
    # driver only emits none/low with executed proof of non-exposure (else medium).
    if source == "probe" and grade in {"none", "low"}:
        return _record(
            SignalName.PROBE,
            SignalStatus.PASS,
            same_behavior=True,
            relevant=False,
            confidence=_confidence_from_behavioral_grade(bg),
            rationale=f"probe grade {grade}; our usage not exposed",
        )
    # No clearing probe grade, but a behavioural change is asserted -> FAIL.
    if changed_bool:
        return _record(
            SignalName.PROBE,
            SignalStatus.FAIL,
            same_behavior=False,
            relevant=True,
            severity=SafetySeverity.MEDIUM,
            residual_risk=SafetySeverity.MEDIUM,
            confidence=_confidence_from_behavioral_grade(bg),
            rationale=f"behavioral grade {grade or 'unknown'} from {source or 'unknown'}",
        )
    if source == "probe" and grade == "medium":
        return _record(
            SignalName.PROBE,
            SignalStatus.UNKNOWN,
            residual_risk=SafetySeverity.MEDIUM,
            confidence=_confidence_from_behavioral_grade(bg),
            rationale="probe grade medium",
        )
    return None


def _confidence_from_behavioral_grade(bg: Mapping[str, Any]) -> Confidence:
    raw = str(bg.get("confidence") or "").strip().lower()
    if raw in {c.value for c in Confidence}:
        return Confidence(raw)
    return Confidence.MEDIUM


def bundle_for_pr(pr: Mapping[str, Any], global_test_exit: Optional[int] = None) -> tuple[EvidenceBundle, Dict[str, Any]]:
    release_notes = analyse_release_notes(dict(pr))
    changed_symbols = _changed_symbols_from_release_notes(release_notes)
    reachability = _lite().analyze(dict(pr), changed_symbols or None)
    callsite = analyze_callsite_impact(dict(pr), release_notes.to_dict(), reachability)

    signals: Dict[SignalName, EvidenceRecord] = {
        SignalName.BUILD: _build_record(pr),
        SignalName.TEST: _test_record(pr, global_test_exit),
        SignalName.API_DIFF: _api_record(pr),
        SignalName.RELEASE_NOTES: release_notes,
        SignalName.REACHABILITY: _reachability_record(callsite),
        SignalName.SECURITY: _security_record(pr),
    }
    probe = _probe_record_from_pr(pr)
    if probe is not None:
        signals[SignalName.PROBE] = probe

    bundle = EvidenceBundle(
        package=_first_str(pr, "package", default="unknown"),
        ecosystem=_first_str(pr, "ecosystem", default="unknown"),
        from_version=_first_str(pr, "from", "from_version", default="unknown"),
        to_version=_first_str(pr, "to", "to_version", default="unknown"),
        signals=signals,
        confidence=Confidence.MEDIUM,
        residual_risk=_bundle_residual(signals),
        is_major=_is_major_bump(pr),
        is_ci_only=_first_str(pr, "ecosystem").lower() in {"actions", "docker"},
        security_sensitive=(
            bool(pr.get("security_sensitive"))
            or str(pr.get("ci_tier") or "") == "secsens"
            or (
                _first_str(pr, "ecosystem").lower() in {"actions", "docker"}
                and _ci_security_sensitive(_first_str(pr, "package"))
            )
            or bool(pr.get("cves"))
        ),
    )
    # Reachability adjudication — the agent-review layer's deterministic core. For a
    # break-reachable API, decide whether a *changed* symbol is actually called, or scope a
    # precise task for the AI agent when symbol-level reachability is unresolved.
    adjudication = adjudicate_reachability(pr, callsite, reachability)
    support = {
        "release_notes": release_notes.to_dict(),
        "reachability": reachability,
        "callsite_impact": callsite,
        "reachability_adjudication": adjudication,
    }
    return bundle, support


def _bundle_residual(signals: Mapping[SignalName, EvidenceRecord]) -> SafetySeverity:
    rank = 0
    values = {
        SafetySeverity.NONE: 0,
        SafetySeverity.LOW: 1,
        SafetySeverity.MEDIUM: 2,
        SafetySeverity.HIGH: 3,
    }
    reverse = {v: k for k, v in values.items()}
    for rec in signals.values():
        rank = max(rank, values[rec.residual_risk], values[rec.severity])
    return reverse[rank]


def _is_major_bump(pr: Mapping[str, Any]) -> bool:
    def major(v: str) -> Optional[int]:
        v = v.lstrip("v")
        parts = v.split(".")
        try:
            return int(parts[0])
        except Exception:
            return None

    old = major(_first_str(pr, "from", "from_version"))
    new = major(_first_str(pr, "to", "to_version"))
    return old is not None and new is not None and new > old


def _global_test_exit(results: Mapping[str, Any]) -> Optional[int]:
    """Repo-wide baseline `go test -race` exit code (main_build.go.test_exit).

    Non-zero (or -1) means the suite does not pass cleanly on `main`, so per-PR test
    failures cannot be trusted as PR-introduced.
    """
    if not isinstance(results, Mapping):
        return None
    main_build = results.get("main_build")
    if not isinstance(main_build, Mapping):
        return None
    go = main_build.get("go")
    if not isinstance(go, Mapping):
        return None
    return _exit_status(go.get("test_exit"))


def decision_for_pr(pr: Mapping[str, Any], global_test_exit: Optional[int] = None) -> Dict[str, Any]:
    bundle, support = bundle_for_pr(pr, global_test_exit)
    decision: VerdictDecision = decide(bundle)
    return {
        "decision": decision.to_dict(),
        "bundle": bundle.to_dict(),
        **support,
    }


def apply_policy(results: Mapping[str, Any], pr_numbers: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    selected = {str(n) for n in pr_numbers} if pr_numbers else None
    global_test_exit = _global_test_exit(results)
    out: Dict[str, Any] = {}
    for pr_num, pr in _prs(results).items():
        key = str(pr_num)
        if selected is not None and key not in selected:
            continue
        if not isinstance(pr, Mapping):
            continue
        out[key] = decision_for_pr(pr, global_test_exit)
    return out


def enrich_results(results: Mapping[str, Any], pr_numbers: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    enriched = json.loads(json.dumps(results))
    decisions = apply_policy(enriched, pr_numbers)
    for pr_num, payload in decisions.items():
        pr = _prs(enriched).get(pr_num)
        if isinstance(pr, dict):
            pr["policy_lowering"] = payload
    return enriched


def _parse_prs(raw: Optional[str]) -> Optional[list[str]]:
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build typed policy-lowering decisions from build-results")
    parser.add_argument("results_file", help="Path to build-results JSON")
    parser.add_argument("--prs", default=None, help="Comma-separated PR numbers to include")
    parser.add_argument("--enrich", action="store_true", help="Emit full build-results JSON with prs[*].policy_lowering attached")
    parser.add_argument("-o", "--output", default=None, help="Write JSON to this file instead of stdout")
    args = parser.parse_args(argv[1:])

    with open(args.results_file, encoding="utf-8") as fh:
        results = json.load(fh)
    output = enrich_results(results, _parse_prs(args.prs)) if args.enrich else apply_policy(results, _parse_prs(args.prs))
    text = json.dumps(output, indent=2, sort_keys=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
