"""Write a minimal skip entry for PRs with the breakability:skip label.

Reads PR metadata from environment variables and writes a skip record into the
results JSON file so that skipped PRs are acknowledged in the final report
(avoids pr_count mismatch).

Expected env vars:
    RESULTS_FILE  — path to the results JSON file
    PR_NUM        — pull request number
    _SKIP_BRANCH  — PR branch name (unused in the entry but available)
"""

import json
import os

results_file = os.environ["RESULTS_FILE"]
pr_num = os.environ["PR_NUM"]
try:
    with open(f"/tmp/_bc_skip_title_{pr_num}.txt") as f:
        pr_title = f.read().strip()
except Exception:
    pr_title = "unknown"
pr_branch = os.environ["_SKIP_BRANCH"]
with open(results_file) as f:
    data = json.load(f)
data["prs"][pr_num] = {
    "package": pr_title,
    "from": "",
    "to": "",
    "ecosystem": "unknown",
    "bump": "unknown",
    "dep_type": "unknown",
    "dep_relation": "unknown",
    "cves": [],
    "build": {"verdict": "skipped", "main_exit": -1, "pr_exit": -1, "output_tail": "", "new_errors": [], "install_method": "none", "error_class": ""},
    "test": {"ran": False, "exit": None, "output_tail": ""},
    "smoke": {"ran": False, "exit": None},
    "files_importing": [],
    "additional_imports": [],
    "diff_lines": 0,
    "diff_truncated": False,
    "pkg_dir": "/",
    "cascade_impact": [],
    "nestjs_peer_warning": "",
    "install_ok": False,
    "additional_packages": "",
    "mergeable_status": "UNKNOWN",
    "npm_audit": {"critical": 0, "high": 0},
    "ownership_class": "unknown",
    "verification_level": -1,
    "verification_label": "NA_not_applicable",
    "verification_steps": [],
    "evidence": [{
        "signal": "dependency_resolution",
        "label": "Dependency resolution",
        "status": "skipped",
        "command": "",
        "stdout": "",
        "exit_code": None,
        "summary": "PR skipped by breakability:skip label",
        "na_reason": "breakability:skip label"
    }],
    "skip_reason": "breakability:skip label"
}
_tmp = results_file + ".tmp"
with open(_tmp, "w") as f:
    json.dump(data, f, indent=2)
os.rename(_tmp, results_file)
print(f"  ✓ PR #{pr_num} written (skipped)")
