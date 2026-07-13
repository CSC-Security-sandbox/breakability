#!/usr/bin/env python3
"""Verify all Python module imports work after reorganization.

Run from scripts/:
  python3 check_imports.py
"""
import importlib
import sys
import os

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ".")

# CLI scripts extracted from heredocs require these env vars at import time
os.environ.setdefault("RESULTS_FILE", "/dev/null")
os.environ.setdefault("OWNER_REPO", "test/test")
os.environ.setdefault("REPO_ROOT", ".")

MODULES = [
    "evidence_contract",
    "verdict_contract",
    "ai_backend",
    "generate_ai_comments",
    "generate_ai_merge_plan",
    "generate_ai_verdicts",
    "ecosystem_adapters",
    "npm_changelog",
    "core.evidence_contract",
    "core.verdict_contract",
    "core.build_results_schema",
    "ai.ai_backend",
    "ai.generate_ai_comments",
    "ai.generate_ai_merge_plan",
    "ai.generate_ai_verdicts",
    "ecosystems.ecosystem_adapters",
    "ecosystems.npm_changelog",
    "policy_lowering",
    "callsite_impact",
    "release_notes_evidence",
    "changelog_comprehension",
    "break_class_router",
    "cross_pr_reconciler",
    "agent_adjudicator",
    "ci_classifier",
    "cve_security_posture",
    "reconcile_adjudication",
    "dynamic_probe_runner",
    "breakability_analyst",
    "rendering",
    "rendering.normalizers",
    "rendering.helpers",
    "rendering.renderer",
    "rendering.cli",
    "breakability_eval",
]

# CLI-only scripts (extracted from heredocs) execute at import time and need
# specific env vars + network access. Verified separately via bash -n / direct run.
CLI_SCRIPTS = [
    "cross_pr_deps",
    "security_posture_scan",
    "batch_vuln_summary",
    "write_skip_entry",
    "discover_peer_groups",
]

failed = []
skipped = len(CLI_SCRIPTS)
for mod in MODULES:
    try:
        importlib.import_module(mod)
    except Exception as e:
        failed.append((mod, str(e)))

if failed:
    print(f"FAIL: {len(failed)}/{len(MODULES)} modules failed to import:")
    for mod, err in failed:
        print(f"  {mod}: {err}")
    sys.exit(1)
else:
    print(f"OK: all {len(MODULES)} library modules imported ({skipped} CLI scripts skipped)")
