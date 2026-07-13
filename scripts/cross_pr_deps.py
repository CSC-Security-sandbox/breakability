#!/usr/bin/env python3
"""Cross-PR dependency detection.

Reads build-results.json and identifies coordinated upgrade groups
(K8s, OTel, NestJS, React, etc.). Writes cross_pr_deps back to the
results file.

Called by build-check.sh after the per-PR loop completes.
"""
import json, os, sys

results_file = os.environ["RESULTS_FILE"]

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
    ("k8s.io/client-go", "k8s.io/apimachinery"): ("K8s module coordination: k8s.io/apimachinery + k8s.io/client-go must match versions", "merge together"),
    ("k8s.io/client-go", "k8s.io/api"): ("K8s module coordination: k8s.io/api + k8s.io/client-go must match versions", "merge together"),
    ("k8s.io/apimachinery", "k8s.io/api"): ("K8s module coordination: k8s.io/apimachinery + k8s.io/api must match versions", "merge together"),
    ("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/sdk"): ("OTel coordination: core + SDK should match", "merge together"),
    ("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/trace"): ("OTel coordination: core + trace should match", "merge together"),
    ("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/metric"): ("OTel coordination: core + metric should match", "merge together"),
    ("go.opentelemetry.io/otel/sdk", "go.opentelemetry.io/otel/trace"): ("OTel coordination: SDK + trace should match", "merge together"),
    ("go.opentelemetry.io/otel/sdk", "go.opentelemetry.io/otel/metric"): ("OTel coordination: SDK + metric should match", "merge together"),
    ("go.opentelemetry.io/otel/trace", "go.opentelemetry.io/otel/metric"): ("OTel coordination: trace + metric should match", "merge together"),
}
try:
    with open("/tmp/_bc_peer_groups.json") as f: pd = json.load(f)
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
except FileNotFoundError:
    pass
except json.JSONDecodeError as e:
    print(f"WARNING: corrupt peer groups JSON: {e}", file=sys.stderr)

with open(results_file) as f: data = json.load(f)
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
try:
    with open("/tmp/_bc_workspace_graph.json") as f: graph = json.load(f)
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
_tmp = results_file + ".tmp"
with open(_tmp, "w") as f: json.dump(data, f, indent=2)
os.rename(_tmp, results_file)
if cross_deps:
    for dep in cross_deps: print(f"  Found: PR #{dep['pr_a']} <-> #{dep['pr_b']} - {dep['reason']}")
else: print("  No cross-PR dependencies detected")
