"""Typed schema for build-results.json — the single source of truth.

Every producer (build-check.sh heredocs, cross_pr_deps.py, security_posture_scan.py,
verdict_contract.py, etc.) and consumer (generate_ai_comments.py, breakability_analyst.py,
etc.) should validate against these types.

KNOWN ALIASES (legacy field names that must be normalized before validation):
  files_importing  <-  import_files, importFiles, deterministic.files_importing
  from             <-  from_version
  to               <-  to_version
  prs              <-  results (array form), pr_results
  behavior_changed <-  changed_behavior, different
  test.exit        <-  test.exit_code
  build.output_tail <- build.stdout
  test.output_tail  <- test.stdout, test.output
  pr_num           <-  pr (in array-form results)
  merge_risk       <-  deterministic.merge_risk, deterministic.verdict, deterministic.classification

See ALIASES dict below for the full mapping used by normalize_pr().
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ── Canonical field-name aliases ────────────────────────────────────────────
# Maps (parent_key, legacy_name) -> canonical_name.
# normalize_pr() uses this to rewrite legacy keys before validation.
ALIASES: dict[tuple[str, str], str] = {
    ("", "import_files"): "files_importing",
    ("", "importFiles"): "files_importing",
    ("", "from_version"): "from",
    ("", "to_version"): "to",
    ("", "changed_behavior"): "behavior_changed",
    ("", "different"): "behavior_changed",
    ("build", "stdout"): "output_tail",
    ("test", "stdout"): "output_tail",
    ("test", "output"): "output_tail",
    ("test", "exit_code"): "exit",
}

TOP_LEVEL_ALIASES: dict[str, str] = {
    "pr_results": "prs",
}


def normalize_pr(pr: dict) -> dict:
    """Rewrite legacy field names to canonical names in-place. Returns pr."""
    for (parent, old), new in ALIASES.items():
        if parent == "":
            if old in pr and new not in pr:
                pr[new] = pr.pop(old)
            elif old in pr:
                del pr[old]
        else:
            sub = pr.get(parent)
            if isinstance(sub, dict):
                if old in sub and new not in sub:
                    sub[new] = sub.pop(old)
                elif old in sub:
                    del sub[old]

    if "merge_risk" not in pr:
        det = pr.get("deterministic")
        if isinstance(det, dict):
            for alt in ("merge_risk", "verdict", "classification"):
                if alt in det:
                    pr["merge_risk"] = det[alt]
                    break

    if "files_importing" not in pr:
        det = pr.get("deterministic")
        if isinstance(det, dict) and "files_importing" in det:
            pr["files_importing"] = det["files_importing"]

    return pr


def normalize_top_level(data: dict) -> dict:
    """Normalize top-level field names. Converts results array to prs dict."""
    for old, new in TOP_LEVEL_ALIASES.items():
        if old in data and new not in data:
            data[new] = data.pop(old)

    if "prs" not in data and "results" in data:
        results = data.pop("results")
        if isinstance(results, list):
            prs = {}
            for item in results:
                num = str(item.get("pr_num", item.get("pr", "")))
                if num:
                    prs[num] = item
            data["prs"] = prs

    return data


# ── Dataclass schemas ───────────────────────────────────────────────────────

@dataclass
class BuildResult:
    verdict: str = ""
    main_exit: Optional[int] = None
    pr_exit: Optional[int] = None
    output_tail: str = ""
    new_errors: list[str] = field(default_factory=list)
    install_method: str = ""
    install_ok: bool = False
    error_class: str = ""
    oom_override: bool = False
    oom_packages: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    ran: bool = False
    exit: Optional[int] = None
    main_test_exit: Optional[int] = None
    main_npm_test_exit: Optional[int] = None
    output_tail: str = ""
    verdict: Optional[str] = None
    new_failures: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class SmokeResult:
    ran: bool = False
    exit: Optional[int] = None


@dataclass
class GoResolution:
    command: str = ""
    output_tail: str = ""
    modsum_diff: str = ""


@dataclass
class MergeRisk:
    tag: str = ""
    reason: str = ""
    evidenceAxis: str = ""
    buildVerificationAxis: str = ""
    confidenceAxis: str = ""


@dataclass
class NoTestConfidence:
    applies: bool = False
    confidence: str = ""
    basis: dict[str, Any] = field(default_factory=dict)
    residual_risk: str = ""


@dataclass
class VerificationStep:
    step: str = ""
    status: str = ""
    detail: str = ""


@dataclass
class EvidenceSignal:
    signal: str = ""
    label: str = ""
    status: str = ""
    command: str = ""
    stdout: str = ""
    exit_code: Optional[int] = None
    summary: str = ""
    na_reason: str = ""


@dataclass
class DeclaredBreakReachability:
    checked: bool = False
    affected_paths: list[str] = field(default_factory=list)
    prod_reachable: bool = False
    test_only: bool = False
    reachability_kind: str = ""
    behavior_confirmed: bool = False
    evidence: list[dict[str, Any]] = field(default_factory=list)
    surface_kind: str = ""
    surface_symbols: list[str] = field(default_factory=list)
    named_symbols: list[str] = field(default_factory=list)
    surface_evidence: list[dict[str, Any]] = field(default_factory=list)
    surface_by_path: dict[str, dict] = field(default_factory=dict)


@dataclass
class CVEDetail:
    cve_id: str = ""
    severity: str = ""
    cvss_score: str = ""
    advisory_url: str = ""
    summary: str = ""


@dataclass
class BehavioralGrade:
    grade: str = ""
    source: str = ""
    probe_kind: Optional[str] = None
    behavior_changed: Any = None
    same_behavior: Optional[bool] = None
    changed_behavior: Optional[str] = None
    rationale: str = ""
    observed_from: Optional[str] = None
    observed_to: Optional[str] = None
    evidence: str = ""
    confidence: str = ""
    probe_commands: list[str] = field(default_factory=list)
    generated_at: Optional[int] = None
    guidance: Optional[str] = None
    call_site: Optional[str] = None
    router_class: Optional[str] = None
    router_markers: list[str] = field(default_factory=list)
    honest_cap: Optional[str] = None
    model: Optional[str] = None
    cached: Optional[bool] = None
    trigger_condition: Optional[str] = None
    trigger_exercised: Optional[bool] = None
    our_usage_exposed: Optional[bool] = None
    our_usage_mapping: Optional[str] = None
    limitations: Optional[str] = None
    call_site_import_path: Optional[str] = None
    reconciliation_note: Optional[str] = None
    our_relevant_usage: Optional[str] = None
    exposure_assessment: Optional[str] = None


@dataclass
class PolicyDecision:
    verdict: str = ""
    severity: str = ""
    confidence: str = ""
    reason_code: str = ""
    display_reason: str = ""


@dataclass
class PolicyLowering:
    decision: dict[str, Any] = field(default_factory=dict)
    bundle: dict[str, Any] = field(default_factory=dict)
    release_notes: dict[str, Any] = field(default_factory=dict)
    reachability: dict[str, Any] = field(default_factory=dict)
    callsite_impact: dict[str, Any] = field(default_factory=dict)
    reachability_adjudication: dict[str, Any] = field(default_factory=dict)


@dataclass
class VerdictV2:
    verdict: str = ""
    severity: str = ""
    confidence: str = ""
    priority: str = ""
    reason: str = ""
    source: str = ""
    breakability_grade: str = ""
    residual: Optional[dict[str, str]] = None
    evidenceState: Optional[dict[str, str]] = None
    policyDecision: Optional[dict[str, str]] = None


@dataclass
class AIAdjudication:
    applied: str = ""
    source: str = ""
    reason_code: str = ""
    evidence: str = ""
    citation: str = ""
    remediation: Optional[str] = None
    deterministic_flaw: Optional[str] = None
    escalation_question: Optional[str] = None


@dataclass
class NpmAudit:
    critical: int = 0
    high: int = 0


@dataclass
class CascadeImpact:
    service: str = ""
    path: str = ""
    reason: str = ""


@dataclass
class PRResult:
    """Canonical shape of a single PR entry in build-results.json."""
    # Identity
    package: str = ""
    ecosystem: str = ""  # npm | gomod | pip | actions | docker | maven
    bump: str = ""  # major | minor | patch | unknown
    dep_type: str = ""  # production | dev | unknown
    dep_relation: str = ""  # direct | transitive | unknown
    pkg_dir: str = "/"
    ownership_class: str = ""

    # Versions — canonical names are "from" and "to"
    # Python reserves "from" so we use field() with metadata
    from_version: str = field(default="", metadata={"json_key": "from"})
    to_version: str = field(default="", metadata={"json_key": "to"})

    # Build pipeline results
    build: dict[str, Any] = field(default_factory=dict)
    test: dict[str, Any] = field(default_factory=dict)
    smoke: dict[str, Any] = field(default_factory=dict)

    # File/dependency analysis
    files_importing: list[str] = field(default_factory=list)
    additional_imports: list[str] = field(default_factory=list)
    diff_lines: int = 0
    diff_truncated: bool = False
    diff_path: str = ""
    install_ok: bool = False

    # Go-specific
    gosum_new_count: int = 0
    gosum_new_names: str = ""
    gosum_total_pr: int = 0
    gosum_total_main: int = 0
    bumped_modules: dict[str, str] = field(default_factory=dict)
    go_resolution: dict[str, Any] = field(default_factory=dict)

    # Vulnerability scan
    vuln_status: str = ""
    vuln_finding: str = ""
    vuln_new_findings: list[str] = field(default_factory=list)
    vuln_preexisting_count: int = 0
    vuln_output: str = ""

    # CVEs
    cves: list[str] = field(default_factory=list)
    cve_details: list[dict[str, Any]] = field(default_factory=list)
    fixes_cves: list[dict[str, Any]] = field(default_factory=list)

    # Deterministic pipeline output
    deterministic: dict[str, Any] = field(default_factory=dict)
    merge_risk: dict[str, Any] = field(default_factory=dict)

    # Verification
    verification_level: int = -1
    verification_label: str = ""
    verification_steps: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    # Declared break reachability
    declared_break_reachability: dict[str, Any] = field(default_factory=dict)

    # No-test confidence
    no_test_confidence: dict[str, Any] = field(default_factory=dict)

    # Downstream enrichment (written by separate scripts)
    behavioral_grade: Optional[dict[str, Any]] = None
    policy_lowering: Optional[dict[str, Any]] = None
    verdict_v2: Optional[dict[str, Any]] = None
    ai_adjudication: Optional[dict[str, Any]] = None
    ai_behavioral_assessment: Optional[dict[str, Any]] = None

    # NPM-specific
    cascade_impact: list[dict[str, Any]] = field(default_factory=list)
    additional_packages: str = ""
    npm_audit: Optional[dict[str, int]] = None
    nestjs_peer_warning: str = ""

    # Merge status
    mergeable_status: str = ""
    oom_override: bool = False
    oom_packages: list[str] = field(default_factory=list)

    # Skip
    skip_reason: Optional[str] = None

    # PR number (present in array-form results)
    pr_num: Optional[int] = None

    # Security-sensitive flag
    security_sensitive: Optional[bool] = None
    ci_tier: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = {}
        for f in self.__dataclass_fields__:
            val = getattr(self, f)
            meta = self.__dataclass_fields__[f].metadata
            key = meta.get("json_key", f)
            if val is not None:
                d[key] = val
        return d


# ── Top-level structures ────────────────────────────────────────────────────

@dataclass
class CrossPRDep:
    pr_a: int = 0
    pr_b: int = 0
    reason: str = ""
    merge_order: str = ""


@dataclass
class CVEFix:
    pr: int = 0
    package: str = ""
    cve_id: str = ""
    severity: str = ""
    from_version: str = ""
    to_version: str = ""
    first_patched_version: str = ""
    via: str = ""  # primary | transitive
    summary: str = ""


@dataclass
class OrphanAlert:
    cve_id: str = ""
    package: str = ""
    severity: str = ""
    first_patched_version: str = ""
    summary: str = ""


@dataclass
class PRFixingAlerts:
    package: str = ""
    alert_count: int = 0
    severities: list[str] = field(default_factory=list)
    cve_ids: list[str] = field(default_factory=list)
    cvss_scores: list[str] = field(default_factory=list)
    advisory_urls: list[str] = field(default_factory=list)


@dataclass
class SecurityPosture:
    total_open_alerts: int = 0
    severity_counts: dict[str, int] = field(default_factory=dict)
    total_cves_in_prs: int = 0
    prs_fixing_alerts: dict[str, dict] = field(default_factory=dict)
    prs_with_cves: dict[str, list[str]] = field(default_factory=dict)
    alerts_fixable_by_merging: int = 0
    cve_fixes: list[dict[str, Any]] = field(default_factory=list)
    orphan_alerts: list[dict[str, Any]] = field(default_factory=list)
    # Subset-mode fields (from merge-results.sh)
    scope: str = ""
    alert_counts_scope: str = ""
    pr_rows_scope: str = ""
    alerts_unavailable: bool = False
    subset_pr_numbers: list[int] = field(default_factory=list)
    subset_note: str = ""
    orphan_alerts_omitted_for_subset: int = 0
    omitted_due_to_subset: Optional[dict] = None


@dataclass
class GovulncheckBaseline:
    status: str = "unknown"
    findings: list[str] = field(default_factory=list)


@dataclass
class Govulncheck:
    main_baseline: dict[str, Any] = field(default_factory=lambda: {"status": "unknown", "findings": []})
    prs_scanned: int = 0
    prs_with_new_vulns: int = 0
    total_new_findings: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class MainBuildEcosystem:
    exit: int = 0
    output_tail: str = ""
    test_exit: Optional[int] = None
    test_output_tail: Optional[str] = None


@dataclass
class MainBuild:
    npm: Optional[dict[str, Any]] = None
    go: Optional[dict[str, Any]] = None
    pip: Optional[dict[str, Any]] = None


@dataclass
class Metadata:
    repo: str = ""
    timestamp: str = ""
    pr_count: int = 0
    cli_path: str = ""
    mode: str = "advisory"
    subset_requested: Optional[bool] = None
    requested_pr_numbers: list[int] = field(default_factory=list)
    # merge-results.sh additions
    missing_pr_numbers: list[int] = field(default_factory=list)
    dropped_unrequested_pr_numbers: list[int] = field(default_factory=list)
    selected_pr_numbers: list[int] = field(default_factory=list)
    expected_pr_count: Optional[int] = None
    expected_pr_count_source: str = ""
    incomplete: Optional[bool] = None
    missing_pr_count: int = 0
    incomplete_batches: list[str] = field(default_factory=list)


@dataclass
class BuildResults:
    """Top-level schema for build-results.json."""
    metadata: dict[str, Any] = field(default_factory=dict)
    main_build: dict[str, Any] = field(default_factory=dict)
    prs: dict[str, dict[str, Any]] = field(default_factory=dict)
    cross_pr_deps: list[dict[str, Any]] = field(default_factory=list)
    security_posture: dict[str, Any] = field(default_factory=dict)
    govulncheck: dict[str, Any] = field(default_factory=dict)
    workspace_graph: dict[str, Any] = field(default_factory=dict)
    nestjs_skew: list[Any] = field(default_factory=list)
    main_baseline_vuln: Optional[dict[str, Any]] = None
    # Compatibility shim (array form from merge-results.sh)
    results: Optional[list[dict[str, Any]]] = None


# ── Validation ──────────────────────────────────────────────────────────────

class SchemaValidationError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} schema violation(s): {'; '.join(errors[:5])}")


_VALID_ECOSYSTEMS = {"npm", "gomod", "pip", "actions", "docker", "maven", ""}
_VALID_BUMPS = {"major", "minor", "patch", "unknown", ""}
_VALID_DEP_TYPES = {"production", "dev", "unknown", ""}
_VALID_DEP_RELATIONS = {"direct", "transitive", "unknown", ""}
_VALID_BUILD_VERDICTS = {
    "pass", "fail", "pre_existing", "pre_existing_plus_new",
    "skipped", "security_review", "",
}
_VALID_VERDICT_V2 = {"SAFE", "REVIEW", "BLOCKED", "GLANCE", ""}
_VALID_VERIFICATION_LABELS = {
    "L0_unresolved", "L1_dep_resolved", "L2_type_checked", "L2_build_failed",
    "L3_symbols_verified", "L4_tests_pass", "L5_fully_verified",
    "NA_not_applicable", "CI_ONLY", "",
}


def validate_pr(pr_num: str, pr: dict, *, strict: bool = False) -> list[str]:
    """Validate a single PR entry. Returns list of error strings (empty = valid)."""
    errors = []

    if not pr.get("package"):
        errors.append(f"PR #{pr_num}: missing 'package'")

    eco = pr.get("ecosystem", "")
    if eco and eco not in _VALID_ECOSYSTEMS:
        errors.append(f"PR #{pr_num}: invalid ecosystem '{eco}'")

    bump = pr.get("bump", "")
    if bump and bump not in _VALID_BUMPS:
        errors.append(f"PR #{pr_num}: invalid bump '{bump}'")

    dep_type = pr.get("dep_type", "")
    if dep_type and dep_type not in _VALID_DEP_TYPES:
        errors.append(f"PR #{pr_num}: invalid dep_type '{dep_type}'")

    dep_rel = pr.get("dep_relation", "")
    if dep_rel and dep_rel not in _VALID_DEP_RELATIONS:
        errors.append(f"PR #{pr_num}: invalid dep_relation '{dep_rel}'")

    build = pr.get("build")
    if isinstance(build, dict):
        bv = build.get("verdict", "")
        if bv and bv not in _VALID_BUILD_VERDICTS:
            errors.append(f"PR #{pr_num}: invalid build.verdict '{bv}'")

    v2 = pr.get("verdict_v2")
    if isinstance(v2, dict):
        vv = v2.get("verdict", "")
        if vv and vv not in _VALID_VERDICT_V2:
            errors.append(f"PR #{pr_num}: invalid verdict_v2.verdict '{vv}'")

    vl = pr.get("verification_label", "")
    if vl and vl not in _VALID_VERIFICATION_LABELS:
        errors.append(f"PR #{pr_num}: invalid verification_label '{vl}'")

    # Check for legacy field names that should have been normalized
    for legacy in ("import_files", "importFiles", "from_version", "to_version"):
        if legacy in pr:
            errors.append(f"PR #{pr_num}: legacy field '{legacy}' present — run normalize_pr() first")

    if strict:
        if not pr.get("from") and not pr.get("skip_reason"):
            errors.append(f"PR #{pr_num}: missing 'from' version")
        if not pr.get("to") and not pr.get("skip_reason"):
            errors.append(f"PR #{pr_num}: missing 'to' version")

    return errors


def validate(data: dict, *, strict: bool = False) -> list[str]:
    """Validate a full build-results.json dict. Returns list of errors."""
    errors = []

    if "prs" not in data and "results" not in data:
        errors.append("Missing both 'prs' and 'results' — no PR data found")
        return errors

    prs = data.get("prs", {})
    if not isinstance(prs, dict):
        errors.append(f"'prs' should be dict, got {type(prs).__name__}")
        return errors

    for pr_num, pr in prs.items():
        if not isinstance(pr, dict):
            errors.append(f"PR #{pr_num}: expected dict, got {type(pr).__name__}")
            continue
        errors.extend(validate_pr(pr_num, pr, strict=strict))

    cross_deps = data.get("cross_pr_deps", [])
    if not isinstance(cross_deps, list):
        errors.append(f"'cross_pr_deps' should be list, got {type(cross_deps).__name__}")

    return errors


def validate_file(path: str, *, strict: bool = False, normalize: bool = True) -> list[str]:
    """Validate a build-results.json file on disk."""
    with open(path) as f:
        data = json.load(f)

    if normalize:
        normalize_top_level(data)
        for pr in data.get("prs", {}).values():
            if isinstance(pr, dict):
                normalize_pr(pr)

    return validate(data, strict=strict)


# ── CLI entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 -m core.build_results_schema <build-results.json> [--strict]", file=sys.stderr)
        sys.exit(1)

    path = sys.argv[1]
    strict = "--strict" in sys.argv

    errors = validate_file(path, strict=strict)
    if errors:
        print(f"FAIL: {len(errors)} validation error(s):", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    else:
        with open(path) as f:
            data = json.load(f)
        pr_count = len(data.get("prs", {}))
        print(f"OK: {pr_count} PRs validated")
        sys.exit(0)
