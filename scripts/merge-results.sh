#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# merge-results.sh — Merge parallel batch results into one build-results.json
#
# Called after matrix deterministic jobs complete. Merges partial results,
# runs cross-PR dependency detection, security posture scan, and comment cleanup.
#
# Expects:
#   - /tmp/batch-results/batch-*/build-results-*.json  (downloaded artifacts)
#   - /tmp/batch-results/batch-*/pr-*.diff              (PR diffs)
#   - /tmp/batch-results/batch-*/_bc_workspace_graph.json (from any batch)
#   - /tmp/batch-results/batch-*/_bc_peer_groups.json   (from any batch)
#   - GH_TOKEN / GITHUB_TOKEN env
# ──────────────────────────────────────────────────────────────────────────────
set -u
export LC_ALL=en_US.UTF-8
unset GH_TOKEN

RESULTS_FILE="/tmp/build-results.json"
OWNER_REPO="${GITHUB_REPOSITORY:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BATCH_RESULTS_DIR="${BATCH_RESULTS_DIR:-/tmp/batch-results}"
WORKSPACE_GRAPH_FILE="${WORKSPACE_GRAPH_FILE:-/tmp/_bc_workspace_graph-${GITHUB_RUN_ID:-$$}.json}"
PEER_GROUPS_FILE="${PEER_GROUPS_FILE:-/tmp/_bc_peer_groups-${GITHUB_RUN_ID:-$$}.json}"
export BATCH_RESULTS_DIR
export WORKSPACE_GRAPH_FILE
export PEER_GROUPS_FILE

echo "════════════ MERGE BATCH RESULTS ════════════"

# ── Step 1: Merge partial JSON files ──────────────────────────────────────────
python3 << 'MERGEEOF'
import json, glob, os, re

merged = {
    "metadata": {},
    "main_build": {},
    "prs": {},
    "cross_pr_deps": [],
    "workspace_graph": {},
    "nestjs_skew": [],
    "security_posture": {}
}

total_prs = 0
requested_prs = set()
subset_requested = False

def _parse_pr_numbers(value):
    out = []
    for token in str(value or "").split(","):
        token = token.strip().lstrip("#")
        if re.fullmatch(r"[0-9]+", token or ""):
            out.append(int(token))
    return out

requested_prs.update(_parse_pr_numbers(os.environ.get("BREAKABILITY_PR_NUMBERS") or os.environ.get("PR_FILTER")))
explicit_batch_dir = bool(os.environ.get("BATCH_RESULTS_DIR"))
batch_dir = os.environ.get("BATCH_RESULTS_DIR") or "/tmp/batch-results"
batch_files = sorted(glob.glob(os.path.join(batch_dir, "batch-*", "build-results-*.json")))
if not batch_files and not explicit_batch_dir:
    # Fallback: single-mode (no batches)
    batch_files = ["/tmp/build-results.json"] if os.path.exists("/tmp/build-results.json") else []

print(f"  Found {len(batch_files)} batch result files")

# Warn when batch directories exist but are missing their result file
# (indicates a batch job crashed before writing output — those PRs will be absent)
all_batch_dirs = sorted(glob.glob(os.path.join(batch_dir, "batch-*")))
missing_batches = []
for bd in all_batch_dirs:
    batch_id = os.path.basename(bd).replace("batch-", "")
    result_pattern = os.path.join(bd, f"build-results-{batch_id}.json")
    if not glob.glob(result_pattern):
        missing_batches.append(batch_id)
if missing_batches:
    print(f"  ⚠️  WARNING: batch(es) {', '.join(missing_batches)} produced no result file — PRs in those batches will be absent from the merged output")

# Track incomplete batches so merge plan can warn users
incomplete_batches = missing_batches[:]

for bf in batch_files:
    print(f"  Merging: {bf}")
    # V9.8 iter6 (G): tolerate corrupt/truncated batch artifacts instead of crashing the whole merge.
    try:
        with open(bf) as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError) as _err:
        print(f"  ⚠️  SKIPPING corrupt batch {bf}: {_err}")
        incomplete_batches.append(os.path.basename(os.path.dirname(bf)).replace('batch-', ''))
        continue
    
    meta = data.get("metadata", {}) or {}
    if meta.get("subset_requested"):
        subset_requested = True
    for n in meta.get("requested_pr_numbers", []) or []:
        try:
            requested_prs.add(int(n))
        except (TypeError, ValueError):
            pass

    # Merge metadata (take first, update count)
    if not merged["metadata"]:
        merged["metadata"] = meta
    
    # Merge main_build (take first non-empty)
    if not merged["main_build"] and data.get("main_build"):
        merged["main_build"] = data["main_build"]
    
    # Merge PRs (union of all)
    for num, pr_data in data.get("prs", {}).items():
        merged["prs"][num] = pr_data
        total_prs += 1
    
    # Merge workspace_graph (take first non-empty)
    if not merged["workspace_graph"] and data.get("workspace_graph"):
        merged["workspace_graph"] = data["workspace_graph"]
    
    # Merge nestjs_skew (take first non-empty)
    if not merged["nestjs_skew"] and data.get("nestjs_skew"):
        merged["nestjs_skew"] = data["nestjs_skew"]

if requested_prs:
    subset_requested = True
if subset_requested:
    requested_sorted = sorted(requested_prs)
    before_filter = sorted(int(n) for n in merged["prs"].keys() if str(n).isdigit())
    merged["prs"] = {
        num: pr
        for num, pr in merged["prs"].items()
        if str(num).isdigit() and int(num) in requested_prs
    }
    merged["metadata"]["subset_requested"] = True
    merged["metadata"]["requested_pr_numbers"] = requested_sorted
    merged["metadata"]["missing_pr_numbers"] = [n for n in requested_sorted if str(n) not in merged["prs"]]
    dropped = [n for n in before_filter if n not in requested_prs]
    if dropped:
        merged["metadata"]["dropped_unrequested_pr_numbers"] = dropped

# Update total PR count and track incomplete batches after subset filtering.
total_prs = len(merged["prs"])
merged["metadata"]["pr_count"] = total_prs
selected_prs = sorted(int(n) for n in merged["prs"].keys() if str(n).isdigit())
merged["metadata"]["selected_pr_numbers"] = selected_prs
expected_count = int(os.environ.get("EXPECTED_PR_COUNT", "0"))
if expected_count > 0 and total_prs < expected_count:
    merged["metadata"]["incomplete"] = True
    merged["metadata"]["expected_pr_count"] = expected_count
    merged["metadata"]["missing_pr_count"] = expected_count - total_prs
    print(f"  ⚠️  INCOMPLETE: expected {expected_count} PRs, got {total_prs} ({expected_count - total_prs} missing)")
if incomplete_batches:
    merged["metadata"]["incomplete_batches"] = incomplete_batches

with open("/tmp/build-results.json", "w") as f:
    json.dump(merged, f, indent=2)

print(f"  Total merged PRs: {total_prs}")
MERGEEOF

# ── Step 2: Collect PR diffs into /tmp/ ───────────────────────────────────────
echo ""
echo "Collecting PR diffs..."
rm -f /tmp/pr-*.diff
_MERGE_PR_FILTER=",${BREAKABILITY_PR_NUMBERS:-${PR_FILTER:-}},"
_MERGE_PR_FILTER="${_MERGE_PR_FILTER// /}"
for diff_file in "$BATCH_RESULTS_DIR"/batch-*/pr-*.diff; do
  [[ -f "$diff_file" ]] || continue
  if [[ "$_MERGE_PR_FILTER" != ",," ]]; then
    _diff_pr="$(basename "$diff_file" | sed -E 's/^pr-([0-9]+)\.diff$/\1/')"
    [[ "$_MERGE_PR_FILTER" == *",${_diff_pr},"* ]] || continue
  fi
  cp "$diff_file" /tmp/ 2>/dev/null || true
done
DIFF_COUNT=$(find /tmp -maxdepth 1 -name 'pr-*.diff' 2>/dev/null | wc -l | tr -d ' ')
echo "  Collected $DIFF_COUNT PR diffs"

# ── Step 3: Collect workspace graph + peer groups ─────────────────────────────
# These were generated by each batch; take the first available
rm -f "$WORKSPACE_GRAPH_FILE" "$PEER_GROUPS_FILE"
for f in "$BATCH_RESULTS_DIR"/batch-*/_bc_workspace_graph.json; do
  [[ -f "$f" ]] && { cp "$f" "$WORKSPACE_GRAPH_FILE"; break; }
done
for f in "$BATCH_RESULTS_DIR"/batch-*/_bc_peer_groups.json; do
  [[ -f "$f" ]] && { cp "$f" "$PEER_GROUPS_FILE"; break; }
done

# ── Step 4: Cross-PR dependency detection ─────────────────────────────────────
echo ""
echo "════════════ CROSS-PR DEPENDENCIES ════════════"

python3 << 'CROSSDEPS'
import json, os, re

KNOWN_DEPS = {
    ("flask", "jinja2"): ("flask depends on jinja2", "jinja2 first"),
    ("flask", "werkzeug"): ("flask depends on werkzeug", "werkzeug first"),
    ("requests", "urllib3"): ("requests depends on urllib3", "urllib3 first"),
    ("requests", "certifi"): ("requests depends on certifi", "certifi first"),
    ("express", "@types/express"): ("types follow express", "express first"),
    ("lodash", "@types/lodash"): ("types follow lodash", "lodash first"),
    ("jsonwebtoken", "@types/jsonwebtoken"): ("types follow jsonwebtoken", "jsonwebtoken first"),
    ("react", "react-dom"): ("react and react-dom must match", "merge together"),
    ("react", "@types/react"): ("types follow react", "react first"),
    ("react-dom", "@types/react-dom"): ("types follow react-dom", "react-dom first"),
}
try:
    with open(os.environ.get("PEER_GROUPS_FILE", "/tmp/_bc_peer_groups.json")) as f: pd = json.load(f)
    for i, a in enumerate(pd.get("nestjs_group", [])):
        for b in pd.get("nestjs_group", [])[i+1:]:
            KNOWN_DEPS.setdefault((a, b), (f"NestJS peer group: {a} + {b}", "merge together"))
    for i, a in enumerate(pd.get("react_group", [])):
        for b in pd.get("react_group", [])[i+1:]:
            KNOWN_DEPS.setdefault((a, b), (f"React peer group: {a} + {b}", "merge together"))
    for pn, pl in pd.get("peer_groups", {}).items():
        for peer in pl:
            key = tuple(sorted([pn.lower(), peer.lower()]))
            KNOWN_DEPS.setdefault(key, (f"{pn} peerDep on {peer}", "check compatibility"))
except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError): pass
with open("/tmp/build-results.json") as f: data = json.load(f)
cross_deps = []
prs = data.get("prs", {})
pr_list = list(prs.items())
for i, (na, pa) in enumerate(pr_list):
    for nb, pb in pr_list[i+1:]:
        a, b = pa.get("package", "").lower(), pb.get("package", "").lower()
        for (da, db), (reason, order) in KNOWN_DEPS.items():
            if (a == da and b == db) or (a == db and b == da):
                cross_deps.append({"pr_a": int(na), "pr_b": int(nb), "reason": reason, "merge_order": order})
nestjs_prs = {}
for num, pr in prs.items():
    if pr.get("package", "").startswith("@nestjs/"):
        nestjs_prs.setdefault(pr.get("pkg_dir", "/"), []).append((num, pr["package"]))
for pkg_dir, entries in nestjs_prs.items():
    if len(entries) > 1:
        for i, (na, pa) in enumerate(entries):
            for nb, pb in entries[i+1:]:
                if not any((d["pr_a"]==int(na) and d["pr_b"]==int(nb)) or (d["pr_a"]==int(nb) and d["pr_b"]==int(na)) for d in cross_deps):
                    cross_deps.append({"pr_a": int(na), "pr_b": int(nb), "reason": f"NestJS in {pkg_dir}: {pa} + {pb} must upgrade together", "merge_order": "merge together"})
# Same package across different Go modules (A4-5): flag PRs that upgrade the same
# package in different modules — developers may not realize all 3 need merging
same_pkg_groups = {}
for num, pr in prs.items():
    pkg = pr.get("package", "")
    if pkg and pr.get("ecosystem") == "gomod":
        same_pkg_groups.setdefault(pkg, []).append((num, pr.get("pkg_dir", "/")))
for pkg, entries in same_pkg_groups.items():
    if len(entries) > 1:
        for i, (na, dir_a) in enumerate(entries):
            for nb, dir_b in entries[i+1:]:
                if dir_a != dir_b:
                    if not any((d["pr_a"]==int(na) and d["pr_b"]==int(nb)) or (d["pr_a"]==int(nb) and d["pr_b"]==int(na)) for d in cross_deps):
                        cross_deps.append({"pr_a": int(na), "pr_b": int(nb), "reason": f"Same package ({pkg}) in different modules: {dir_a} + {dir_b}", "merge_order": "merge all to fully upgrade"})
        if len(entries) > 1:
            dirs = [d for _, d in entries]
            nums = [n for n, _ in entries]
            if len(set(dirs)) > 1:
                print(f"  Same package group: {pkg} in {len(entries)} modules (PRs: {', '.join('#'+n for n in nums)})")
# K8s module coordination: k8s.io modules must be upgraded together
K8S_MODULES = {"k8s.io/api", "k8s.io/apimachinery", "k8s.io/client-go", "k8s.io/apiserver", "k8s.io/apiextensions-apiserver"}
k8s_prs = [(num, pr["package"]) for num, pr in prs.items() if any(pr.get("package", "").startswith(m) for m in K8S_MODULES)]
if len(k8s_prs) > 1:
    for i, (na, pa) in enumerate(k8s_prs):
        for nb, pb in k8s_prs[i+1:]:
            if not any((d["pr_a"]==int(na) and d["pr_b"]==int(nb)) or (d["pr_a"]==int(nb) and d["pr_b"]==int(na)) for d in cross_deps):
                cross_deps.append({"pr_a": int(na), "pr_b": int(nb), "reason": f"K8s module coordination: {pa} + {pb} must match versions", "merge_order": "merge together"})
    print(f"  K8s module group: {len(k8s_prs)} PRs need coordinated merge")
# OpenTelemetry coordination: go.opentelemetry.io/otel* modules share a release train and
# should move together so the resolved otel core version is consistent (otel trio #23/#27/#36).
otel_prs = [(num, pr["package"]) for num, pr in prs.items()
            if pr.get("package", "").startswith("go.opentelemetry.io/otel")]
if len(otel_prs) > 1:
    for i, (na, pa) in enumerate(otel_prs):
        for nb, pb in otel_prs[i+1:]:
            if not any((d["pr_a"]==int(na) and d["pr_b"]==int(nb)) or (d["pr_a"]==int(nb) and d["pr_b"]==int(na)) for d in cross_deps):
                cross_deps.append({"pr_a": int(na), "pr_b": int(nb), "reason": f"OpenTelemetry release-train coordination: {pa} + {pb} share the otel core version — merge together to keep the resolved otel version consistent", "merge_order": "merge together"})
    print(f"  OpenTelemetry module group: {len(otel_prs)} PRs need coordinated merge")
try:
    with open(os.environ.get("WORKSPACE_GRAPH_FILE", "/tmp/_bc_workspace_graph.json")) as f: graph = json.load(f)
    for num, pr in prs.items():
        pd = pr.get("pkg_dir", "/")
        if pd.startswith("lib/"):
            pkg_name = next((n for n, i in graph.get("packages",{}).items() if i["path"]==pd), None)
            if pkg_name:
                consumers = graph.get("consumers",{}).get(pkg_name, [])
                if not consumers:
                    for k, v in graph.get("consumers",{}).items():
                        if k.lower()==pkg_name.lower(): consumers=v; break
                for c in consumers:
                    for nb, pb in prs.items():
                        if nb!=num and pb.get("pkg_dir")==c["path"] and pb.get("package")==pr.get("package"):
                            if not any((d["pr_a"]==int(num) and d["pr_b"]==int(nb)) or (d["pr_a"]==int(nb) and d["pr_b"]==int(num)) for d in cross_deps):
                                cross_deps.append({"pr_a": int(num), "pr_b": int(nb), "reason": f"Shared lib cascade: {pkg_name} ({pd}) consumed by {c['service']}", "merge_order": f"lib first, then {c['path']}"})
    data["workspace_graph"] = graph
    data["nestjs_skew"] = graph.get("nestjs_skew", [])
except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
    data["workspace_graph"] = {}
    data["nestjs_skew"] = []
data["cross_pr_deps"] = cross_deps
with open("/tmp/build-results.json", "w") as f: json.dump(data, f, indent=2)
if cross_deps:
    for dep in cross_deps: print(f"  Found: PR #{dep['pr_a']} <-> #{dep['pr_b']} - {dep['reason']}")
else: print("  No cross-PR dependencies detected")
CROSSDEPS

# ── Step 5: Security posture scan ────────────────────────────────────────────
echo ""
echo "════════════ SECURITY POSTURE ════════════"
PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" OWNER_REPO="$OWNER_REPO" python3 << 'SECURITYEOF'
import json, subprocess, os
from cve_security_posture import build_cve_attribution

owner_repo = os.environ["OWNER_REPO"]

# Fetch Dependabot vulnerability alerts from GitHub API
try:
    result = subprocess.run(
        ["gh", "api", f"repos/{owner_repo}/dependabot/alerts",
         "--jq", '.[] | {number, state, security_advisory: {ghsa_id: .security_advisory.ghsa_id, cve_id: .security_advisory.cve_id, severity: .security_advisory.severity, summary: .security_advisory.summary}, security_vulnerability: {first_patched_version: .security_vulnerability.first_patched_version.identifier, vulnerable_version_range: .security_vulnerability.vulnerable_version_range}, dependency: {package: .dependency.package.name, ecosystem: .dependency.package.ecosystem, manifest_path: .dependency.manifest_path}}',
         "-X", "GET", "--paginate"],
        capture_output=True, text=True, timeout=60,
        env={**os.environ, **({"GH_TOKEN": os.environ["BREAKABILITY_PAT"], "GITHUB_TOKEN": os.environ["BREAKABILITY_PAT"]} if os.environ.get("BREAKABILITY_PAT") else {})}
    )
    if result.returncode != 0:
        print("  Could not fetch Dependabot alerts (may need security permissions)")
        alerts = []
        alerts_raw = None  # Distinguish auth failure from genuinely empty
    else:
        lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
        alerts = [json.loads(l) for l in lines]
        alerts_raw = json.dumps(alerts)
except Exception as e:
    print(f"  Security scan error: {e}")
    alerts = []
    alerts_raw = None

_alerts_unavailable = (alerts_raw is None)
try:
    alerts = json.loads(alerts_raw) if isinstance(alerts_raw, str) else (alerts if alerts_raw is None else [])
except (json.JSONDecodeError, TypeError, ValueError):
    alerts = []
    _alerts_unavailable = True

open_alerts = [a for a in alerts if a.get("state") == "open"]
severity_counts = {}
for a in open_alerts:
    sev = a.get("security_advisory", {}).get("severity", "unknown")
    severity_counts[sev] = severity_counts.get(sev, 0) + 1

with open("/tmp/build-results.json") as f:
    data = json.load(f)

prs = data.get("prs", {})
meta = data.get("metadata", {})
subset_requested = bool(meta.get("subset_requested"))
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

cve_fixes, orphan_alerts = build_cve_attribution(open_alerts, prs)

security_posture = {
    "scope": "subset" if subset_requested else "repository",
    "alert_counts_scope": "repository",
    "pr_rows_scope": "selected_prs" if subset_requested else "all_analyzed_prs",
    "total_open_alerts": len(open_alerts),
    "alerts_unavailable": _alerts_unavailable,
    "severity_counts": severity_counts,
    "total_cves_in_prs": total_cve_count,
    "prs_fixing_alerts": fixes_by_pr,
    "prs_with_cves": pr_cves,
    "alerts_fixable_by_merging": sum(f["alert_count"] for f in fixes_by_pr.values()),
    "cve_fixes": cve_fixes,
    "orphan_alerts": orphan_alerts,
}
if subset_requested:
    security_posture["subset_pr_numbers"] = meta.get("selected_pr_numbers", [])
    security_posture["subset_note"] = (
        "PR-scoped security rows are limited to selected PRs; repository-wide "
        "alert counts remain for context."
    )
    security_posture["orphan_alerts_omitted_for_subset"] = len(orphan_alerts)
    security_posture["omitted_due_to_subset"] = {"orphan_alerts": orphan_alerts} if orphan_alerts else {}
    security_posture["orphan_alerts"] = []

data["security_posture"] = security_posture

# V9.9 iter8: annotate each PR with CVEs it fixes (from Dependabot alert matching)
# so the per-PR comment poster can show "this PR fixes CVE-XXXX (CRITICAL)"
for fix in cve_fixes:
    pr_num = str(fix["pr"])
    if pr_num in prs:
        if "fixes_cves" not in prs[pr_num]:
            prs[pr_num]["fixes_cves"] = []
        prs[pr_num]["fixes_cves"].append({
            "cve_id": fix["cve_id"], "severity": fix["severity"],
            "first_patched_version": fix["first_patched_version"],
        })

# ── govulncheck aggregates (V9.7b): main baseline + per-PR new findings ─────
_govuln = {"main_baseline": {"status": "unknown", "findings": []},
           "prs_scanned": 0, "prs_with_new_vulns": 0, "total_new_findings": []}
# Reload any batch's main_baseline_vuln (they all scan the same main, just take first non-empty)
import glob as _glob
_batch_dir = os.environ.get("BATCH_RESULTS_DIR") or "/tmp/batch-results"
for _bf in sorted(_glob.glob(os.path.join(_batch_dir, "batch-*", "build-results-*.json"))):
    try:
        _bd = json.load(open(_bf))
        _mb = _bd.get("main_baseline_vuln")
        if _mb and _mb.get("status") != "unknown":
            _govuln["main_baseline"] = {"status": _mb.get("status", "unknown"),
                                         "findings": _mb.get("findings", [])}
            break
    except Exception:
        pass
# Aggregate per-PR vuln_new_findings from the merged prs dict
_new_set = set()
for _pn, _pr in data.get("prs", {}).items():
    _vs = _pr.get("vuln_status", "")
    if _vs in ("ok", "vulns_found", "ok_preexisting"):
        _govuln["prs_scanned"] += 1
    _new = _pr.get("vuln_new_findings", [])
    if _new:
        _govuln["prs_with_new_vulns"] += 1
        for _f in _new: _new_set.add(_f)
_govuln["total_new_findings"] = sorted(_new_set)
data["govulncheck"] = _govuln
print(f"  govulncheck: main_baseline={len(_govuln['main_baseline']['findings'])} CVE(s), prs_with_new_vulns={_govuln['prs_with_new_vulns']}, total_new={len(_govuln['total_new_findings'])}")

with open("/tmp/build-results.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"  Open vulnerability alerts: {len(open_alerts)}")
for sev, count in sorted(severity_counts.items(), key=lambda x: {'critical':0,'high':1,'medium':2,'low':3}.get(x[0],4)):
    print(f"    {sev}: {count}")
print(f"  PRs that fix known alerts: {len(fixes_by_pr)}")
print(f"  Alerts fixable by merging open PRs: {security_posture['alerts_fixable_by_merging']}")
if total_cve_count:
    print(f"  CVEs referenced in PR bodies: {total_cve_count}")
SECURITYEOF

# ── Step 5b: Merge-risk taxonomy on merged results ────────────────────────────
python3 << 'RISKEOF'
import json

with open("/tmp/build-results.json") as f:
    data = json.load(f)

def normalize(pr):
    existing = pr.get("merge_risk") or (pr.get("deterministic") or {}).get("merge_risk") or {}
    return {
        "tag": existing.get("tag") or "Medium",
        "reason": existing.get("reason") or "change evidence is limited; default caution",
        "evidenceAxis": existing.get("evidenceAxis") or "limited evidence",
        "buildVerificationAxis": existing.get("buildVerificationAxis") or existing.get("confidenceAxis") or pr.get("verification_label") or "unverified",
        "confidenceAxis": existing.get("confidenceAxis") or existing.get("buildVerificationAxis") or pr.get("verification_label") or "unverified",
    }

counts = {"Low": 0, "Medium": 0, "High": 0}
for pr in data.get("prs", {}).values():
    risk = normalize(pr)
    pr["merge_risk"] = risk
    counts[risk["tag"]] = counts.get(risk["tag"], 0) + 1

with open("/tmp/build-results.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"  Merge Risk: High={counts.get('High', 0)} Medium={counts.get('Medium', 0)} Low={counts.get('Low', 0)}")
RISKEOF

# ── Step 6: Comment cleanup ──────────────────────────────────────────────────
# CR4-9: Comment cleanup is now handled per-PR atomically in post-fallback-comments.sh
# (lines 73-79). Doing it here too causes duplicate API calls (2 GETs per PR)
# that accomplish nothing because the comments are either already deleted or will
# be deleted by post-fallback-comments.sh anyway.
echo ""
echo "════════════ COMMENT CLEANUP ════════════"
echo "  Skipped — per-PR atomic cleanup is handled by post-fallback-comments.sh"

# ── Summary ──────────────────────────────────────────────────────────────────
TOTAL_PRS=$(python3 -c "import json; print(len(json.load(open('/tmp/build-results.json')).get('prs', {})))")
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  MERGE COMPLETE"
echo "  Results: $RESULTS_FILE"
echo "  Total PRs: $TOTAL_PRS"
echo "  Diffs: /tmp/pr-{N}.diff"
echo "═══════════════════════════════════════════════════════════════════"
