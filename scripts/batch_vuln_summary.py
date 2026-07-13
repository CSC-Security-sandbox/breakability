"""Aggregate per-PR vulnerability scan results into a batch-level summary.

Reads the batch results JSON file (path from RESULTS_FILE env var), embeds
the main-baseline vuln scan status and findings into it under the
"main_baseline_vuln" key, and writes the updated JSON back atomically.

Extracted from the BATCHVULN heredoc in build-check.sh.
"""
import json, os

rf = os.environ["RESULTS_FILE"]
try:
    with open(rf) as f: d = json.load(f)
except Exception: d = {}
mb = {"status": "unknown", "findings": []}
try:
    if os.path.exists("/tmp/_bc_main_vuln_status.txt"):
        mb["status"] = open("/tmp/_bc_main_vuln_status.txt").read().strip() or "unknown"
    if os.path.exists("/tmp/_bc_main_vuln_findings.txt"):
        mb["findings"] = sorted(set(l.strip() for l in open("/tmp/_bc_main_vuln_findings.txt") if l.strip()))
except Exception as e:
    mb["error"] = str(e)
d["main_baseline_vuln"] = mb
with open(rf + ".tmp", "w") as f: json.dump(d, f, indent=2)
os.rename(rf + ".tmp", rf)
print(f"  [batch] main_baseline_vuln: status={mb['status']} findings={len(mb['findings'])}")
