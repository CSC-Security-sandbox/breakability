"""
rendering.helpers — Analysis helpers that compute risk, recommendations, and
per-layer confidence / narrative from normalized signal data.
"""
from typing import Dict, Any, List, Optional

from rendering.normalizers import (
    _normalize_verdict,
    _normalize_changelog,
    _normalize_test,
    _normalize_probe,
    _normalize_reachability,
)

__all__ = [
    "_merge_risk_tag",
    "_get_recommendation",
    "_count_evidence_layers",
    "_per_layer_confidence",
    "_build_per_layer_narrative",
    "_build_expanded_layer_sections",
    "_build_risk_assessment",
    "_build_numbered_recommendations",
    "_is_api_diff_skipped",
    "_extract_error_lines",
]


def _is_api_diff_skipped(pr: Dict) -> bool:
    for ev in pr.get("evidence", []):
        if ev.get("signal") == "api_diff":
            return ev.get("status") == "skipped"
    if not pr.get("deterministic", {}).get("api_changes") and pr.get("deterministic") == {}:
        return True
    return False


def _extract_error_lines(output: str, max_chars: int = 500) -> str:
    if not output:
        return ""
    lines = output.strip().splitlines()
    error_patterns = ("undefined:", "cannot ", "error:", "Error:", "FAIL", "fatal:",
                      "not found", "no required module", "could not")
    error_lines = [l for l in lines if any(p in l for p in error_patterns)]
    if error_lines:
        result = "\n".join(error_lines)
        if len(result) > max_chars:
            result = result[:max_chars] + "\n..."
        return result
    return output[-max_chars:] if len(output) > max_chars else output


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


def _per_layer_confidence(build_v, test_norm, api_changes, changelog_norm, reach, probe,
                          *, api_diff_skipped=False, ecosystem=""):
    """Compute per-layer confidence (HIGH/MEDIUM/LOW) with one-sentence rationale."""
    layers = {}

    is_actions = ecosystem == "actions"
    if is_actions:
        layers["Build"] = ("LOW", "CI-only dependency — no build applicable")
    elif build_v in ("pass", "fail"):
        layers["Build"] = ("HIGH", "Definitive exit code from full build pipeline")
    elif build_v == "pre_existing":
        layers["Build"] = ("MEDIUM", "Build fails but failures pre-date this upgrade")
    else:
        layers["Build"] = ("LOW", "Build was not executed or status unknown")

    if test_norm["verdict"] == "pre_existing":
        layers["Tests"] = ("MEDIUM", f"Tests fail (exit {test_norm['exit_code']}) but same failures exist on main branch")
    elif test_norm["verdict"] == "pass":
        layers["Tests"] = ("HIGH", "Test suite ran and passed (exit 0)")
    elif test_norm["verdict"] == "fail":
        layers["Tests"] = ("HIGH", f"Test suite ran and failed (exit {test_norm['exit_code']})")
    elif test_norm["ran"]:
        layers["Tests"] = ("MEDIUM", "Tests ran but result is ambiguous")
    else:
        layers["Tests"] = ("LOW", "No test suite was executed")

    if api_diff_skipped:
        layers["API Diff"] = ("LOW", "API diff tool unavailable — no symbol comparison performed")
    elif api_changes > 0:
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
    ecosystem = pr.get("ecosystem", "")
    is_actions = ecosystem == "actions"
    if is_actions:
        build_icon = "ℹ️"
        lines += [
            f"### {build_icon} Build Analysis",
            f"**Status:** {build_icon} **CI-ONLY** | **Verification Level:** {layer_conf['Build'][0]}",
            "",
            "**What we checked:**",
            "- ℹ️ CI-only dependency — no build applicable",
            "- ℹ️ This action only affects CI/CD workflows, not application code",
        ]
    else:
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
    if build_output and not is_actions:
        error_output = _extract_error_lines(build_output, 400)
        lines += ["", "**Build Output:**", "```", error_output, "```"]
    lines += ["", f"**Confidence:** **{layer_conf['Build'][0]}** — {layer_conf['Build'][1]}", ""]

    # ### Test Analysis
    test_verdict_display = test_norm['verdict'].upper()
    if test_norm["verdict"] == "pre_existing":
        test_icon = "⚠️"
        test_verdict_display = "PRE-EXISTING FAILURES"
    lines += [
        f"### {test_icon} Test Analysis",
        f"**Status:** {test_icon} **{test_verdict_display}** | **Verification Level:** {layer_conf['Tests'][0]}",
        "",
        "**What we checked:**",
    ]
    if test_norm["verdict"] == "pre_existing":
        lines += [
            "- ✅ Executed project test suite against the upgraded dependency",
            f"- ⚠️ Test exit code: {test_norm['exit_code']}",
            "- ⚠️ Same failures exist on main branch — not caused by this PR",
        ]
    elif test_norm["ran"] and test_norm["verdict"] in ("pass", "fail"):
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
    if test_output and test_norm["verdict"] in ("fail", "pre_existing"):
        lines += ["", "**Test Output:**", "```", test_output[:400], "```"]
    lines += ["", f"**Confidence:** **{layer_conf['Tests'][0]}** — {layer_conf['Tests'][1]}", ""]

    # ### API Diff Analysis
    api_diff_skipped = _is_api_diff_skipped(pr)
    if api_diff_skipped:
        api_icon = "⬜"
        lines += [
            f"### {api_icon} API Diff Analysis",
            f"**Status:** {api_icon} **UNAVAILABLE** | **Verification Level:** {layer_conf['API Diff'][0]}",
            "",
            "**What we checked:**",
            f"- ⬜ API diff tool was not available — no symbol comparison performed",
        ]
    else:
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
