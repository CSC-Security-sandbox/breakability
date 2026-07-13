"""Discover peer dependency groups (NestJS, React) by scanning node_modules package.json files.

Reads REPO_ROOT from the environment (defaults to ".") and writes the discovered
peer-dependency graph to /tmp/_bc_peer_groups.json.  Summary counts are printed
to stdout for the calling build-check harness.
"""

import json, os, glob

peer_groups = {}
for pj_path in glob.glob(os.path.join(os.environ.get("REPO_ROOT", "."), "**/package.json"), recursive=True):
    if "node_modules" not in pj_path: continue
    try:
        with open(pj_path) as f: data = json.load(f)
    except: continue
    name = data.get("name", "")
    peers = data.get("peerDependencies", {})
    if name and peers: peer_groups[name] = list(peers.keys())

nestjs_group = set()
for pkg, peers in peer_groups.items():
    if pkg.startswith("@nestjs/"):
        nestjs_group.add(pkg)
        nestjs_group.update(p for p in peers if p.startswith("@nestjs/"))

react_group = set()
for pkg, peers in peer_groups.items():
    if "react" in pkg.lower():
        react_group.add(pkg)
        react_group.update(p for p in peers if "react" in p.lower())

result = {"peer_groups": peer_groups, "nestjs_group": sorted(nestjs_group), "react_group": sorted(react_group)}
with open("/tmp/_bc_peer_groups.json", "w") as f: json.dump(result, f, indent=2)

if nestjs_group: print(f"  NestJS peer group: {len(nestjs_group)} packages")
if react_group: print(f"  React peer group: {len(react_group)} packages")
print(f"  Total packages with peer deps: {len(peer_groups)}")
