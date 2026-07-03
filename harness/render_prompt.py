#!/usr/bin/env python3
"""Render the per-PR AI adjudicator prompt from build-results.json.

Usage: render_prompt.py <build-results.json> <pr_id> <prompt_template.md>
Prints the rendered prompt to stdout (ready to pipe into the `agent` CLI).
Substitutes the {{...}} fields the template declares, scoped to the bumped module.
"""
import json
import sys


def module_dir(pr):
    d = (pr.get("pkg_dir") or "/").strip()
    return "" if d in ("/", ".", "") else d.strip("/")


def in_module(fp, mod):
    f = fp.lstrip("./")
    if mod == "":
        return not (f.startswith("cicd/") or f.startswith("automations/"))
    return f.startswith(mod + "/") or f == mod


def main():
    results_path, pr_id, tmpl_path = sys.argv[1], sys.argv[2], sys.argv[3]
    prs = json.load(open(results_path)).get("prs", {})
    pr = prs.get(str(pr_id))
    if not pr:
        sys.exit(1)
    det = pr.get("deterministic") or {}
    mod = module_dir(pr)

    bullets = "\n".join(
        f"- {b}" for b in ((det.get("changelogSignal") or {}).get("bullets") or [])
    ) or "(none recorded)"

    apidiff = "\n".join(
        f"- {c.get('symbol')}: {c.get('changeType')} "
        f"({'HARD' if c.get('isHardBreak') else 'soft'})"
        for c in (det.get("api_changes_detail") or [])
    ) or "(none)"

    in_mod_sites = [
        f"- {u.get('file')}:{u.get('line')} {u.get('symbol')} ({u.get('context')})"
        for u in (det.get("usages") or [])
        if in_module(u.get("file", ""), mod)
    ]
    sites = "\n".join(in_mod_sites) or "(no recorded usages inside the bumped module)"

    # ALL deterministic-claimed sites (in- AND out-of-module), tagged so the AI can audit the
    # cross-module miscount that produces false positives like #38.
    all_claimed = [
        f"- {u.get('file')}:{u.get('line')} {u.get('symbol')} "
        f"[{'IN bumped module' if in_module(u.get('file', ''), mod) else 'OUTSIDE bumped module — different go.mod'}]"
        for u in (det.get("usages") or [])
    ]
    claimed = "\n".join(all_claimed) or "(deterministic recorded no symbol usages)"

    v2 = pr.get("verdict_v2") or {}
    det_verdict = (v2.get("verdict") or "?")
    det_reason = ((v2.get("residual") or {}).get("check") or v2.get("reason") or "?")

    sub = {
        "pr_id": str(pr_id),
        "package": pr.get("package", "?"),
        "module": mod if mod else ".",
        "from": pr.get("from", "?"),
        "to": pr.get("to", "?"),
        "bump": pr.get("bump", "?"),
        "changelog_breaking_bullets": bullets,
        "apidiff_symbols": apidiff,
        "our_call_sites_in_bumped_module": sites,
        "det_verdict": det_verdict,
        "det_reason": det_reason,
        "det_claimed_sites": claimed,
        "build_verdict": (pr.get("build") or {}).get("verdict", "?"),
        "test_verdict": (pr.get("test") or {}).get("verdict", "n/a"),
    }
    text = open(tmpl_path).read()
    for k, v in sub.items():
        text = text.replace("{{" + k + "}}", str(v))
    print(text)


if __name__ == "__main__":
    main()
