#!/usr/bin/env python3
"""Assemble PR data dict from temp files and env vars, write into results JSON.

Extracted from the UNQUOTED PYEOF heredoc in build-check.sh (lines 1600-2658).
Shell variables are now passed via environment variables; all other logic is
identical to the original heredoc.

Usage (called from build-check.sh with env-var prefix):
    RESULTS_FILE=... PR_NUM=... ... python3 "$BRK_SCRIPTS/core/pr_data_assembler.py"
"""

import json
import os
import re
import subprocess
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_here))


def main():
    # ── Load all env vars into local variables ────────────────────
    results_file = os.environ["RESULTS_FILE"]
    pr_num = os.environ["PR_NUM"]
    bc_scratch_dir = os.environ.get("BC_SCRATCH_DIR", "/tmp")

    # String values for comparison
    test_ran_str = os.environ.get("TEST_RAN", "false")
    test_exit_str = os.environ.get("TEST_EXIT", "null")
    build_verdict_initial = os.environ.get("BUILD_VERDICT", "unknown")
    error_class = os.environ.get("ERROR_CLASS", "")
    install_method = os.environ.get("INSTALL_METHOD", "ci")
    smoke_ran_str = os.environ.get("SMOKE_RAN", "false")
    smoke_exit_str = os.environ.get("SMOKE_EXIT", "null")
    diff_truncated_str = os.environ.get("DIFF_TRUNCATED", "false")
    pkg_dir_env = os.environ.get("PKG_DIR", "/")
    install_ok_str = os.environ.get("INSTALL_OK", "false")
    mergeable_status = os.environ.get("MERGEABLE_STATUS", "")
    pr_tsc_exit_str = os.environ.get("PR_TSC_EXIT", "-1")
    pr_install_exit_str = os.environ.get("PR_INSTALL_EXIT", "")
    main_go_test_exit_str = os.environ.get("MAIN_GO_TEST_EXIT_PR", "-1")
    main_npm_test_exit_str = os.environ.get("MAIN_NPM_TEST_EXIT_PR", "-1")

    # Numeric values
    main_exit_eco = int(os.environ.get("MAIN_EXIT_ECO", "-1"))
    build_exit_code = int(os.environ.get("BUILD_EXIT_CODE", "-1"))
    diff_lines = int(os.environ.get("DIFF_LINES", "0"))
    audit_critical = int(os.environ.get("AUDIT_CRITICAL", "0"))
    audit_high = int(os.environ.get("AUDIT_HIGH", "0"))
    main_go_test_exit_pr = int(main_go_test_exit_str) if main_go_test_exit_str not in ("-1", "") else -1
    main_npm_test_exit_pr = int(main_npm_test_exit_str) if main_npm_test_exit_str not in ("-1", "") else -1

    # ── Original heredoc logic (verbatim except shell var replacements) ──

    with open(results_file) as f:
        data = json.load(f)

    # Read deterministic output (CR2-4: use specific exception types, not bare except)
    det_path = f"/tmp/_bc_det_{pr_num}.json"
    try:
        with open(det_path) as f:
            det_raw = f.read().strip()
        deterministic = json.loads(det_raw) if det_raw and det_raw != '{}' else {}
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        deterministic = {}

    # Read cascade_impact (from temp file to avoid shell injection -- Finding-3.2)
    try:
        with open(f"/tmp/_bc_cascade_{pr_num}.txt") as f:
            cascade_str = f.read().strip()
        cascade_impact = json.loads(cascade_str) if cascade_str else []
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        cascade_impact = []


    # Read files_importing
    files_path = f"/tmp/_bc_files_{pr_num}.json"
    try:
        with open(files_path) as f:
            files_importing = json.loads(f.read().strip())
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        files_importing = []

    # Read additional_imports for multi-package PRs (from temp file -- Finding-3.2)
    try:
        with open(f"/tmp/_bc_addl_imports_{pr_num}.json") as f:
            additional_imports = json.loads(f.read().strip())
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        additional_imports = []

    # Read build output
    build_out_path = f"/tmp/_bc_build_out_{pr_num}.txt"
    try:
        with open(build_out_path) as f:
            build_output = f.read()
    except (IOError, OSError):
        build_output = ""

    # Read test output
    test_out_path = f"/tmp/_bc_test_out_{pr_num}.txt"
    try:
        with open(test_out_path) as f:
            test_output = f.read()
    except (IOError, OSError):
        test_output = ""

    # Read new errors (errors on PR branch not present on main)
    new_errors_path = f"/tmp/_bc_new_errors_{pr_num}.txt"
    try:
        with open(new_errors_path) as f:
            new_errors_raw = f.read().strip()
        new_errors = [e for e in new_errors_raw.split('\n') if e.strip()] if new_errors_raw else []
    except (IOError, OSError, ValueError):
        new_errors = []

    # Read go.sum new transitive count and names
    try:
        with open(f"/tmp/_bc_gosum_new_{pr_num}.txt") as f:
            gosum_new_count = int(f.read().strip() or "0")
    except (IOError, OSError, ValueError):
        gosum_new_count = 0
    try:
        with open(f"/tmp/_bc_gosum_names_{pr_num}.txt") as f:
            gosum_new_names = f.read().strip()
    except (IOError, OSError):
        gosum_new_names = ""
    try:
        with open(f"/tmp/_bc_gosum_total_pr_{pr_num}.txt") as f:
            gosum_total_pr = int(f.read().strip() or "0")
    except (IOError, OSError, ValueError):
        gosum_total_pr = 0
    try:
        with open(f"/tmp/_bc_gosum_total_main_{pr_num}.txt") as f:
            gosum_total_main = int(f.read().strip() or "0")
    except (IOError, OSError, ValueError):
        gosum_total_main = 0
    try:
        with open(f"/tmp/_bc_bumped_mods_{pr_num}.json") as f:
            bumped_modules = json.load(f)
            if not isinstance(bumped_modules, dict):
                bumped_modules = {}
    except (IOError, OSError, ValueError):
        bumped_modules = {}

    # Read govulncheck status + first finding (if any)
    try:
        with open(f"/tmp/_bc_vuln_status_{pr_num}.txt") as f:
            vuln_status = f.read().strip() or "unknown"
    except (IOError, OSError):
        vuln_status = "unknown"
    try:
        with open(f"/tmp/_bc_vuln_finding_{pr_num}.txt") as f:
            vuln_finding = f.read().strip()
    except (IOError, OSError):
        vuln_finding = ""
    try:
        with open(f"/tmp/_bc_vuln_new_findings_{pr_num}.txt") as f:
            vuln_new_findings = [l.strip() for l in f.readlines() if l.strip()]
    except (IOError, OSError):
        vuln_new_findings = []
    try:
        with open(f"/tmp/_bc_vuln_preexisting_count_{pr_num}.txt") as f:
            vuln_preexisting_count = int(f.read().strip() or "0")
    except (IOError, OSError, ValueError):
        vuln_preexisting_count = 0
    # V9.8 iter6 (C): load vuln scan output from its own file (separate from BUILD_OUTPUT)
    try:
        with open(f"/tmp/_bc_vuln_output_{pr_num}.txt") as f:
            vuln_output = f.read()
    except (IOError, OSError):
        vuln_output = ""

    # Read PR metadata from temp files to avoid shell injection (Finding-4.4)
    # MUST be defined before INFRA_ERROR_PATTERNS because eco is used there (Finding-5.1)
    def _read_tmp(suffix):
        try:
            with open(f"/tmp/_bc_{suffix}_{pr_num}.txt") as f:
                return f.read().strip()
        except (IOError, OSError):
            return ""

    pkg = _read_tmp("pkg") or "unknown"
    from_ver = _read_tmp("from_ver")
    to_ver = _read_tmp("to_ver")
    dep_type = _read_tmp("dep_type") or "unknown"
    dep_relation = _read_tmp("dep_relation") or "unknown"
    bump = _read_tmp("bump") or "unknown"
    eco = _read_tmp("ecosystem") or "unknown"

    # Parse CVEs
    cves_raw = _read_tmp("cves")
    cves = [c.strip() for c in cves_raw.split(",") if c.strip()] if cves_raw else []

    # V8 FIX: Parse enriched CVE details (severity, CVSS, advisory URL)
    try:
        with open(f"/tmp/_bc_cve_details_{pr_num}.json") as f:
            cve_details = json.loads(f.read().strip() or "[]")
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        cve_details = []

    # Filter out infrastructure artifact errors from new_errors.
    # When install_fallback/local_fallback is used, tsc may report different errors
    # because file: links don't provide type declarations. These are NOT caused by the upgrade.
    # Additionally, when both baseline and PR tsc fail (main_exit=2, pr_exit=2),
    # non-deterministic tsc output can produce "new" errors that are actually pre-existing.
    # We filter known patterns that are infrastructure artifacts, not genuine regressions.
    INFRA_ERROR_PATTERNS = [
        # Private packages resolved via file: links (no .d.ts) -- add org-specific
        # patterns via extra_infra_patterns in breakability-config.yml
        "Cannot find module 'rxjs'",
        "Cannot find module './../../node_modules/",
        # Transitive deps missing when install degrades
        "Cannot find module 'winston'",
        "Cannot find module '../../utils/file-type-detection.service'",
        # Flaky tsc error: appears non-deterministically across runs
        # (confirmed: GitHub Actions-only PRs produce this same error)
        "TS2349: This expression is not callable",
        # Type mismatches from degraded install (jest mock types, etc.)
        "is not assignable to type 'MockInstance<",
        "commands: undefined[]",
        # Missing properties from partial type resolution
        "publishBulkToCommandStream",
        "toThrowError",
    ]

    # Go-specific infra patterns (added separately for clarity)
    GO_INFRA_PATTERNS = [
        # Go build cache corruption (stale object files with hash paths)
        "go-build/HASH",   # After normalize_go_errors, cache paths become go-build/HASH
        # Go module download / proxy errors (not caused by upgrade)
        "GOPROXY",
        "connection refused",
        "i/o timeout",
    ]
    if eco == "gomod":
        INFRA_ERROR_PATTERNS.extend(GO_INFRA_PATTERNS)
    # Append project-specific patterns from .github/breakability-config.yml
    # Read from temp file to avoid shell injection via unquoted heredoc (Finding-3.2)
    try:
        with open(f"/tmp/_bc_extra_infra_{pr_num}.txt") as f:
            extra_raw = f.read()
    except (IOError, OSError):
        extra_raw = ""
    for line in extra_raw.strip().split('\n'):
        line = line.strip()
        if line and line not in INFRA_ERROR_PATTERNS:
            INFRA_ERROR_PATTERNS.append(line)
    if new_errors:
        real_errors = [e for e in new_errors if not any(p in e for p in INFRA_ERROR_PATTERNS)]
        infra_filtered = len(new_errors) - len(real_errors)
        new_errors = real_errors

    # Test values
    test_ran = True if test_ran_str == "true" else False
    test_exit_raw = test_exit_str
    test_exit = int(test_exit_raw) if test_exit_raw not in ("null", "") else None
    no_go_tests = (eco == "gomod" and test_ran and test_exit == 0 and "[no test files]" in (test_output or ""))

    # If all "new" errors were infra artifacts, downgrade verdict to pre_existing
    build_verdict = build_verdict_initial
    if build_verdict == "pre_existing_plus_new" and not new_errors:
        build_verdict = "pre_existing"

    # For Go builds: if error_class is infrastructure-related (not a code problem),
    # the failure is NOT caused by the upgrade -- downgrade verdict.
    # P0 FIX (v9): Only downgrade if the baseline ALSO failed (main_exit != 0).
    # When main_exit == 0 the baseline passes cleanly, so even infra-looking errors
    # on the PR branch are a genuine regression introduced by the upgrade.
    oom_override = False  # tracks whether verdict was overridden due to OOM on unrelated packages
    oom_packages = []     # which packages were OOM-killed (for comment attribution)
    if error_class in ("cache_corruption", "infra_error", "private_module", "resource_exhaustion", "timeout"):
        if build_verdict in ("fail", "pre_existing_plus_new") and main_exit_eco != 0:
            build_verdict = "pre_existing"  # baseline also fails -- treat as infra issue
        elif build_verdict in ("fail", "pre_existing_plus_new") and main_exit_eco == 0:
            # V9.3 FIX: OOM misclassification (P1 from all reviewers).
            # When error_class is resource_exhaustion and baseline passes, check if ALL
            # build errors are "signal: killed" on packages UNRELATED to the PR's upgraded
            # dependency. If the PR's own targeted dirs built fine (or have 0 imports),
            # the OOM is infrastructure, not a code regression.
            if error_class == "resource_exhaustion" and eco == "gomod":
                # Extract which packages were killed from build output
                killed_pkgs = set()
                for line in build_output.splitlines():
                    if 'signal: killed' in line.lower() or 'signal: kill' in line.lower():
                        # Go build output format: "github.com/org/repo/pkg/subpkg: ...signal: killed"
                        m = re.match(r'^(\S+?):\s', line)
                        if m:
                            killed_pkgs.add(m.group(1))
                # Get the PR's targeted build dirs from files_importing
                targeted_dirs = set()
                for fi in files_importing:
                    fpath = fi.split(':')[0] if ':' in fi else fi
                    d = os.path.dirname(fpath)
                    if d:
                        targeted_dirs.add(d)
                # Check: are ALL errors signal:killed on unrelated packages?
                # Conditions for override:
                # 1. All build errors are signal:killed (no real type errors)
                # 2. None of the killed packages overlap with PR's targeted dirs
                # 3. No new_errors found (or all were infra-filtered)
                has_real_type_errors = False
                for line in build_output.splitlines():
                    line_l = line.lower().strip()
                    if not line_l:
                        continue
                    # Skip info/targeted build output lines
                    if line_l.startswith('targeted build') or line_l.startswith('full build') or line_l.startswith('dirs:') or line_l.startswith('---'):
                        continue
                    # If line contains a Go compile error (.go:NN:NN:) it's a real error
                    if re.search(r'\.go:\d+:\d+:', line):
                        has_real_type_errors = True
                        break
                # Determine if killed packages overlap with targeted dirs
                killed_overlaps_target = False
                for kp in killed_pkgs:
                    for td in targeted_dirs:
                        if td in kp or kp.endswith(td):
                            killed_overlaps_target = True
                            break
                if killed_pkgs and not has_real_type_errors and not killed_overlaps_target and not new_errors:
                    build_verdict = "pass"
                    oom_override = True
                    oom_packages = sorted(killed_pkgs)
            # else: baseline passes but errors are real code regressions -- keep verdict as-is

    pr_data = {
        "package": pkg,
        "from": from_ver,
        "to": to_ver,
        "ecosystem": eco,
        "bump": bump,
        "dep_type": dep_type,
        "dep_relation": dep_relation,
        "cves": cves,
        "cve_details": cve_details,
        "deterministic": deterministic,
        "merge_risk": deterministic.get("merge_risk", {}) if deterministic else {},
        "build": {
            "main_exit": main_exit_eco,
            "pr_exit": build_exit_code,
            "verdict": build_verdict,
            "output_tail": build_output,
            "new_errors": new_errors,
            "install_method": install_method,
            "error_class": error_class,
            "oom_override": oom_override,
            "oom_packages": oom_packages
        },
        "test": {
            "ran": test_ran,
            "exit": test_exit,
            "main_test_exit": main_go_test_exit_pr,
            "main_npm_test_exit": main_npm_test_exit_pr,
            "output_tail": test_output
        },
        "smoke": {
            "ran": True if smoke_ran_str == "true" else False,
            "exit": int(smoke_exit_str) if smoke_exit_str not in ("null", "") else None
        },
        "files_importing": files_importing,
        "additional_imports": additional_imports,
        "diff_lines": diff_lines,
        "diff_truncated": True if diff_truncated_str == "true" else False,
        "diff_path": f"/tmp/pr-{pr_num}.diff",
        "pkg_dir": pkg_dir_env,
        "cascade_impact": cascade_impact,
        "gosum_new_count": gosum_new_count,
        "gosum_new_names": gosum_new_names,
        "gosum_total_pr": gosum_total_pr,
        "gosum_total_main": gosum_total_main,
        "bumped_modules": bumped_modules,
        "vuln_status": vuln_status,
        "vuln_finding": vuln_finding,
        "vuln_new_findings": vuln_new_findings,
        "vuln_preexisting_count": vuln_preexisting_count,
        "vuln_output": vuln_output,
        "go_resolution": {
            "command": open(f"{bc_scratch_dir}/_bc_go_resolution_command_{pr_num}.txt").read().strip() if os.path.exists(f"{bc_scratch_dir}/_bc_go_resolution_command_{pr_num}.txt") else "",
            "output_tail": open(f"{bc_scratch_dir}/_bc_go_resolution_output_{pr_num}.txt").read()[-20000:] if os.path.exists(f"{bc_scratch_dir}/_bc_go_resolution_output_{pr_num}.txt") else "",
            "modsum_diff": open(f"{bc_scratch_dir}/_bc_go_modsum_diff_{pr_num}.txt").read()[-30000:] if os.path.exists(f"{bc_scratch_dir}/_bc_go_modsum_diff_{pr_num}.txt") else "",
        },
        "nestjs_peer_warning": open(f"/tmp/_bc_peer_warn_{pr_num}.txt").read().strip() if os.path.exists(f"/tmp/_bc_peer_warn_{pr_num}.txt") else "",
        "install_ok": True if install_ok_str == "true" else False,
        "additional_packages": open(f"/tmp/_bc_addl_pkgs_{pr_num}.txt").read().strip() if os.path.exists(f"/tmp/_bc_addl_pkgs_{pr_num}.txt") else "",
        "mergeable_status": mergeable_status,
        "npm_audit": {
            "critical": audit_critical,
            "high": audit_high
        },
        "no_test_confidence": {}
    }

    if eco == "gomod" and no_go_tests:
        api_changes = len(deterministic.get("apiChanges", [])) if deterministic else 0
        symbol_results = deterministic.get("verification", {}).get("symbolResults", {}) if deterministic else {}
        used_symbols = 0
        if isinstance(symbol_results, dict):
            for val in symbol_results.values():
                if isinstance(val, dict) and val.get("used"):
                    used_symbols += 1
                elif isinstance(val, (list, tuple, set)):
                    used_symbols += len(val)
        usage = len(files_importing) + used_symbols
        score = 0
        if api_changes == 0:
            score += 2
        elif api_changes <= 2:
            score += 1
        if usage == 0:
            score += 2
        elif usage <= 3:
            score += 1
        if bump in ("patch", "minor"):
            score += 1
        if dep_type in ("dev", "development"):
            score += 1
        confidence = "high" if score >= 5 else ("medium" if score >= 3 else "low")
        residual = "No Go test files were present, so runtime behavior is not exercised by CI. "
        if api_changes:
            residual += f"API diff reported {api_changes} change(s). "
        else:
            residual += "API diff reported no removed/changed exported APIs. "
        if usage:
            residual += f"Reachability saw {usage} usage signal(s); review touched call sites if behavior changed."
        else:
            residual += "No direct usage was found in scanned files; remaining risk is transitive/runtime behavior."
        pr_data["no_test_confidence"] = {
            "applies": True,
            "confidence": confidence,
            "basis": {"api_changes": api_changes, "usage_signals": usage, "semver_bump": bump, "dep_type": dep_type},
            "residual_risk": residual
        }

    # -- Ownership classification -----------------------------------------
    # Tells reviewers WHO fixes this and whether THEIR code is affected.
    # Re-use eco, pkg, dep_type, dep_relation from _read_tmp() above (Finding-5.2).
    # Do NOT re-assign from shell expansion -- that re-introduces injection risk.
    dep_rel = dep_relation  # alias for shorter references below
    pkg_dir = _read_tmp("pkg_dir") or "/"
    n_imports = len(files_importing)

    KNOWN_BUILD_TOOLS = {
        "typescript", "eslint", "prettier", "webpack", "vite", "rollup",
        "babel", "jest", "vitest", "mocha", "nyc", "c8", "esbuild", "swc",
        "ts-jest", "ts-node", "tsup", "turbo", "lerna", "nx",
        "@typescript-eslint/parser", "@typescript-eslint/eslint-plugin",
        "@nestjs/schematics", "@nestjs/cli", "husky", "lint-staged",
        "commitlint", "@commitlint/cli", "@commitlint/config-conventional",
        "nodemon", "ts-loader", "webpack-cli", "rimraf", "concurrently",
    }
    # Platform SDKs: you build a plugin ON these (compile against their API)
    PLATFORM_SDK_IMAGES = {"keycloak", "liquibase", "tinygo", "maven", "gradle"}
    # Service images: you just run these as infrastructure (base_image)
    SERVICE_IMAGES = {"postgres", "mysql", "redis", "mongo", "elasticsearch",
                      "rabbitmq", "kafka", "zookeeper", "consul", "vault", "nginx"}

    if eco == "actions":
        ownership = "ci_tool"
    elif eco == "docker":
        # Platform SDK (you build a plugin on it) vs base image (OS/runtime)
        base_img = (build_output or "").lower()
        if any(p in base_img for p in PLATFORM_SDK_IMAGES):
            ownership = "platform_sdk"
        else:
            ownership = "base_image"
    elif eco == "maven":
        ownership = "platform_sdk"
    elif dep_type == "dev" and any(t in pkg.lower() for t in ["eslint", "prettier", "webpack", "vite", "rollup", "babel", "jest", "vitest", "typescript", "tsc", "swc", "esbuild", "turbo", "nx"]):
        ownership = "build_tool"
    elif pkg.lower() in KNOWN_BUILD_TOOLS:
        ownership = "build_tool"
    elif pkg.lower().startswith("@types/"):
        # @types/* with actual imports = direct_dep (your code relies on these types)
        # @types/* with 0 imports and dev dep = build_tool (ambient declarations)
        if n_imports > 0 or dep_type == "production":
            ownership = "direct_dep"
        else:
            ownership = "build_tool"
    elif dep_rel == "transitive" and n_imports == 0:
        ownership = "transitive_dep"
    else:
        ownership = "direct_dep"

    pr_data["ownership_class"] = ownership

    # -- Verification Level (L0-L5) ---------------------------------------
    # Graduated confidence based on what ACTUALLY ran, not what we hope.
    # L0: Unresolved -- couldn't install
    # L1: Dep-resolved -- npm ci / pip install / go mod tidy succeeded
    # L2: Type-checked -- tsc --noEmit / go build passed (no new type errors)
    # L3: Symbols-verified -- ESM/CJS probe confirmed symbol existence (from deterministic.verification)
    # L4: Tests-pass -- npm test / go test / pytest passed on PR branch
    # L5: Fully-verified -- tests pass AND no new errors AND API compatible AND smoke pass

    # Docker and actions now have real build verdicts -- let them flow through normal confidence logic
    install_ok = pr_data.get("install_ok", False)
    # IMPORTANT: reuse the Python build_verdict from above, NOT the shell BUILD_VERDICT.
    # The earlier Python code may have downgraded build_verdict (e.g., fail -> pre_existing for
    # infra errors). Re-reading from shell would discard that fix. (CR2-1)
    # build_verdict is already set correctly above -- do NOT overwrite it here.
    test_ran_val = test_ran
    test_exit_val = test_exit
    smoke_ran_val = pr_data["smoke"]["ran"]
    smoke_exit_val = pr_data["smoke"]["exit"]
    det_verified = deterministic.get("verification", {}).get("verified", False) if deterministic else False
    det_compatible = deterministic.get("verification", {}).get("compatible", None) if deterministic else None

    steps = []
    level = 0

    if not install_ok:
        level = 0
        steps.append({"step": "dependency_resolution", "status": "fail", "detail": error_class or "install failed"})
    else:
        level = 1
        steps.append({"step": "dependency_resolution", "status": "pass"})

        # L2: Type-checking (tsc / go build)
        tsc_ran = pr_tsc_exit_str not in ("-1", "")
        tsc_passed = pr_tsc_exit_str == "0" if tsc_ran else False
        pr_exit_val = pr_data.get("build", {}).get("pr_exit", -1)
        if eco in ("gomod", "pip"):
            # go build / pip import check IS the type-check equivalent
            if build_verdict in ("pass", "security_review"):
                level = 2
                steps.append({"step": "type_check", "status": "pass"})
            elif build_verdict == "pre_existing" and pr_exit_val == 0:
                # v9.2 FIX: PR build actually passes (exit=0) but verdict was set to
                # pre_existing (e.g., baseline timed out). The PR branch builds clean,
                # so this IS L2 -- type-check passed on the PR branch.
                level = 2
                steps.append({"step": "type_check", "status": "pass", "detail": "PR build passes (baseline had errors)"})
            elif build_verdict == "pre_existing":
                # Build fails on both branches with same errors -- NOT a real pass (CR3-8).
                # Stay at L1 (like npm does for tsc pre_existing), mark as inconclusive.
                level = 1  # DO NOT promote to L2
                # v9: Include first error line so the comment says WHAT failed
                _pre_sample = new_errors[0] if new_errors else (build_output.strip().splitlines()[-1] if build_output.strip() else "unknown")
                steps.append({"step": "type_check", "status": "pre_existing", "detail": f"same errors on main — {_pre_sample[:120]}"})
            elif build_verdict in ("fail", "pre_existing_plus_new"):
                # V8 FIX (L2/1.4/1.5): Build WAS run and FAILED with new errors.
                # This IS L2 (type-check was attempted), not L1 (dep-resolved only).
                # The BUILD_FAILS comment should show L2, not L1.
                level = 2
                # v9: Include first new error so the comment says WHAT broke
                _fail_sample = new_errors[0] if new_errors else "build exit non-zero"
                steps.append({"step": "type_check", "status": "fail", "detail": f"{len(new_errors)} new error(s): {_fail_sample[:120]}"})
            else:
                steps.append({"step": "type_check", "status": "fail"})
        elif tsc_ran:
            if tsc_passed:
                # tsc actually passed -- genuine L2
                level = 2
                steps.append({"step": "type_check", "status": "pass"})
            elif build_verdict == "pre_existing" and pr_tsc_exit_str == "0":
                # v9.2 FIX: tsc actually passed on PR branch (exit=0) but verdict was
                # set to pre_existing (e.g., baseline timed out or had other issues).
                # The PR's type-check passed, so this IS L2.
                level = 2
                steps.append({"step": "type_check", "status": "pass", "detail": "tsc passes on PR (baseline had errors)"})
            elif build_verdict == "pre_existing":
                # tsc failed on both branches with same errors -- NOT a real pass
                # Stay at L1, mark type_check as "pre_existing" (inconclusive)
                level = 1  # DO NOT promote to L2
                # v9: Include first error so the comment says WHAT failed
                _tsc_pre_sample = new_errors[0] if new_errors else (build_output.strip().splitlines()[-1] if build_output.strip() else "unknown")
                steps.append({"step": "type_check", "status": "pre_existing", "detail": f"same tsc errors on main — {_tsc_pre_sample[:120]}"})
            elif build_verdict in ("fail", "pre_existing_plus_new"):
                # V8 FIX: tsc WAS run and FAILED. This is L2 (attempted), not L1.
                level = 2
                # v9: Include first new error so the comment says WHAT broke
                _tsc_fail_sample = new_errors[0] if new_errors else "tsc exit non-zero"
                steps.append({"step": "type_check", "status": "fail", "detail": f"{len(new_errors)} new error(s): {_tsc_fail_sample[:120]}"})
            else:
                steps.append({"step": "type_check", "status": "fail"})
        else:
            steps.append({"step": "type_check", "status": "skip", "detail": "no tsconfig.json"})
            if build_verdict in ("pass", "security_review"):
                level = 2  # install passed, no tsc to run = still dep-resolved+

        # L3: Symbol verification (from CLI deterministic layer)
        if det_verified:
            level = max(level, 3)
            steps.append({"step": "symbol_verification", "status": "pass", "detail": f"compatible={det_compatible}"})
        elif deterministic:
            steps.append({"step": "symbol_verification", "status": "skip", "detail": "not run or no .d.ts"})
        else:
            steps.append({"step": "symbol_verification", "status": "skip"})

        # L4: Tests
        # For Go: content-level pre-existing comparison (Finding-4.3).
        # Compare actual FAIL lines, not just exit codes, to detect mixed failures
        # where different tests fail on main vs PR.
        main_go_test_exit_raw = main_go_test_exit_str
        main_go_test_exit_val = int(main_go_test_exit_raw) if main_go_test_exit_raw not in ("-1", "") else -1
        # npm test pre-existing comparison (Finding-4.5)
        main_npm_test_exit_raw = main_npm_test_exit_str
        main_npm_test_exit_val = int(main_npm_test_exit_raw) if main_npm_test_exit_raw not in ("-1", "") else -1
        if test_ran_val and test_exit_val is not None:
            if eco == "gomod" and no_go_tests:
                steps.append({"step": "test_suite", "status": "skip", "detail": "go test reported [no test files]; see no_test_confidence"})
            elif test_exit_val == 0:
                level = max(level, 4)
                steps.append({"step": "test_suite", "status": "pass"})
            else:
                is_preexisting_test = False
                preexisting_detail = ""
                new_test_fails = set()
                new_npm_test_fails = set()
                if eco == "gomod" and main_go_test_exit_val > 0 and test_exit_val > 0:
                    # Content-level comparison: extract FAIL lines from both (Finding-4.3)
                    main_test_file = f"/tmp/_bc_main_go_test_out_{pr_num}.txt"
                    try:
                        with open(main_test_file) as f:
                            main_test_lines = f.read()
                    except (IOError, OSError):
                        main_test_lines = ""
                    # Extract "--- FAIL:" lines from Go test output
                    main_fails = set(re.findall(r'--- FAIL: (\S+)', main_test_lines))
                    pr_fails = set(re.findall(r'--- FAIL: (\S+)', test_output))
                    new_test_fails = pr_fails - main_fails
                    if new_test_fails:
                        # PR has NEW test failures not present on main
                        preexisting_detail = f"exit={test_exit_val} — {len(new_test_fails)} new test failure(s): {', '.join(sorted(new_test_fails)[:5])}"
                    else:
                        is_preexisting_test = True
                        preexisting_detail = f"exit={test_exit_val} — same failures on main (exit={main_go_test_exit_val})"
                elif eco == "npm" and main_npm_test_exit_val > 0 and test_exit_val > 0:
                    # Content-level comparison for npm tests (Finding-5.4, upgrades Finding-4.5)
                    # Read baseline npm test output for comparison
                    main_npm_test_file = f"/tmp/_bc_main_npm_test_out_{pr_num}.txt"
                    try:
                        with open(main_npm_test_file) as f:
                            main_npm_test_lines = f.read()
                    except (IOError, OSError):
                        main_npm_test_lines = ""
                    # Jest format: "FAIL src/tests/foo.test.ts" or "FAIL ./src/tests/foo.test.ts"
                    main_npm_fails = set(re.findall(r'FAIL\s+(\S+)', main_npm_test_lines))
                    pr_npm_fails = set(re.findall(r'FAIL\s+(\S+)', test_output))
                    new_npm_test_fails = pr_npm_fails - main_npm_fails
                    if new_npm_test_fails:
                        preexisting_detail = f"exit={test_exit_val} — {len(new_npm_test_fails)} new test failure(s): {', '.join(sorted(new_npm_test_fails)[:5])}"
                    else:
                        is_preexisting_test = True
                        preexisting_detail = f"exit={test_exit_val} — same failures on main (exit={main_npm_test_exit_val})"
                if is_preexisting_test:
                    steps.append({"step": "test_suite", "status": "pre_existing",
                                  "detail": preexisting_detail})
                    pr_data["test"]["verdict"] = "pre_existing"
                    pr_data["test"]["new_failures"] = []
                else:
                    detail = preexisting_detail if preexisting_detail else f"exit={test_exit_val}"
                    steps.append({"step": "test_suite", "status": "fail", "detail": detail})
                    pr_data["test"]["verdict"] = "fail"
                    new_fails_list = sorted(new_test_fails) if eco == "gomod" and new_test_fails else (sorted(new_npm_test_fails) if eco == "npm" and 'new_npm_test_fails' in dir() and new_npm_test_fails else [])
                    pr_data["test"]["new_failures"] = new_fails_list
        else:
            steps.append({"step": "test_suite", "status": "skip", "detail": "not triggered"})

        # L5: Fully verified (tests pass + no new errors + symbols ok + smoke ok)
        if (test_ran_val and test_exit_val == 0 and
            build_verdict in ("pass", "security_review") and
            (det_compatible is True or det_compatible is None)):
            if smoke_ran_val and smoke_exit_val == 0:
                level = 5
                steps.append({"step": "smoke_probe", "status": "pass"})
            elif smoke_ran_val:
                steps.append({"step": "smoke_probe", "status": "fail", "detail": f"exit={smoke_exit_val}"})
            elif not smoke_ran_val:
                # Tests pass but no smoke -- still L4
                steps.append({"step": "smoke_probe", "status": "skip", "detail": "no dist/main.js after build"})
        elif smoke_ran_val:
            if smoke_exit_val == 0:
                steps.append({"step": "smoke_probe", "status": "pass"})
            else:
                steps.append({"step": "smoke_probe", "status": "fail", "detail": f"exit={smoke_exit_val}"})

    LEVEL_LABELS = {
        -1: "NA_not_applicable",
        0: "L0_unresolved",
        1: "L1_dep_resolved",
        2: "L2_type_checked",
        3: "L3_symbols_verified",
        4: "L4_tests_pass",
        5: "L5_fully_verified"
    }

    # V8 FIX (H3): Actions PRs should NOT show L2_type_checked -- no type-checking
    # was performed. They get a distinct label so the merge plan doesn't lie.
    if eco == "actions":
        pr_data["verification_level"] = -1
        pr_data["verification_label"] = "CI_ONLY"
    else:
        pr_data["verification_level"] = level
        pr_data["verification_label"] = LEVEL_LABELS.get(level, f"L{level}")
        # A build that FAILED still reaches level 2 (type-check was attempted) but must
        # NOT be labelled "L2_type_checked" -- that reads as a clean pass. Use a distinct
        # "L2_build_failed" label so the merge plan / signal table never imply the build
        # passed (PR#38 false-positive).
        if level == 2 and build_verdict in ("fail", "pre_existing_plus_new"):
            pr_data["verification_label"] = "L2_build_failed"
    if isinstance(pr_data.get("merge_risk"), dict):
        pr_data["merge_risk"].setdefault("evidenceAxis", "limited evidence")
        pr_data["merge_risk"]["buildVerificationAxis"] = f"L{level}" if level >= 0 else pr_data["verification_label"]
        pr_data["merge_risk"]["confidenceAxis"] = pr_data["merge_risk"]["buildVerificationAxis"]
        if isinstance(pr_data.get("deterministic"), dict) and isinstance(pr_data["deterministic"].get("merge_risk"), dict):
            pr_data["deterministic"]["merge_risk"] = pr_data["merge_risk"]

    # -- Declared-break reachability resolution ----------------------------
    # A declared-breaking changelog verdict (High) is reachability-BLIND on its own: the break
    # may live in a sibling/sub-module the repo does not even import. Extract the affected import
    # paths from the breaking bullets, grep the working tree, and either PROVE reachability (name
    # the importing file) or DOWNGRADE when nothing imports the affected package.
    _dbr_re = re
    _dbr_sub = subprocess

    # -- Behavioral-exposure classifier (deterministic, Go-first) ----------
    # Import-level reachability proves only that the affected PACKAGE is imported. For a
    # behavioral break that is the WHOLE residual: it tells the developer nothing about
    # whether their code touches the changed surface. This classifier refines import into
    # SURFACE exposure: does production code reference a changelog-NAMED changed symbol
    # (strongest), some exported symbol of the package (subsystem surface, the typical
    # shape of an internal-trigger behavioral change), or only import it (lowest)? It
    # NEVER asserts safety (internal behavior can change behind a stable API). Go-only
    # for now; other ecosystems return 'unknown' so the renderer keeps import-level wording.
    # NOTE: this code originally lived inside an UNQUOTED heredoc, so it used chr(96)
    # for backtick and avoided end-of-string anchors. Those workarounds are preserved
    # for behavioral compatibility.
    _BT = chr(96)
    def _extract_named_symbols(text):
        named = set()
        for q, s in _dbr_re.findall(r"\b([a-z][A-Za-z0-9_]*)\.([A-Z][A-Za-z0-9_]{2,})", text or ""):
            named.add(s)
        for chunk in _dbr_re.findall(_BT + r"([^" + _BT + r"]+)" + _BT, text or ""):
            for s in _dbr_re.findall(r"\b([A-Z][A-Za-z0-9_]{2,})\b", chunk):
                named.add(s)
        return named

    def _go_local_name(pkg, file_text):
        m = _dbr_re.search(r'^\s*([A-Za-z_]\w*)\s+"' + _dbr_re.escape(pkg) + r'"', file_text or "", _dbr_re.M)
        if m:
            return m.group(1)
        segs = [s for s in pkg.split("/") if s]
        if segs and len(segs) >= 2 and segs[-1][:1] == "v" and segs[-1][1:].isdigit():
            return segs[-2]
        return segs[-1] if segs else pkg

    def _classify_behavioral_exposure(repo_root, paths, evidence, text, eco):
        out = {"surface_kind": "unknown", "surface_symbols": [], "named_symbols": [],
               "surface_evidence": [], "surface_by_path": {}}
        if eco != "gomod":
            return out
        named = _extract_named_symbols(text)
        out["named_symbols"] = sorted(named)[:12]
        by_path = {}
        for e in evidence:
            if e.get("is_test"):
                continue
            by_path.setdefault(e["path"], []).append(e["file"])
        rank = {"named": 3, "package": 2, "import_only": 1, "unknown": 0}
        best = "unknown"; seen_syms = []; surf_ev = []
        for p, files in by_path.items():
            refs = set(); ref_locs = []; local = None
            for rel in dict.fromkeys(files):
                try:
                    with open(os.path.join(repo_root, rel), "r", errors="replace") as fh:
                        src = fh.read()
                except (IOError, OSError):
                    continue
                ln = _go_local_name(p, src); local = local or ln
                for m in _dbr_re.finditer(_dbr_re.escape(ln) + r"\.([A-Z][A-Za-z0-9_]*)", src):
                    sym = m.group(1); refs.add(sym)
                    ref_locs.append((sym, rel, src.count(chr(10), 0, m.start()) + 1))
            if not refs:
                kind = "import_only"
            elif refs & named:
                kind = "named"
            else:
                kind = "package"
            out["surface_by_path"][p] = {"kind": kind, "local": local, "symbols": sorted(refs)[:12]}
            if rank[kind] > rank[best]:
                best = kind
            for sym, rel, line_no in ref_locs:
                is_named = sym in named
                if kind == "named" and not is_named:
                    continue
                surf_ev.append({"path": p, "symbol": sym, "file": rel, "line": str(line_no), "named": is_named})
            seen_syms.extend(sorted(refs))
        seen = set(); ded = []
        for ev in surf_ev:
            k = (ev["path"], ev["symbol"])
            if k in seen:
                continue
            seen.add(k); ded.append(ev)
        ded.sort(key=lambda e: (e["path"] not in (text or ""), not e["named"]))
        out["surface_kind"] = best
        out["surface_symbols"] = sorted(set(seen_syms))[:20]
        out["surface_evidence"] = ded[:8]
        return out

    def _resolve_declared_break_reachability(pr_data, deterministic, eco):
        mr = pr_data.get("merge_risk") or {}
        evidence_axis = (mr.get("evidenceAxis") or "").lower()
        sig = (deterministic or {}).get("changelogSignal") or {}
        neg = _dbr_re.compile(r"\b(no|not|without|non[-\s]?breaking|does not|did not)\b.{0,80}\b(api change|breaking|incompatible|removed|behavior change)s?\b|\b(api change|breaking change)s?\b.{0,80}\b(no|not|without|none)\b", _dbr_re.I)
        bullets = [b for b in (sig.get("bullets") or []) if isinstance(b, str) and not neg.search(b)]
        if str(sig.get("status") or "").lower() == "breaking" and not bullets:
            mr["tag"] = "Low"
            mr["reason"] = "changelog only contained negated no-change language; no non-negated breaking-change evidence found"
            mr["evidenceAxis"] = "changelog negation filtered"
            return
        # Only the changelog-DECLARED-break High path (merge-risk evidenceAxis
        # "declared breaking change (changelog), behavior unverified") may be downgraded here. A High
        # driven by an independently CONFIRMED signal -- "break-reachable API change", "runtime support
        # drop", "failed deterministic signal" -- must NOT enter this resolver (it would wrongly become
        # Medium). So gate strictly on the declared-breaking axis, NOT the broad changelog status.
        is_declared = mr.get("tag") == "High" and "declared breaking change" in evidence_axis
        if not is_declared:
            return
        # STRONG markers only: a genuine break, not a deprecation/additive note. This keeps us from
        # extracting incidental package names (e.g. an EMPTY-type deprecation) as the affected path.
        strong_re = _dbr_re.compile(r"breaking[\s-]?change|no longer|cardinalit|migration[\s-]?required|removed\s|signature|incompatible|default[s]?\s+(?:changed|now|of|to)", _dbr_re.I)
        breaking_bullets = [b for b in bullets if strong_re.search(b or "")]
        text = " \n ".join(breaking_bullets) if breaking_bullets else ((deterministic or {}).get("changelogText") or "")
        # Extract module/import-style paths (domain + at least one path segment).
        raw_paths = set(_dbr_re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.[A-Za-z]{2,}(?:/[A-Za-z0-9_.-]+)+", text))
        # Trailing sentence punctuation can attach to a path captured from prose (e.g.
        # "...exporters/prometheus. Previously" -> "...prometheus."), which then fails the
        # import grep. Strip trailing non-path punctuation so reachability is not falsely lost.
        raw_paths = {p.rstrip(".,;:)]'\"") for p in raw_paths}
        # npm scoped packages and bare python modules are less reliably named in prose; focus on
        # path-like identifiers, which covers Go module paths and npm scoped/url-style packages.
        reason_text = (mr.get("reason") or "")
        # Sort so paths named in the driving verdict reason are tried first.
        paths = sorted((p for p in raw_paths if "/" in p), key=lambda p: (p not in reason_text, p))[:8]
        repo_root = os.environ.get("REPO_ROOT") or "."
        ext_by_eco = {"gomod": ["*.go"], "npm": ["*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs"], "pip": ["*.py"]}
        includes = ext_by_eco.get(eco, ["*.go", "*.ts", "*.js", "*.py"])
        evidence = []
        prod_reached = False
        test_only = False
        for p in paths:
            cmd = ["grep", "-rnE", "--binary-files=without-match"]
            for inc in includes:
                cmd.append("--include=" + inc)
            cmd += ["--exclude-dir=vendor", "--exclude-dir=node_modules", "--exclude-dir=.git",
                    "(\"|')" + _dbr_re.escape(p) + "(\"|')", repo_root]
            try:
                out = _dbr_sub.run(cmd, capture_output=True, text=True, timeout=45)
            except Exception:
                continue
            for line in (out.stdout or "").splitlines():
                parts = line.split(":", 2)
                if len(parts) < 2:
                    continue
                fpath = parts[0]
                rel = os.path.relpath(fpath, repo_root)
                is_test = bool(_dbr_re.search(r"(_test\.[a-z]+\Z|\.test\.[a-z]+\Z|/tests?/|/__tests__/|\.spec\.[a-z]+\Z)", rel))
                # Reachability decision must see ALL matches; only the DISPLAYED evidence list is capped,
                # so a production import that appears after the 12th match still flips prod_reached.
                if not is_test:
                    prod_reached = True
                if len(evidence) < 12:
                    evidence.append({"path": p, "file": rel, "line": parts[1].strip(), "is_test": is_test})
        if evidence and not prod_reached:
            test_only = True
        # NOTE on confidence: this resolver only runs for a changelog-DECLARED breaking change that the
        # deterministic API-diff did NOT flag (a real removed/changed symbol would have been caught
        # upstream as a reachable hard break -- a different, higher-confidence path). So everything here
        # is a BEHAVIORAL declaration (changed defaults, error/ordering semantics) that build, tests, and
        # API-diff cannot see. We can prove the package is IMPORTED, but never that our code triggers the
        # changed behavior. Therefore we never claim a confirmed break: import-reachable behavioral
        # declarations are a manual-REVIEW signal (Medium), not High.
        if prod_reached:
            reachability_kind = "import"
        elif test_only:
            reachability_kind = "test_only"
        elif paths:
            reachability_kind = "not_imported"
        else:
            reachability_kind = "unresolved"
        result = {
            "checked": bool(paths),
            "affected_paths": paths,
            "prod_reachable": prod_reached,
            "test_only": test_only,
            "reachability_kind": reachability_kind,
            "behavior_confirmed": False,
            "evidence": evidence[:12],
        }
        # Refine import-level reachability into SURFACE-level exposure tiers (deterministic).
        try:
            result.update(_classify_behavioral_exposure(repo_root, paths, evidence, text, eco))
        except Exception as _exp_e:
            print("  behavioral-exposure classification skipped:", str(_exp_e)[:120])
        pr_data["declared_break_reachability"] = result
        # Adjust the verdict using the resolved reachability.
        if not paths:
            return
        if prod_reached:
            sk = result.get("surface_kind", "unknown")
            surf_ev = result.get("surface_evidence", [])
            proof = next((e for e in evidence if (not e["is_test"]) and e["path"] in (mr.get("reason") or "")), None)
            if not proof:
                proof = next((e for e in evidence if not e["is_test"]), None)
            loc = (" (" + proof["path"] + ")") if proof else ""
            mr["tag"] = "Medium"
            if sk == "named":
                sev = next((e for e in surf_ev if e.get("named")), None) or (surf_ev[0] if surf_ev else None)
                symloc = (" — your code calls %s at %s:%s" % (sev["symbol"], sev["file"], sev["line"])) if sev else ""
                mr["reason"] = ("review required: the changelog declares a BEHAVIORAL breaking change to a symbol your "
                                "production code calls directly" + symloc + "; build, tests, and API-diff cannot confirm "
                                "whether the changed behavior affects your usage — verify against the release notes")
                mr["evidenceAxis"] = "declared behavioral change on a directly-called symbol, unverified by build/test/api-diff"
            elif sk == "package":
                sev = surf_ev[0] if surf_ev else None
                local = (result.get("surface_by_path", {}).get(sev["path"], {}).get("local") or sev["path"].split("/")[-1]) if sev else ""
                symloc = (" (e.g. %s.%s at %s:%s)" % (local, sev["symbol"], sev["file"], sev["line"])) if sev else ""
                mr["reason"] = ("review required: the changelog declares a BEHAVIORAL breaking change inside a package your "
                                "production code uses" + loc + symloc + "; the change is internal to the package, so whether it "
                                "affects you depends on your runtime data/configuration — build, tests, and API-diff cannot "
                                "confirm or rule it out; verify against the release notes")
                mr["evidenceAxis"] = "declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff"
            elif sk == "import_only":
                mr["reason"] = ("review required: your production code imports the affected package" + loc + " but does not "
                                "appear to reference its exported surface (possibly a blank or transitive import); the changelog "
                                "declares a BEHAVIORAL change whose impact we cannot confirm or rule out — lower-risk, but verify "
                                "against the release notes")
                mr["evidenceAxis"] = "declared behavioral change, package imported but exported surface not referenced in production"
            else:
                mr["reason"] = ("review required: the changelog declares a BEHAVIORAL breaking change and your "
                                "code imports the affected package" + loc + ", but build, tests, and API-diff "
                                "cannot confirm or rule out that your usage triggers it — not a confirmed break; "
                                "verify against the release notes")
                mr["evidenceAxis"] = "declared behavioral change, import-reachable but unverified by build/test/api-diff"
        elif test_only:
            mr["tag"] = "Medium"
            mr["reason"] = "declared breaking change is only reachable from test/CI code: " + ", ".join(paths)
            mr["evidenceAxis"] = "declared breaking change, reachable only from non-production code"
        else:
            mr["tag"] = "Medium"
            mr["reason"] = "declared breaking change is in " + ", ".join(paths) + ", which your code does not import (not reachable)"
            mr["evidenceAxis"] = "declared breaking change, not reachable (package not imported)"
        pr_data["merge_risk"] = mr
        if isinstance(pr_data.get("deterministic"), dict) and isinstance(pr_data["deterministic"].get("merge_risk"), dict):
            pr_data["deterministic"]["merge_risk"] = mr
    try:
        _resolve_declared_break_reachability(pr_data, deterministic, eco)
    except Exception as _dbr_e:
        print("  declared-break reachability resolution skipped:", str(_dbr_e)[:120])

    # -- Structured per-signal evidence ------------------------------------
    def _tail_text(value, limit=4000):
        value = value or ""
        return value[-limit:] if len(value) > limit else value

    def _read_scratch(name):
        try:
            with open(os.path.join(bc_scratch_dir, name)) as f:
                return f.read()
        except (IOError, OSError):
            return ""

    def _read_scratch_int(name):
        raw = _read_scratch(name).strip()
        try:
            return int(raw) if raw not in ("", "null", "None") else None
        except ValueError:
            return None

    def _status_from_exit(exit_code):
        if exit_code is None:
            return "skipped"
        return "ran_pass" if exit_code == 0 else "ran_fail"

    def _step_detail(step_names, default=""):
        for st in steps:
            if st.get("step") in step_names:
                detail = st.get("detail") or st.get("status") or default
                return str(detail)
        return default

    def _ev(signal, label, status, command="", stdout="", exit_code=None, summary="", na_reason=""):
        return {
            "signal": signal,
            "label": label,
            "status": status,
            "command": command or "",
            "stdout": _tail_text(stdout),
            "exit_code": exit_code,
            "summary": summary or "",
            "na_reason": na_reason if status in ("n/a", "skipped") else "",
        }

    evidence = []
    dep_cmd = _read_scratch(f"_bc_evidence_dep_command_{pr_num}.txt").strip()
    build_cmd = _read_scratch(f"_bc_evidence_build_command_{pr_num}.txt").strip()
    test_cmd = _read_scratch(f"_bc_evidence_test_command_{pr_num}.txt").strip()
    smoke_cmd = _read_scratch(f"_bc_evidence_smoke_command_{pr_num}.txt").strip()
    usage_raw = _read_scratch(f"_bc_usage_raw_{pr_num}.txt")
    cli_stdout = _read_scratch(f"_bc_cli_output_{pr_num}.txt")
    npm_audit_stdout = _read_scratch(f"_bc_npm_audit_output_{pr_num}.txt")
    smoke_stdout = _read_scratch(f"_bc_smoke_output_{pr_num}.txt")
    smoke_exit_recorded = _read_scratch_int(f"_bc_smoke_exit_{pr_num}.txt")

    go_resolution = pr_data.get("go_resolution", {}) if isinstance(pr_data.get("go_resolution"), dict) else {}
    if eco == "gomod":
        dep_cmd = go_resolution.get("command") or dep_cmd or "go mod tidy"
        dep_out = go_resolution.get("output_tail") or ""
        dep_exit = _read_scratch_int(f"_bc_go_resolution_exit_{pr_num}.txt")
        if dep_cmd:
            evidence.append(_ev("dependency_resolution", "Dependency resolution", _status_from_exit(dep_exit), dep_cmd, dep_out, dep_exit, _step_detail({"dependency_resolution"}, "dependency resolution")))
        else:
            evidence.append(_ev("dependency_resolution", "Dependency resolution", "n/a", "", "", None, "dependency resolution not applicable", "no Go dependency resolution command recorded"))
    elif eco == "npm":
        dep_out = build_output.split("--- tsc ---", 1)[0]
        dep_exit = int(pr_install_exit_str) if pr_install_exit_str not in ("", "-1") else None
        evidence.append(_ev("dependency_resolution", "Dependency resolution", _status_from_exit(dep_exit), dep_cmd or "npm ci --ignore-scripts", dep_out, dep_exit, _step_detail({"dependency_resolution"}, "dependency resolution")))
    elif eco == "pip":
        dep_out = build_output.split("--- import check ---", 1)[0]
        dep_exit = 0 if install_ok else (pr_data.get("build", {}).get("pr_exit") if pr_data.get("build", {}).get("pr_exit") != -1 else None)
        if dep_cmd:
            evidence.append(_ev("dependency_resolution", "Dependency resolution", _status_from_exit(dep_exit), dep_cmd, dep_out, dep_exit, _step_detail({"dependency_resolution"}, "dependency resolution")))
        else:
            evidence.append(_ev("dependency_resolution", "Dependency resolution", "n/a", "", dep_out, None, "no Python dependency manifest found", "no requirements.txt, pyproject.toml, or poetry.lock found"))

    # Build/type-check/import-check signal
    build_exit = None
    if eco == "npm":
        build_exit = int(pr_tsc_exit_str) if pr_tsc_exit_str not in ("", "-1") else None
    elif eco in ("gomod", "pip"):
        build_exit = pr_data.get("build", {}).get("pr_exit")
        build_exit = None if build_exit == -1 else build_exit
    if build_cmd:
        evidence.append(_ev("build", "Build", _status_from_exit(build_exit), build_cmd, build_output, build_exit, _step_detail({"type_check"}, build_verdict)))
    else:
        reason = "no tsconfig.json" if eco == "npm" else ("Go unavailable or build skipped" if eco == "gomod" else "Python import check not run")
        evidence.append(_ev("build", "Build", "n/a", "", build_output, build_exit, _step_detail({"type_check"}, "build not run"), reason))

    # API diff and usage scan come from the deterministic pipeline and shell grep scan.
    if eco in ("gomod", "npm", "pip"):
        if deterministic:
            api_changes = len(deterministic.get("api_changes_detail", deterministic.get("apiChanges", [])) or [])
            compatible = deterministic.get("verification", {}).get("compatible")
            status = "ran_fail" if compatible is False else "ran_pass"
            evidence.append(_ev("api_diff", "API diff", status, "node .github/actions/breakability-check/index.js --json", cli_stdout, 0, f"api_changes={api_changes}, compatible={compatible}"))
        else:
            evidence.append(_ev("api_diff", "API diff", "skipped", "node .github/actions/breakability-check/index.js --json", cli_stdout, None, "pipeline output unavailable", "pipeline skipped or produced no JSON"))
        usage_cmd = {"npm": "scan_usage_npm", "gomod": "scan_usage_go", "pip": "scan_usage_pip"}.get(eco, "")
        evidence.append(_ev("usage_scan", "Usage scan", "ran_pass", usage_cmd, usage_raw, 0, f"{len(files_importing)} importing file(s) found"))

    # Vulnerability scan evidence: npm audit for npm, govulncheck for Go, none for pip.
    if eco == "npm":
        if npm_audit_stdout:
            audit_status = "ran_fail" if (audit_critical > 0 or audit_high > 0) else "ran_pass"
            evidence.append(_ev("vuln_scan", "Vulnerability scan", audit_status, "npm audit --json --production", npm_audit_stdout, 0, f"critical={pr_data['npm_audit']['critical']}, high={pr_data['npm_audit']['high']}"))
        else:
            evidence.append(_ev("vuln_scan", "Vulnerability scan", "skipped", "npm audit --json --production", "", None, "npm audit not run", "dependency installation failed or npm audit skipped"))
    elif eco == "gomod":
        if vuln_status in ("skipped_disabled",):
            evidence.append(_ev("vuln_scan", "Vulnerability scan", "skipped", "govulncheck ./...", vuln_output, None, "govulncheck disabled", "govulncheck disabled by config"))
        elif vuln_status in ("not_installed",):
            evidence.append(_ev("vuln_scan", "Vulnerability scan", "n/a", "govulncheck ./...", vuln_output, None, "govulncheck unavailable", "govulncheck tool unavailable"))
        else:
            vuln_ev_status = "ran_pass" if vuln_status in ("ok", "ok_preexisting") else "ran_fail"
            evidence.append(_ev("vuln_scan", "Vulnerability scan", vuln_ev_status, "govulncheck ./...", vuln_output, None, vuln_status))
    elif eco == "pip":
        evidence.append(_ev("vuln_scan", "Vulnerability scan", "n/a", "", "", None, "Python vulnerability scan not configured", "no Python vulnerability scanner configured"))

    # Test and smoke evidence.
    if test_ran_val:
        if eco == "gomod" and no_go_tests:
            evidence.append(_ev("tests", "Tests", "n/a", test_cmd or "go test ./...", test_output, test_exit_val, _step_detail({"test_suite"}, "no Go test files present"), "no Go test files present"))
        else:
            evidence.append(_ev("tests", "Tests", _status_from_exit(test_exit_val), test_cmd, test_output, test_exit_val, _step_detail({"test_suite"}, "tests ran")))
    else:
        evidence.append(_ev("tests", "Tests", "skipped", test_cmd, test_output, None, _step_detail({"test_suite"}, "tests not triggered"), "not triggered"))

    if eco == "npm":
        if smoke_ran_val:
            evidence.append(_ev("smoke", "Smoke probe", _status_from_exit(smoke_exit_recorded if smoke_exit_recorded is not None else smoke_exit_val), smoke_cmd, smoke_stdout, smoke_exit_recorded if smoke_exit_recorded is not None else smoke_exit_val, _step_detail({"smoke_probe"}, "smoke probe ran")))
        else:
            evidence.append(_ev("smoke", "Smoke probe", "skipped", smoke_cmd, smoke_stdout, None, _step_detail({"smoke_probe"}, "smoke probe not run"), "not triggered or no dist entrypoint"))

    pr_data["evidence"] = evidence

    pr_data["verification_steps"] = steps

    data["prs"][pr_num] = pr_data

    _tmp = results_file + ".tmp"
    with open(_tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.rename(_tmp, results_file)

    print(f"  ✓ PR #{pr_num} written to results")

    # Cleanup temp files
    for p in [det_path, files_path, build_out_path, test_out_path, new_errors_path]:
        try:
            os.remove(p)
        except (FileNotFoundError, OSError):
            pass


if __name__ == "__main__":
    main()
