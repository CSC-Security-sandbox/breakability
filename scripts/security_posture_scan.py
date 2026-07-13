#!/usr/bin/env python3
"""Security posture scan — Dependabot alert attribution and govulncheck aggregation.

Fetches open Dependabot alerts via GitHub API, cross-references with
analyzed PRs to identify which PRs fix which CVEs (precise semver matching),
and writes security_posture + govulncheck data to build-results.json.

Called by build-check.sh after cross-PR dependency detection.

Env vars:
  RESULTS_FILE   — path to build-results.json (required)
  OWNER_REPO     — GitHub owner/repo string (required)
  BREAKABILITY_PAT — optional fine-grained PAT with Dependabot-alerts:read
"""
import json, subprocess, os

owner_repo = os.environ["OWNER_REPO"]
results_file = os.environ["RESULTS_FILE"]

_alerts_env = os.environ.copy()
if os.environ.get("BREAKABILITY_PAT"):
    _alerts_env["GH_TOKEN"] = os.environ["BREAKABILITY_PAT"]
    _alerts_env["GITHUB_TOKEN"] = os.environ["BREAKABILITY_PAT"]
try:
    result = subprocess.run(
        ["gh", "api", f"repos/{owner_repo}/dependabot/alerts",
         "--jq", '.[] | {number, state, security_advisory: {ghsa_id: .security_advisory.ghsa_id, cve_id: .security_advisory.cve_id, severity: .security_advisory.severity, summary: .security_advisory.summary}, security_vulnerability: {first_patched_version: .security_vulnerability.first_patched_version.identifier, vulnerable_version_range: .security_vulnerability.vulnerable_version_range}, dependency: {package: .dependency.package.name, ecosystem: .dependency.package.ecosystem, manifest_path: .dependency.manifest_path}}',
         "-X", "GET", "--paginate"],
        capture_output=True, text=True, timeout=60, env=_alerts_env
    )
    if result.returncode != 0:
        print("  Could not fetch Dependabot alerts (may need security permissions)")
        print(f"  stderr: {(result.stderr or '')[:200]}")
        alerts_raw = "[]"
    else:
        lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
        alerts = [json.loads(l) for l in lines]
        alerts_raw = json.dumps(alerts)
except Exception as e:
    print(f"  Security scan error: {e}")
    alerts = []
    alerts_raw = "[]"

try:
    alerts = json.loads(alerts_raw) if isinstance(alerts_raw, str) else alerts
except:
    alerts = []

open_alerts = [a for a in alerts if a.get("state") == "open"]
severity_counts = {}
for a in open_alerts:
    sev = a.get("security_advisory", {}).get("severity", "unknown")
    severity_counts[sev] = severity_counts.get(sev, 0) + 1

with open(results_file) as f:
    data = json.load(f)

prs = data.get("prs", {})
pr_cves = {}
total_cve_count = 0
for num, pr in prs.items():
    cves = pr.get("cves", [])
    if cves:
        pr_cves[num] = cves
        total_cve_count += len(cves)

fixes_by_pr = {}
for num, pr in prs.items():
    pkg = pr.get("package", "")
    matching_alerts = [a for a in open_alerts
                       if a.get("dependency", {}).get("package", "") == pkg]
    if matching_alerts:
        fixes_by_pr[num] = {
            "package": pkg,
            "alert_count": len(matching_alerts),
            "severities": [a.get("security_advisory", {}).get("severity", "unknown") for a in matching_alerts],
            "cve_ids": [a.get("security_advisory", {}).get("cve_id") or a.get("security_advisory", {}).get("ghsa_id", "") for a in matching_alerts]
        }

def _parse_semver(v):
    if not v: return None
    s = str(v).lstrip("v").lstrip("=").strip()
    for sep in ("-", "+"):
        if sep in s: s = s.split(sep, 1)[0]
    parts = s.split(".")
    try:
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - min(3, len(parts)))
    except ValueError:
        return None

def _semver_gte(a, b):
    pa, pb = _parse_semver(a), _parse_semver(b)
    if pa is None or pb is None: return False
    return pa >= pb

cve_fixes = []
orphan_alerts = []
matched_alert_ids = set()

for a in open_alerts:
    alert_pkg = a.get("dependency", {}).get("package", "")
    fpv = a.get("security_vulnerability", {}).get("first_patched_version")
    sev = a.get("security_advisory", {}).get("severity", "unknown")
    cve = a.get("security_advisory", {}).get("cve_id") or a.get("security_advisory", {}).get("ghsa_id", "")
    summary = a.get("security_advisory", {}).get("summary", "")
    alert_num = a.get("number")
    matched = False
    for num, pr in prs.items():
        pr_pkg = pr.get("package", "")
        pr_to = pr.get("to", "")
        bumped = pr.get("bumped_modules") or {}
        if pr_pkg == alert_pkg:
            resulting_ver = pr_to
            via = "primary"
        elif alert_pkg in bumped:
            resulting_ver = bumped[alert_pkg]
            via = "transitive"
        else:
            continue
        if fpv and _semver_gte(resulting_ver, fpv):
            cve_fixes.append({
                "pr": int(num) if str(num).isdigit() else num,
                "package": alert_pkg,
                "cve_id": cve,
                "severity": sev,
                "from_version": pr.get("from", ""),
                "to_version": resulting_ver,
                "first_patched_version": fpv,
                "via": via,
                "summary": summary[:200],
            })
            matched_alert_ids.add(alert_num)
            matched = True
    if not matched:
        orphan_alerts.append({
            "cve_id": cve,
            "package": alert_pkg,
            "severity": sev,
            "first_patched_version": fpv or "unknown",
            "summary": summary[:200],
        })

_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "moderate": 2, "low": 3, "unknown": 4}
cve_fixes.sort(key=lambda x: (_SEV_ORDER.get((x["severity"] or "").lower(), 4), x.get("pr", 9999)))
orphan_alerts.sort(key=lambda x: _SEV_ORDER.get((x["severity"] or "").lower(), 4))

security_posture = {
    "total_open_alerts": len(open_alerts),
    "severity_counts": severity_counts,
    "total_cves_in_prs": total_cve_count,
    "prs_fixing_alerts": fixes_by_pr,
    "prs_with_cves": pr_cves,
    "alerts_fixable_by_merging": sum(f["alert_count"] for f in fixes_by_pr.values()),
    "cve_fixes": cve_fixes,
    "orphan_alerts": orphan_alerts,
}

_govuln_block = {"main_baseline": {"status": "unknown", "findings": []}, "prs_scanned": 0, "prs_with_new_vulns": 0, "total_new_findings": set()}
try:
    if os.path.exists("/tmp/_bc_main_vuln_status.txt"):
        with open("/tmp/_bc_main_vuln_status.txt") as f:
            _govuln_block["main_baseline"]["status"] = f.read().strip() or "unknown"
    if os.path.exists("/tmp/_bc_main_vuln_findings.txt"):
        with open("/tmp/_bc_main_vuln_findings.txt") as f:
            _govuln_block["main_baseline"]["findings"] = sorted(set(l.strip() for l in f.readlines() if l.strip()))
    for _pn, _pr in data.get("prs", {}).items():
        _vs = _pr.get("vuln_status", "")
        if _vs in ("ok", "vulns_found", "ok_preexisting"):
            _govuln_block["prs_scanned"] += 1
        _new = _pr.get("vuln_new_findings", [])
        if _new:
            _govuln_block["prs_with_new_vulns"] += 1
            for _f in _new:
                _govuln_block["total_new_findings"].add(_f)
    _govuln_block["total_new_findings"] = sorted(_govuln_block["total_new_findings"])
except Exception as _e:
    _govuln_block["error"] = str(_e)

data["security_posture"] = security_posture
data["govulncheck"] = _govuln_block
_tmp = results_file + ".tmp"
with open(_tmp, "w") as f:
    json.dump(data, f, indent=2)
os.rename(_tmp, results_file)

print(f"  Open vulnerability alerts: {len(open_alerts)}")
for sev, count in sorted(severity_counts.items(), key=lambda x: {'critical':0,'high':1,'medium':2,'low':3}.get(x[0],4)):
    print(f"    {sev}: {count}")
print(f"  PRs that fix known alerts: {len(fixes_by_pr)}")
print(f"  Alerts fixable by merging open PRs: {security_posture['alerts_fixable_by_merging']}")
if total_cve_count:
    print(f"  CVEs referenced in PR bodies: {total_cve_count}")
