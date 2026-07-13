"""
rendering.normalizers — Normalize raw PR signal data into consistent structures.

Each function takes a raw dict (or sub-dict) from build-results.json and returns
a cleaned, consistently-keyed dict that downstream helpers and renderers rely on.
"""
from typing import Dict, Any
from verdict_contract import authoritative_verdict as _authoritative_verdict

__all__ = [
    "_normalize_verdict",
    "_normalize_changelog",
    "_normalize_test",
    "_normalize_probe",
    "_normalize_reachability",
]


def _normalize_verdict(pr: Dict) -> Dict[str, str]:
    v = _authoritative_verdict(pr)
    return {
        "verdict": v.get("verdict", "REVIEW"),
        "confidence": v.get("confidence", "MEDIUM"),
        "severity": v.get("severity", "medium"),
        "priority": v.get("priority", "P2"),
    }


def _normalize_changelog(det: Dict) -> Dict[str, Any]:
    cl = det.get("changelogSignal")

    if not cl:
        return {"status": "missing", "bullets": [], "is_breaking": False, "available": False}

    if isinstance(cl, str):
        return {
            "status": cl,
            "bullets": [],
            "is_breaking": cl == "breaking",
            "available": cl != "missing"
        }

    if not isinstance(cl, dict):
        return {"status": "missing", "bullets": [], "is_breaking": False, "available": False}

    status = cl.get("status", "unknown")
    bullets = cl.get("bullets", [])

    if bullets is None:
        bullets = []
    elif isinstance(bullets, str):
        bullets = [bullets] if bullets else []
    elif not isinstance(bullets, list):
        bullets = []

    has_breaking_in_bullets = any(
        "BREAKING" in str(bullet).upper() or "BREAK" in str(bullet).upper()
        for bullet in bullets
    )

    _negation_patterns = ["no api change", "no breaking change", "bug fix and maintenance"]
    all_bullets_negated = (
        status == "breaking" and bullets and
        all(any(neg in str(b).lower() for neg in _negation_patterns) for b in bullets)
    )
    if all_bullets_negated:
        status = "clean"
        has_breaking_in_bullets = False

    is_breaking = status == "breaking" or has_breaking_in_bullets
    available = status != "missing" or len(bullets) > 0

    return {
        "status": status,
        "bullets": bullets,
        "is_breaking": is_breaking,
        "available": available
    }


def _normalize_test(test: Dict) -> Dict[str, Any]:
    if not test:
        return {"verdict": "skip", "exit_code": -1, "ran": False, "reason": "No test data"}

    if "ran" in test:
        ran = test.get("ran", False)
        exit_code = test.get("exit")
        if exit_code is None:
            exit_code = test.get("main_test_exit", -1)

        if not ran:
            verdict = "skip"
            reason = test.get("reason", "Tests not executed")
        elif exit_code == 0:
            verdict = "pass"
            reason = "All tests passed"
        elif exit_code is None:
            verdict = "skip"
            reason = "Test execution status unknown"
        else:
            output = test.get("output_tail", "")
            if "no test specified" in output or "Error: no test specified" in output:
                verdict = "skip"
                ran = False
                reason = "No test suite configured"
            else:
                verdict = "fail"
                reason = f"Tests failed with exit code {exit_code}"

        return {"verdict": verdict, "exit_code": exit_code, "ran": ran, "reason": reason}

    verdict = test.get("verdict", "skip")
    exit_code = test.get("exit_code", -1)
    reason = test.get("reason", "Test execution status")
    ran = verdict == "pass" or verdict == "fail"

    return {"verdict": verdict, "exit_code": exit_code, "ran": ran, "reason": reason}


def _normalize_probe(pr: Dict) -> Dict[str, Any]:
    probe = pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe", {})

    if not probe:
        return {"state": "NOT_RUN", "same_behavior": None, "evidence": {}}

    build_verdict = (pr.get("build") or {}).get("verdict", "")
    if build_verdict in ("fail", "pre_existing_plus_new"):
        return {"state": "PROBE_FAILED", "same_behavior": None, "evidence": probe}

    same_behavior = probe.get("same_behavior")

    if same_behavior is None:
        behavior_changed = probe.get("behavior_changed") or probe.get("changed_behavior")
        if behavior_changed is True:
            same_behavior = False
        elif behavior_changed is False:
            same_behavior = True
        elif behavior_changed == "unverified":
            same_behavior = None

    if same_behavior is None and "different" in probe:
        different = probe.get("different")
        if different is True:
            same_behavior = False
        elif different is False:
            same_behavior = True

    if same_behavior is True:
        state = "SAME"
    elif same_behavior is False:
        state = "DIFFERENT"
    else:
        old_sha = probe.get("old_sha256", "")[:16]
        new_sha = probe.get("new_sha256", "")[:16]
        if old_sha and new_sha:
            if old_sha == new_sha:
                state = "SAME"
                same_behavior = True
            else:
                state = "DIFFERENT"
                same_behavior = False
        else:
            state = "NOT_RUN"

    return {
        "state": state,
        "same_behavior": same_behavior,
        "evidence": probe
    }


def _normalize_reachability(pr: Dict) -> Dict[str, Any]:
    det = pr.get("deterministic") or {}
    usages = det.get("usages")
    if not isinstance(usages, list):
        usages = []
    import_files = pr.get("files_importing")
    if not isinstance(import_files, list):
        import_files = det.get("files_importing")
        if not isinstance(import_files, list):
            import_files = []
    reached = len(import_files) > 0 or len(usages) > 0
    pkg = pr.get("package", "")
    if not reached and pkg.startswith("@types/"):
        dep_type = pr.get("dep_type", "")
        if dep_type in ("production", "dependency", "dependencies"):
            reached = True
            import_files = ["(ambient type declarations — all TypeScript files)"]
    return {"usages": usages, "import_files": import_files, "reached": reached}
