#!/usr/bin/env python3
"""Module-scoped symbol-usage resolver — the CHEAP replacement for a callgraph.

Whole-program callgraph was dropped (too compute-heavy). This resolves the same residue
deterministically in microseconds by filtering the usage evidence the deterministic layer
ALREADY computed (`deterministic.usages`) down to the module the PR actually bumps (`pkg_dir`).

Why this works: the #38 false-positive is NOT a missing-callgraph problem, it is a
cross-module ATTRIBUTION bug. The tool bumped lib/pq in `automations/tstctl` but counted
`Error.Code` usages in the ROOT module (`database/...`). Those usages cannot be reached by a
bump scoped to another go.mod. A path-prefix filter removes them — same verdict a callgraph
gives (UNREACHABLE), at ~0 cost and O(#usages), not O(codebase).

Verdict per PR:
  USES_IN_MODULE  -> changed symbol genuinely used in the bumped module (cite file:line) -> REVIEW
  NOT_USED_IN_MODULE -> claimed usages are all out-of-module/test -> downgrade REVIEW->auto_clear
  NO_SYMBOL       -> no changed symbol named; nothing to scope -> leave as-is (defer to other tiers)
"""
import json
import os
import sys


def module_dir(pr):
    d = (pr.get("pkg_dir") or "/").strip()
    return "" if d in ("/", ".", "") else d.strip("/")


def in_module(file_path, mod):
    f = file_path.lstrip("./")
    if mod == "":
        # root module: a file belongs to root iff it is NOT inside a nested module dir.
        return not (f.startswith("cicd/") or f.startswith("automations/"))
    return f.startswith(mod + "/") or f == mod


def scope_usages(pr):
    det = pr.get("deterministic") or {}
    usages = det.get("usages") or []
    mod = module_dir(pr)
    in_mod_prod, in_mod_test, out_mod = [], [], []
    for u in usages:
        fp = u.get("file", "")
        rec = (fp, u.get("line"), u.get("symbol"), u.get("context"))
        if not in_module(fp, mod):
            out_mod.append(rec)
        elif u.get("context") == "production":
            in_mod_prod.append(rec)
        else:
            in_mod_test.append(rec)
    return mod, in_mod_prod, in_mod_test, out_mod


def verdict(pr):
    det = pr.get("deterministic") or {}
    usages = det.get("usages") or []
    if not usages:
        return "NO_SYMBOL", {}
    mod, prod, test, out = scope_usages(pr)
    info = {"module": mod or "(root)", "in_module_prod": prod,
            "in_module_test": test, "out_of_module": out}
    if prod:
        return "USES_IN_MODULE", info
    if test and not out:
        return "TEST_ONLY_IN_MODULE", info
    # all usages are outside the bumped module (the #38 attribution bug) or test-only elsewhere
    return "NOT_USED_IN_MODULE", info


def main():
    results = json.load(open(sys.argv[1]))
    prs = results.get("prs", {})
    ids = sys.argv[2:] or sorted(prs, key=lambda x: int(x))
    for pid in ids:
        pr = prs.get(pid)
        if not pr:
            print(f"PR#{pid}: (not in results)")
            continue
        v, info = verdict(pr)
        print(f"PR#{pid} {pr.get('package')}  bump-module={info.get('module','?')}  -> {v}")
        if info.get("in_module_prod"):
            for f, l, s, c in info["in_module_prod"][:3]:
                print(f"    proof: {f}:{l} uses {s} ({c})")
        if info.get("out_of_module"):
            for f, l, s, c in info["out_of_module"][:3]:
                print(f"    rejected (out-of-module): {f}:{l} {s} ({c})")
        if v == "NOT_USED_IN_MODULE":
            print("    => deterministic downgrade REVIEW->auto_clear (no callgraph needed)")
        elif v == "USES_IN_MODULE":
            print("    => stays REVIEW, now backed by a real in-module call site")


if __name__ == "__main__":
    main()
