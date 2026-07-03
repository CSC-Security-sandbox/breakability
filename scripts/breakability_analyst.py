#!/usr/bin/env python3
"""
breakability_analyst.py - Compact PR comment renderer for breakability analysis.

Reads build-results.json and produces ~40-line comments per PR with collapsible
evidence details. Called by breakability-agent.yml workflow (line 346).
"""
import json
import sys
import os
from typing import Dict, Any, List, Optional
from verdict_contract import authoritative_verdict as _authoritative_verdict


# ── Normalizers ───────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _merge_risk_tag(pr: Dict[str, Any]) -> str:
    warning_count = 0
    signals = []
    probe = _normalize_probe(pr)
    reach = _normalize_reachability(pr)
    build = pr.get("build", {})
    test_norm = _normalize_test(pr.get("test", {}))
    det = pr.get("deterministic", {})
    changelog_norm = _normalize_changelog(det)

    if build.get("verdict") == "fail":
        warning_count += 1
        signals.append("build fail")
    if test_norm["verdict"] == "fail":
        warning_count += 1
        signals.append("test fail")
    if probe["state"] == "DIFFERENT":
        warning_count += 1
        signals.append("probe DIFFERENT")
    if reach.get("reached"):
        warning_count += 1
        signals.append("reachable")
    if changelog_norm["is_breaking"]:
        warning_count += 1
        signals.append("changelog breaking")

    evidence_layers = _count_evidence_layers(pr)
    ecosystem = pr.get("ecosystem", "")

    if warning_count >= 3:
        risk = "High"
        conf = "RC-High"
    elif warning_count >= 1:
        risk = "Medium"
        conf = "RC-Med"
    else:
        risk = "Low"
        conf = "RC-Low"

    if signals:
        evidence_str = " + ".join(signals)
    elif evidence_layers <= 1 and ecosystem != "actions":
        evidence_str = "limited evidence gathered"
    elif ecosystem == "actions":
        evidence_str = "CI-only action — no runtime impact"
    else:
        evidence_str = "all signals clean"
    return f"**Merge Risk:** {risk} (Evidence: {evidence_str} · Confidence: {conf})"


def _get_recommendation(pr: Dict) -> str:
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    pkg = pr.get("package", "unknown")
    dep_type = pr.get("dep_type", "dependency")
    probe = _normalize_probe(pr)
    reach_norm = _normalize_reachability(pr)
    reached = reach_norm["reached"]
    files = reach_norm["import_files"]
    det = pr.get("deterministic", {})
    changelog_norm = _normalize_changelog(det)
    test_norm = _normalize_test(pr.get("test", {}))

    if verdict in ("BUILD_FAILS", "BLOCKED"):
        build = pr.get("build", {})
        if build.get("verdict") == "pre_existing":
            return "Build has pre-existing failures (not caused by this upgrade). Review build infra separately."
        if test_norm["verdict"] == "fail":
            return "Fix build and test failures before merging."
        return "Fix build errors before merging."

    if test_norm["verdict"] == "fail":
        return f"Tests fail (exit {test_norm['exit_code']}). Investigate test failures before merging."

    if verdict == "SAFE":
        if dep_type in ("dev", "devDependency", "devDependencies"):
            return "Safe to merge — dev dependency with no production impact."
        if not reached:
            return "Safe to merge — not imported by production code."
        if probe["state"] == "SAME":
            return "Safe to merge — behavioral probe confirms identical runtime behavior."
        if changelog_norm["is_breaking"]:
            return "Changelog lists breaking changes. Review callsites, then merge."
        return "Safe to merge. Build passes and no breaking changes detected."

    parts = []
    if changelog_norm["is_breaking"]:
        bullets = changelog_norm["bullets"]
        if bullets:
            parts.append(f"Review changelog breaking changes ({bullets[0][:80]})")
        else:
            parts.append("Review the changelog for breaking changes")

    if probe["state"] == "DIFFERENT":
        parts.append("verify behavioral changes are compatible with your usage")

    if reached and files:
        file_ref = (f"`{files[0]}`" if len(files) == 1
                    else f"`{files[0]}` and {len(files)-1} other file(s)")
        parts.append(f"check callsites in {file_ref}")
    elif reached:
        parts.append("verify affected callsites are compatible")

    if not parts:
        parts.append(f"Review the changelog for {pkg}")

    return ". ".join(parts).rstrip(".") + ", then merge."


def _count_evidence_layers(pr: Dict) -> int:
    count = 0
    if pr.get("build", {}).get("verdict"):
        count += 1
    test_norm = _normalize_test(pr.get("test", {}))
    if test_norm["verdict"] not in [None, "skip"]:
        count += 1
    if (pr.get("deterministic", {}).get("api_changes") or 0) > 0:
        count += 1
    if pr.get("deterministic", {}).get("changelogSignal"):
        count += 1
    if pr.get("files_importing") or pr.get("deterministic", {}).get("files_importing"):
        count += 1
    if pr.get("behavioral_grade") or pr.get("deterministic", {}).get("probe"):
        count += 1
    if pr.get("ai_adjudication"):
        count += 1
    return count


def _per_layer_confidence(build_v, test_norm, api_changes, changelog_norm, reach, probe):
    """Compute per-layer confidence (HIGH/MEDIUM/LOW) with one-sentence rationale."""
    layers = {}

    if build_v in ("pass", "fail"):
        layers["Build"] = ("HIGH", "Definitive exit code from full build pipeline")
    elif build_v == "pre_existing":
        layers["Build"] = ("MEDIUM", "Build fails but failures pre-date this upgrade")
    else:
        layers["Build"] = ("LOW", "Build was not executed or status unknown")

    if test_norm["verdict"] == "pass":
        layers["Tests"] = ("HIGH", "Test suite ran and passed (exit 0)")
    elif test_norm["verdict"] == "fail":
        layers["Tests"] = ("HIGH", f"Test suite ran and failed (exit {test_norm['exit_code']})")
    elif test_norm["ran"]:
        layers["Tests"] = ("MEDIUM", "Tests ran but result is ambiguous")
    else:
        layers["Tests"] = ("LOW", "No test suite was executed")

    if api_changes > 0:
        layers["API Diff"] = ("HIGH", f"{api_changes} exported symbol change(s) detected")
    else:
        layers["API Diff"] = ("MEDIUM", "No symbol changes found; diff may not cover all APIs")

    if changelog_norm["is_breaking"]:
        layers["Changelog"] = ("HIGH", "Changelog explicitly declares breaking changes")
    elif changelog_norm["available"]:
        layers["Changelog"] = ("MEDIUM", "Changelog present but no breaking markers found")
    else:
        layers["Changelog"] = ("LOW", "No changelog available for this version range")

    if reach["reached"]:
        n = len(reach["import_files"])
        layers["Reachability"] = ("HIGH", f"Import scan found {n} file(s) using this package")
    else:
        layers["Reachability"] = ("HIGH", "Import scan confirms package is not referenced")

    if probe["state"] in ("SAME", "DIFFERENT"):
        layers["Probe"] = ("HIGH", f"Behavioral probe ran and reported {probe['state'].lower()}")
    else:
        layers["Probe"] = ("LOW", "Behavioral probe was not executed")

    return layers


def _build_per_layer_narrative(build, build_v, test_norm, api_changes, changelog_norm,
                                reach, probe, pkg, from_ver, to_ver):
    """Generate per-layer narrative paragraphs for the template fallback."""
    lines = []

    if build_v == "pass":
        lines.append(f"**Build** passed cleanly — `{pkg}@{to_ver}` integrates without errors.")
    elif build_v == "fail":
        lines.append(f"**Build** fails with `{pkg}@{to_ver}`. This upgrade introduces compilation or resolution errors that must be fixed before merging.")
    elif build_v == "pre_existing":
        lines.append(f"**Build** has pre-existing failures unrelated to this `{pkg}` upgrade.")

    if test_norm["verdict"] == "pass":
        lines.append(f"**Tests** pass (exit {test_norm['exit_code']}), confirming no regressions from this upgrade.")
    elif test_norm["verdict"] == "fail":
        lines.append(f"**Tests** fail (exit {test_norm['exit_code']}). Investigate whether failures are caused by `{pkg}` {to_ver} breaking changes.")
    elif test_norm["verdict"] == "skip":
        lines.append("**Tests** were not executed — test confidence is unavailable for this PR.")

    if api_changes > 0:
        lines.append(f"**API Diff** detected {api_changes} changed symbol(s) between {from_ver} and {to_ver}.")
    else:
        lines.append(f"**API Diff** shows no exported symbol changes between {from_ver} and {to_ver}.")

    if reach["reached"]:
        n_files = len(reach["import_files"])
        lines.append(f"**Reachability** confirms `{pkg}` is imported by {n_files} file(s) in this project — breaking changes could affect production code.")
    else:
        lines.append(f"**Reachability** shows `{pkg}` is not imported by any production source file.")

    if probe["state"] == "SAME":
        lines.append(f"**Probe** confirms runtime behavior is identical before and after the upgrade.")
    elif probe["state"] == "DIFFERENT":
        lines.append(f"**Probe** detected changed runtime behavior — verify the behavioral difference is acceptable.")

    return lines


def _build_expanded_layer_sections(build, build_v, test_norm, api_changes,
                                    changelog_norm, reach, probe, pkg, from_ver, to_ver,
                                    pr, layer_conf):
    """Per-layer H3 subsections for REVIEW/BLOCKED comments (target >=150 lines total)."""
    lines = []
    build_icon = {"pass": "✅", "fail": "❌", "pre_existing": "⚠️"}.get(build_v, "⬜")
    test_icon = {"pass": "✅", "fail": "❌", "skip": "⬜"}.get(test_norm["verdict"], "⬜")

    # ### Build Analysis
    lines += [
        f"### {build_icon} Build Analysis",
        f"**Status:** {build_icon} **{build_v.upper()}** | **Verification Level:** {layer_conf['Build'][0]}",
        "",
        "**What we checked:**",
        f"- ✅ Installed `{pkg}@{to_ver}` into the project",
        f"- ✅ Ran full build pipeline (`npm run build` / `go build ./...`)",
    ]
    if build_v == "pass":
        lines.append("- ✅ Build completed with zero errors")
    elif build_v == "fail":
        lines.append("- ❌ Build produced compilation or resolution errors")
    elif build_v == "pre_existing":
        lines.append("- ⚠️ Pre-existing build failures detected (unrelated to this upgrade)")
    build_output = (build.get("output_tail") or build.get("stdout") or "").strip()
    if build_output:
        lines += ["", "**Build Output:**", "```", build_output[:400], "```"]
    lines += ["", f"**Confidence:** **{layer_conf['Build'][0]}** — {layer_conf['Build'][1]}", ""]

    # ### Test Analysis
    lines += [
        f"### {test_icon} Test Analysis",
        f"**Status:** {test_icon} **{test_norm['verdict'].upper()}** | **Verification Level:** {layer_conf['Tests'][0]}",
        "",
        "**What we checked:**",
    ]
    if test_norm["ran"] and test_norm["verdict"] in ("pass", "fail"):
        lines += [
            "- ✅ Executed project test suite against the upgraded dependency",
            f"- {'✅' if test_norm['verdict'] == 'pass' else '❌'} Test exit code: {test_norm['exit_code']}",
        ]
        if test_norm["verdict"] == "fail":
            lines.append("- ❌ Test failures may indicate breaking changes in the upgrade")
    else:
        lines += [
            "- ⬜ No test suite was executed for this PR",
            "- ⬜ Test-based confidence is unavailable",
        ]
    test_data = pr.get("test", {})
    test_output = (test_data.get("output_tail") or test_data.get("stdout") or "").strip()
    if test_output and test_norm["verdict"] == "fail":
        lines += ["", "**Test Output:**", "```", test_output[:400], "```"]
    lines += ["", f"**Confidence:** **{layer_conf['Tests'][0]}** — {layer_conf['Tests'][1]}", ""]

    # ### API Diff Analysis
    api_icon = "⚠️" if api_changes > 0 else "✅"
    lines += [
        f"### {api_icon} API Diff Analysis",
        f"**Status:** {api_icon} **{api_changes} change(s)** | **Verification Level:** {layer_conf['API Diff'][0]}",
        "",
        "**What we checked:**",
        f"- ✅ Compared exported symbols between {from_ver} and {to_ver}",
    ]
    if api_changes > 0:
        lines.append(f"- ⚠️ {api_changes} exported symbol(s) changed — review for breaking signature changes")
    else:
        lines.append("- ✅ No exported symbol changes detected")
    lines += ["", f"**Confidence:** **{layer_conf['API Diff'][0]}** — {layer_conf['API Diff'][1]}", ""]

    # ### Changelog Analysis
    cl_icon = "⚠️" if changelog_norm["is_breaking"] else "✅" if changelog_norm["available"] else "⬜"
    cl_status = "BREAKING" if changelog_norm["is_breaking"] else "CLEAN" if changelog_norm["available"] else "UNAVAILABLE"
    lines += [
        f"### {cl_icon} Changelog Analysis",
        f"**Status:** {cl_icon} **{cl_status}** | **Verification Level:** {layer_conf['Changelog'][0]}",
        "",
        "**What we checked:**",
    ]
    if changelog_norm["available"]:
        lines.append("- ✅ Parsed release notes and changelog for breaking-change markers")
        if changelog_norm["is_breaking"]:
            lines.append("- ⚠️ Changelog explicitly declares breaking changes or deprecations")
        else:
            lines.append("- ✅ No breaking changes declared in release notes")
        if changelog_norm["bullets"]:
            lines.append("")
            lines.append("**Key changelog entries:**")
            for bullet in changelog_norm["bullets"][:3]:
                lines.append(f"- {bullet[:120]}")
    else:
        lines += [
            "- ⬜ No changelog found for this version range",
            "- ⬜ Cannot verify whether breaking changes were declared",
        ]
    lines += ["", f"**Confidence:** **{layer_conf['Changelog'][0]}** — {layer_conf['Changelog'][1]}", ""]

    # ### Reachability Analysis
    reach_icon = "⚠️" if reach["reached"] else "✅"
    n_files = len(reach["import_files"])
    lines += [
        f"### {reach_icon} Reachability Analysis",
        f"**Status:** {reach_icon} **{'REACHED' if reach['reached'] else 'NOT REACHED'}** | **Verification Level:** {layer_conf['Reachability'][0]}",
        "",
        "**What we checked:**",
        f"- ✅ Scanned project source files for imports of `{pkg}`",
    ]
    if reach["reached"]:
        lines += [
            f"- ⚠️ Found {n_files} file(s) importing this package",
            "- ⚠️ Breaking changes could affect production code paths",
        ]
        if reach["import_files"][:5]:
            lines.append("")
            lines.append("**Files importing this package:**")
            for f in reach["import_files"][:5]:
                lines.append(f"- `{f}`")
            if n_files > 5:
                lines.append(f"- ... and {n_files - 5} more")
    else:
        lines += [
            "- ✅ Package is not imported by any production source file",
            "- ✅ Breaking changes have no direct production impact",
        ]
    lines += ["", f"**Confidence:** **{layer_conf['Reachability'][0]}** — {layer_conf['Reachability'][1]}", ""]

    # ### Probe Analysis
    probe_icon = {"SAME": "✅", "DIFFERENT": "⚠️"}.get(probe["state"], "⬜")
    probe_status = probe["state"].replace("_", " ")
    lines += [
        f"### {probe_icon} Behavioral Probe Analysis",
        f"**Status:** {probe_icon} **{probe_status}** | **Verification Level:** {layer_conf['Probe'][0]}",
        "",
        "**What we checked:**",
    ]
    if probe["state"] == "SAME":
        lines += [
            f"- ✅ Compared runtime behavior between {from_ver} and {to_ver}",
            "- ✅ Runtime exports are identical — no behavioral regression",
        ]
    elif probe["state"] == "DIFFERENT":
        lines += [
            f"- ⚠️ Compared runtime behavior between {from_ver} and {to_ver}",
            "- ⚠️ Runtime behavior differs — verify the change is acceptable",
        ]
        probe_ev = probe["evidence"]
        changed = probe_ev.get("changed_behavior") or probe_ev.get("rationale") or ""
        if changed:
            lines += ["", f"**Behavioral difference:** {changed[:200]}"]
    else:
        lines += [
            "- ⬜ Behavioral probe was not executed",
            "- ⬜ No runtime comparison available",
        ]
    lines += ["", f"**Confidence:** **{layer_conf['Probe'][0]}** — {layer_conf['Probe'][1]}", ""]

    return lines


def _build_risk_assessment(pr, verdict, build_v, test_norm, api_changes,
                           changelog_norm, reach, probe, pkg, from_ver, to_ver,
                           dep_type, bump):
    """Build a risk assessment section for REVIEW/BLOCKED PRs."""
    lines = ["### Risk Assessment", ""]

    lines.append(f"**Upgrade:** `{pkg}` {from_ver} → {to_ver} ({bump} bump, {dep_type})")

    risk_factors = []
    if build_v == "fail":
        risk_factors.append("build fails with the new version")
    if test_norm["verdict"] == "fail":
        risk_factors.append(f"test suite fails (exit {test_norm['exit_code']})")
    if changelog_norm["is_breaking"]:
        risk_factors.append("changelog declares breaking changes")
    if probe["state"] == "DIFFERENT":
        risk_factors.append("behavioral probe detected runtime differences")
    if api_changes > 0:
        risk_factors.append(f"{api_changes} exported symbol(s) changed")
    if reach["reached"]:
        n = len(reach["import_files"])
        risk_factors.append(f"package is imported by {n} production file(s)")

    if risk_factors:
        lines.append("")
        lines.append("**Signals requiring attention:**")
        for factor in risk_factors:
            lines.append(f"- {factor}")

    mitigations = []
    if build_v == "pass":
        mitigations.append("build passes cleanly")
    if test_norm["verdict"] == "pass":
        mitigations.append("full test suite passes")
    if probe["state"] == "SAME":
        mitigations.append("runtime behavior is identical")
    if not reach["reached"]:
        mitigations.append("package is not imported by production code")

    if mitigations:
        lines.append("")
        lines.append("**Positive signals:**")
        for m in mitigations:
            lines.append(f"- {m}")

    ecosystem = pr.get("ecosystem", "npm")
    lines.append("")
    lines.append(f"**Ecosystem:** {ecosystem} · **Scope:** {dep_type} · **Bump:** {bump}")

    return lines


def _build_numbered_recommendations(pr):
    """Generate numbered recommendation steps."""
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    probe = _normalize_probe(pr)
    reach_norm = _normalize_reachability(pr)
    det = pr.get("deterministic", {})
    changelog_norm = _normalize_changelog(det)
    test_norm = _normalize_test(pr.get("test", {}))
    pkg = pr.get("package", "unknown")
    build = pr.get("build", {})

    steps = []
    if build.get("verdict") == "fail":
        steps.append(f"Fix build errors introduced by `{pkg}` upgrade")
    if test_norm["verdict"] == "fail":
        steps.append(f"Investigate test failures (exit {test_norm['exit_code']})")
    if changelog_norm["is_breaking"] and changelog_norm["bullets"]:
        steps.append(f"Review changelog breaking changes: {changelog_norm['bullets'][0][:80]}")
    if probe["state"] == "DIFFERENT":
        steps.append("Verify behavioral changes are compatible with your usage")
    if reach_norm["reached"] and reach_norm["import_files"]:
        n = len(reach_norm["import_files"])
        steps.append(f"Check callsites in {n} importing file(s)")
    if verdict == "SAFE" and not steps:
        steps.append("Safe to merge — no action required")
    elif not steps:
        steps.append(f"Review the changelog for `{pkg}` before merging")
    steps.append("Merge when confident")
    return steps


# ── Compact renderer ─────────────────────────────────────────────────────────

def _synthesize_explanation(pr: Dict) -> str:
    """Generate plain-English explanation from signal data.
    Deterministic replacement for the AI arbiter layer."""
    parts = []
    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    build = pr.get("build", {})
    probe = _normalize_probe(pr)
    reach = _normalize_reachability(pr)
    det = pr.get("deterministic", {})
    changelog_norm = _normalize_changelog(det)
    dep_type = pr.get("dep_type", "dependency")

    test_norm = _normalize_test(pr.get("test", {}))

    if build.get("verdict") == "pass":
        parts.append("Build passes with all dependencies resolving.")
    elif build.get("verdict") == "fail":
        parts.append("Build fails — fix build errors before merging.")
    elif build.get("verdict") == "pre_existing":
        parts.append("Build has pre-existing failures not caused by this upgrade.")
    elif build.get("verdict") == "pre_existing_plus_new":
        parts.append("Build has new errors introduced by this upgrade on top of pre-existing failures.")

    if test_norm["verdict"] == "fail":
        parts.append(f"Tests fail (exit {test_norm['exit_code']}) — investigate before merging.")

    if verdict == "SAFE":
        if dep_type in ("dev", "devDependency", "devDependencies"):
            parts.append("Dev dependency with no production impact.")
        elif not reach["reached"]:
            parts.append("Package is not imported by production code.")
        elif probe["state"] == "SAME":
            parts.append("Behavioral probe confirms runtime exports are identical.")
        else:
            if changelog_norm["is_breaking"]:
                parts.append("Breaking changes listed but assessed safe at current usage.")
            else:
                parts.append("No breaking changes detected.")
        if changelog_norm["is_breaking"] and changelog_norm["bullets"]:
            bullet = changelog_norm["bullets"][0]
            if len(bullet) > 100:
                bullet = bullet[:97] + "..."
            parts.append(f"Changelog notes: {bullet}")
            if not reach["reached"]:
                parts.append("Package is unreachable so this has no production impact.")
    elif verdict == "REVIEW":
        if probe["state"] == "DIFFERENT":
            parts.append("Behavioral probe confirms runtime behavior has changed.")
        if changelog_norm["is_breaking"] and changelog_norm["bullets"]:
            bullet = changelog_norm["bullets"][0]
            if len(bullet) > 100:
                bullet = bullet[:97] + "..."
            parts.append(f"Changelog: {bullet}")
        if reach["reached"]:
            files = reach["import_files"]
            if files:
                parts.append(f"Package is imported by {len(files)} production file(s) — verify callsite compatibility.")
    elif verdict in ("BUILD_FAILS", "BLOCKED"):
        parts.append("Resolve build issues before this upgrade can proceed.")

    build_output = build.get("output_tail", "") or build.get("stdout", "")
    import re
    vuln_match = re.search(r'(\d+)\s+(high|critical)\s+severity\s+vulnerabilit', build_output, re.IGNORECASE)
    if vuln_match:
        parts.append(f"⚠️ npm audit: {vuln_match.group(0)}ies found.")

    return " ".join(parts) if parts else "Review required for this upgrade."


def _render_compact(pr: Dict, cross_deps: Optional[List[Dict]] = None) -> str:
    """Render a compact PR comment (~40 lines)."""
    from datetime import date

    verdict_norm = _normalize_verdict(pr)
    verdict = verdict_norm["verdict"]
    pkg = pr.get("package", "unknown")
    from_ver = pr.get("from", "?")
    to_ver = pr.get("to", "?")
    bump = pr.get("bump", "unknown")
    dep_type = pr.get("dep_type", "dependency")

    emoji = {"SAFE": "✅", "REVIEW": "🟠", "BUILD_FAILS": "❌", "BLOCKED": "🔴"}.get(verdict, "⚠️")
    verification_level = pr.get("verification_level", -1)
    vlevel_labels = {0: "L0 Unresolved", 1: "L1 Dep-resolved", 2: "L2 Type-checked",
                     3: "L3 Symbols-verified", 4: "L4 Tests-pass", 5: "L5 Fully-verified"}
    vlevel_str = vlevel_labels.get(verification_level, "")
    merge_risk = _merge_risk_tag(pr)

    build = pr.get("build", {})
    build_v = build.get("verdict", "unknown")
    build_icon = {"pass": "✅", "fail": "❌", "pre_existing": "⚠️"}.get(build_v, "⬜")

    test_norm = _normalize_test(pr.get("test", {}))
    test_icon = {"pass": "✅", "fail": "❌", "skip": "⬜"}.get(test_norm["verdict"], "⬜")
    test_suffix = f" (exit {test_norm['exit_code']})" if test_norm["verdict"] == "fail" else ""

    probe = _normalize_probe(pr)
    probe_state_display = probe["state"].lower().replace("_", " ")
    probe_icon = {"SAME": "✅", "DIFFERENT": "⚠️"}.get(probe["state"], "⬜")

    det = pr.get("deterministic", {})
    reach = _normalize_reachability(pr)
    changelog_norm = _normalize_changelog(det)
    api_changes = det.get("api_changes") or 0

    is_ambient = any("ambient" in str(f).lower() for f in reach["import_files"])
    reach_file_count = len(reach["import_files"]) or len(set(u.get("file", "") for u in reach["usages"]))
    if is_ambient:
        reach_text = "all TS files (ambient)"
    elif reach["reached"]:
        reach_text = f"{reach_file_count} files"
    else:
        reach_text = "not reached"
    cl_icon = "⚠️" if changelog_norm["is_breaking"] else "✅" if changelog_norm["available"] else "⬜"
    cl_text = "breaking" if changelog_norm["is_breaking"] else "clean" if changelog_norm["available"] else "n/a"

    explanation = _synthesize_explanation(pr)
    recommendation = _get_recommendation(pr)

    vlevel_badge = f" · Verification: {vlevel_str}" if vlevel_str else ""
    lines = [
        "<!-- breakability-check -->",
        f"## {emoji} {verdict} — `{pkg}` {from_ver} → {to_ver} · {dep_type} · {bump}",
        merge_risk + vlevel_badge,
        "",
        f"**Build:** {build_icon} {build_v} · **Tests:** {test_icon} {test_norm['verdict']}{test_suffix} · **Probe:** {probe_icon} {probe_state_display}",
        f"**Reachability:** {reach_text} · **Changelog:** {cl_icon} {cl_text} · **API Diff:** {api_changes} changes",
        "",
        "### What this means",
        explanation,
        "",
        "### Recommendation",
        "",
    ]
    rec_steps = _build_numbered_recommendations(pr)
    for i, step in enumerate(rec_steps, 1):
        lines.append(f"{i}. {step}")
    lines.append("")

    cl_detail = changelog_norm["bullets"][0][:80] if changelog_norm["bullets"] else changelog_norm["status"]
    probe_ev = probe["evidence"]
    if probe["state"] == "DIFFERENT":
        probe_detail = probe_ev.get("changed_behavior", "") or probe_ev.get("rationale", "") or "behavior changed"
        probe_detail = probe_detail[:120]
    elif probe["state"] == "SAME":
        probe_detail = "behavior unchanged"
    else:
        probe_detail = "—"
    test_detail = test_norm["reason"] if test_norm["verdict"] != "pass" else f"exit {test_norm['exit_code']}"

    layer_conf = _per_layer_confidence(build_v, test_norm, api_changes, changelog_norm, reach, probe)

    lines += [
        "### Evidence Summary",
        "",
        "| Layer | Signal | Detail | Confidence |",
        "|-------|--------|--------|------------|",
        f"| Build | {build_icon} {build_v} | exit {build.get('pr_exit', build.get('main_exit', '?'))} | {layer_conf['Build'][0]} — {layer_conf['Build'][1]} |",
        f"| Tests | {test_icon} {test_norm['verdict']} | {test_detail} | {layer_conf['Tests'][0]} — {layer_conf['Tests'][1]} |",
        f"| API Diff | {'⚠️ breaking' if api_changes > 0 else '✅ clean'} | {api_changes} symbol(s) | {layer_conf['API Diff'][0]} — {layer_conf['API Diff'][1]} |",
        f"| Changelog | {cl_icon} {cl_text} | {cl_detail} | {layer_conf['Changelog'][0]} — {layer_conf['Changelog'][1]} |",
        f"| Reachability | {'⚠️ reached' if reach['reached'] else '✅ not reached'} | {reach_file_count} imports | {layer_conf['Reachability'][0]} — {layer_conf['Reachability'][1]} |",
        f"| Probe | {probe_icon} {probe_state_display} | {probe_detail} | {layer_conf['Probe'][0]} — {layer_conf['Probe'][1]} |",
    ]

    ai_adj = pr.get("ai_adjudication", {})
    if ai_adj and isinstance(ai_adj, dict):
        ai_verdict = ai_adj.get("final_verdict", ai_adj.get("verdict", "—"))
        ai_conf = ai_adj.get("confidence", "—")
        ai_icon = {"SAFE": "✅", "REVIEW": "🟠"}.get(ai_verdict, "⬜")
        lines.append(f"| AI Arbiter | {ai_icon} {ai_verdict} | confidence: {ai_conf} |")
    else:
        lines.append("| AI Arbiter | ⬜ not run | — |")

    lines.append("")

    lines += [
        "### How we checked",
        "",
        f"- **Build**: Installed `{pkg}@{to_ver}` and ran full build pipeline",
        f"- **Tests**: {'Ran project test suite' if test_norm['ran'] else 'No test suite executed'}",
        f"- **API Diff**: Compared exported symbols between {from_ver} and {to_ver}",
        f"- **Changelog**: {'Parsed release notes for breaking-change markers' if changelog_norm['available'] else 'No changelog found for this version range'}",
        f"- **Reachability**: Scanned project source for imports of `{pkg}`",
        f"- **Probe**: {'Compared runtime behavior before/after upgrade' if probe['state'] != 'NOT_RUN' else 'Behavioral probe was not executed'}",
        "",
    ]

    if verdict in ("REVIEW", "BUILD_FAILS", "BLOCKED"):
        lines += _build_expanded_layer_sections(build, build_v, test_norm, api_changes,
                                                changelog_norm, reach, probe, pkg, from_ver, to_ver,
                                                pr, layer_conf)
        lines.append("")
        lines += _build_risk_assessment(pr, verdict, build_v, test_norm, api_changes,
                                         changelog_norm, reach, probe, pkg, from_ver, to_ver, dep_type, bump)
        lines.append("")
    else:
        lines += _build_per_layer_narrative(build, build_v, test_norm, api_changes, changelog_norm,
                                             reach, probe, pkg, from_ver, to_ver)
        lines.append("")

    lines += [
        "<details><summary>Verdict logic</summary>",
        "",
        "```",
        f"build      = {build_v.upper()}",
        f"tests      = {test_norm['verdict'].upper()}",
        f"probe      = {probe['state']}",
        f"reachable  = {str(reach['reached']).upper()}",
        f"changelog  = {'BREAKING' if changelog_norm['is_breaking'] else 'CLEAN'}",
        f"verdict    = {verdict}",
        "```",
        "",
        "</details>",
        "",
    ]

    ecosystem = pr.get("ecosystem", "npm")
    lines += ["<details><summary>Verification commands</summary>", "", "```bash"]
    if ecosystem == "gomod":
        lines += [
            f"# Install and build with the new version",
            f"go get {pkg}@v{to_ver}",
            f"go build ./...",
            "",
            f"# Run tests",
            f"go test ./...",
            "",
            f"# Check package docs",
            f"go doc {pkg}",
            "",
            f"# Check your imports",
            f'grep -r "{pkg}" --include="*.go" -l .',
        ]
    elif ecosystem == "actions":
        lines += [
            f"# Check which workflows use this action",
            f'grep -r "uses: {pkg}" --include="*.yml" --include="*.yaml" -l .github/',
        ]
    else:
        lines += [
            f"# Install and build with the new version",
            f"npm install {pkg}@{to_ver}",
            f"npm run build",
            "",
            f"# Run tests",
            f"npm test",
            "",
            f"# Check what changed in the API",
            f"npm info {pkg} --json | jq '.versions'",
            "",
            f"# Check your imports",
            f"grep -r '{pkg}' --include='*.ts' --include='*.js' -l .",
        ]
    lines += ["```", "", "</details>", ""]

    build_output = build.get("output_tail", "")
    if build_output:
        lines += [
            "<details><summary>🔨 Build output</summary>",
            "",
            "```",
            build_output[:500],
            "```",
            "",
            "</details>",
            "",
        ]

    test_data = pr.get("test", {})
    test_output = (test_data.get("output_tail") or test_data.get("stdout") or test_data.get("output") or "").strip()
    if test_norm["verdict"] == "fail" and test_output:
        lines += [
            "<details><summary>🧪 Test output</summary>",
            "",
            "```",
            test_output[:500],
            "```",
            "",
            "</details>",
            "",
        ]

    if probe["state"] == "DIFFERENT":
        old_out = probe_ev.get("observed_from", "") or probe_ev.get("old_output", "")
        new_out = probe_ev.get("observed_to", "") or probe_ev.get("new_output", "")
        changed_summary = probe_ev.get("changed_behavior", "") or probe_ev.get("summary", "") or probe_ev.get("rationale", "")
        old_hash = probe_ev.get("old_hash", "")
        new_hash = probe_ev.get("new_hash", "")
        if not old_out and not new_out and old_hash and new_hash:
            old_out = f"sha256:{old_hash}"
            new_out = f"sha256:{new_hash}"
        if old_out or new_out or changed_summary:
            lines.append("<details><summary>🔬 Probe diff — what changed</summary>")
            lines.append("")
            if changed_summary:
                lines.append(f"**Change:** {changed_summary[:300]}")
                lines.append("")
            if old_out:
                lines.append(f"**Before:** `{old_out[:200]}`")
            if new_out:
                lines.append(f"**After:** `{new_out[:200]}`")
            evidence_text = probe_ev.get("evidence", "")
            if evidence_text:
                lines.append("")
                lines.append(f"**Evidence:** {evidence_text[:300]}")
            lines += ["", "</details>", ""]

    import_list = reach["import_files"]
    usage_refs = []
    if reach["usages"]:
        for u in reach["usages"]:
            uf = u.get("file", "")
            ul = u.get("line")
            if uf:
                usage_refs.append(f"{uf}:{ul}" if ul else uf)
        usage_refs = sorted(set(usage_refs))
    if not import_list and usage_refs:
        import_list = usage_refs
    elif import_list and usage_refs:
        file_to_ref = {}
        for ref in usage_refs:
            base = ref.split(":")[0]
            file_to_ref.setdefault(base, []).append(ref)
        enriched = []
        for f in import_list:
            if f in file_to_ref:
                enriched.extend(file_to_ref[f])
            else:
                enriched.append(f)
        import_list = enriched
    if import_list:
        lines.append(f"<details><summary>📁 Files importing this package ({len(import_list)})</summary>")
        lines.append("")
        for f in import_list[:10]:
            lines.append(f"- `{f}`")
        if len(import_list) > 10:
            lines.append(f"- ... and {len(import_list) - 10} more")
        lines += ["", "</details>", ""]

    if changelog_norm["is_breaking"] and changelog_norm["bullets"]:
        lines.append("<details><summary>📋 Changelog breaking changes</summary>")
        lines.append("")
        for b in changelog_norm["bullets"][:5]:
            lines.append(f"- {b}")
        lines += ["", "</details>", ""]

    fixes_cves = pr.get("fixes_cves") or []
    cve_details = pr.get("cve_details") or []
    all_cves = []
    seen_cve_ids = set()
    for cve in fixes_cves:
        cid = cve.get("cve_id") or ""
        if cid and cid not in seen_cve_ids:
            seen_cve_ids.add(cid)
            sev = cve.get("severity", "unknown")
            score = cve.get("cvss_score", "")
            url = cve.get("advisory_url", "")
            patched = cve.get("first_patched_version", "")
            all_cves.append({"id": cid, "severity": sev, "score": score, "url": url, "patched": patched, "fixes": True})
    for cve in cve_details:
        cid = cve.get("cve_id") or cve.get("ghsa_id") or ""
        if cid and cid not in seen_cve_ids:
            seen_cve_ids.add(cid)
            sev = cve.get("severity", "unknown")
            score = cve.get("cvss_score", "")
            url = cve.get("advisory_url", "")
            all_cves.append({"id": cid, "severity": sev, "score": score, "url": url, "patched": "", "fixes": False})
    if all_cves:
        sev_icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}.get
        lines.append("### Security Advisories")
        lines.append("")
        for cve in all_cves:
            icon = sev_icon(cve["severity"], "⚪")
            score_str = f" · CVSS {cve['score']}" if cve["score"] else ""
            link = f" · [advisory]({cve['url']})" if cve["url"] else ""
            fix_str = " — **fixed by this PR**" if cve["fixes"] else ""
            patched_str = f" (patched in {cve['patched']})" if cve["patched"] else ""
            lines.append(f"- {icon} **{cve['id']}** ({cve['severity']}{score_str}){patched_str}{fix_str}{link}")
        lines.append("")

    pr_num = str(pr.get("pr_num", ""))
    if cross_deps:
        related = [d for d in cross_deps
                   if str(d.get("pr_a")) == pr_num or str(d.get("pr_b")) == pr_num]
        if related:
            lines.append("### Coupled PRs")
            lines.append("")
            for dep in related:
                other = dep["pr_b"] if str(dep["pr_a"]) == pr_num else dep["pr_a"]
                lines.append(f"- PR #{other}: {dep.get('reason', '')} — {dep.get('merge_order', '')}")
            lines.append("")

    merge_plan_issue = os.environ.get("MERGE_PLAN_ISSUE", "")
    run_url = os.environ.get("ANALYSIS_RUN_URL", "")

    lines.append("---")
    footer = (f"Mode: Deterministic + Behavioral Probe · Model: template-fallback · "
              f"Analyzed: {date.today().isoformat()}")
    lines.append(footer)
    if merge_plan_issue:
        lines.append(f"Merge plan: #{merge_plan_issue}")
    if run_url:
        lines.append(f"[Analysis run]({run_url})")

    return "\n".join(lines)


def render_pr_comment(pr: Dict[str, Any], cross_deps: Optional[List[Dict]] = None) -> str:
    """Render compact PR comment (~40 lines)."""
    return _render_compact(pr, cross_deps=cross_deps)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Render breakability analysis PR comments")
    parser.add_argument("build_results", help="Path to build-results.json")
    parser.add_argument("--pr", type=str, help="Render only specific PR number")
    parser.add_argument("--stdout", action="store_true", help="Write to stdout instead of files")
    args = parser.parse_args()

    with open(args.build_results) as f:
        data = json.load(f)

    prs_dict = data.get("prs", {})
    results_array = data.get("results", [])

    if results_array:
        results = results_array
    elif prs_dict:
        results = []
        for pr_num_str, pr_data in prs_dict.items():
            if isinstance(pr_data, dict):
                pr_data.setdefault("pr_num", pr_num_str)
                results.append(pr_data)
    else:
        print("No results found in build-results.json (checked 'prs' dict and 'results' array)", file=sys.stderr)
        sys.exit(1)

    cross_deps = data.get("cross_pr_deps") or []

    if args.pr:
        results = [pr for pr in results if str(pr.get("pr_num")) == args.pr]
        if not results:
            print(f"PR #{args.pr} not found in results", file=sys.stderr)
            sys.exit(1)

    for pr in results:
        pr_num = pr.get("pr_num")
        if not pr_num:
            continue

        comment = render_pr_comment(pr, cross_deps=cross_deps)

        if args.stdout:
            print(comment)
        else:
            output_file = f"/tmp/pr-{pr_num}-comment.md"
            with open(output_file, "w") as f:
                f.write(comment)
            print(f"✅ Rendered PR #{pr_num} comment to {output_file}")


if __name__ == "__main__":
    main()
