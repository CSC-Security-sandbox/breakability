#!/usr/bin/env python3
"""True-e2e disposition + accuracy evaluator over ALL open PRs.

Reads a build-results artifact produced by run-local.sh (--to policy) and emits,
per PR, the authoritative verdict (auto_clear / review / fix) via the SAME single
source the developer sees (verdict_contract.prediction_for_pr). For PRs that are in
the labeled ground-truth corpus it grades the prediction against expected_label and,
critically, counts FALSE-GREEN (predicted auto_clear when truth is review/fix) which
is the hard zero-tolerance invariant. Unlabeled PRs are reported (verdict + evidence)
but never counted as "accuracy"; they are spot-checked for false-green only.

Usage: python eval_all34.py /tmp/build-results-all34.json [corpus.json]
Exit 0 iff zero false-greens on the labeled set.
"""
import json
import os
import sys

HARNESS = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HARNESS, "..", "..", "scripts"))
sys.path.insert(0, SCRIPTS)
from verdict_contract import prediction_for_pr  # noqa: E402

EXPECTED_TO_PRED = {"true_safe": "auto_clear", "true_review": "review", "true_fix": "fix"}


def load_prs(path):
    d = json.load(open(path))
    if isinstance(d, dict) and "prs" in d:
        d = d["prs"]
    if isinstance(d, dict):
        items = []
        for k, v in d.items():
            if isinstance(v, dict):
                v.setdefault("pr", k)
                items.append(v)
        return items
    return d


def load_labels(corpus_path):
    try:
        c = json.load(open(corpus_path))
    except Exception:
        return {}
    cases = c.get("cases", c) if isinstance(c, dict) else c
    out = {}
    for case in cases:
        pid = str(case.get("pr_id") or case.get("pr") or "")
        if pid:
            out[pid] = case.get("expected_label", "")
    return out


def pr_id(pr):
    return str(pr.get("pr") or pr.get("number") or pr.get("pr_number") or "?")


def reach_summary(pr):
    det = pr.get("deterministic") or {}
    r = det.get("reach") or {}
    if r:
        bits = []
        if r.get("any_direct_in_module"):
            bits.append("direct")
        if r.get("any_transitively_reachable"):
            bits.append("transitive")
        return ",".join(bits) or "not-reached"
    dbr = pr.get("declared_break_reachability") or {}
    if dbr:
        return "dbr:" + str(dbr.get("reachability_kind", "?"))
    return "-"


def main():
    art = sys.argv[1] if len(sys.argv) > 1 else "/tmp/build-results-all34.json"
    corpus = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HARNESS, "corpus.json")
    prs = load_prs(art)
    labels = load_labels(corpus)

    rows = []
    for pr in prs:
        pid = pr_id(pr)
        try:
            pred = prediction_for_pr(pr)
        except Exception as e:
            pred = f"ERR:{str(e)[:30]}"
        build = (pr.get("build") or {}).get("verdict", "-")
        mr = (pr.get("merge_risk") or {})
        tag = mr.get("tag", "-")
        eco = pr.get("ecosystem", "-")
        bump = pr.get("bump") or (pr.get("from", "?") + "->" + pr.get("to", "?"))
        expected = labels.get(pid, "")
        exp_pred = EXPECTED_TO_PRED.get(expected, "")
        grade = ""
        if expected:
            if pred == exp_pred:
                grade = "OK"
            elif pred == "auto_clear" and exp_pred in ("review", "fix"):
                grade = "FALSE_GREEN"
            elif pred == "review" and exp_pred == "auto_clear":
                grade = "over_review"
            elif pred == "fix" and exp_pred != "fix":
                grade = "over_fix"
            else:
                grade = "miss"
        rows.append(dict(pid=pid, eco=eco, bump=str(bump)[:42], build=build,
                         tag=tag, reach=reach_summary(pr), pred=pred,
                         expected=expected or "(unlabeled)", grade=grade))

    rows.sort(key=lambda r: int(r["pid"]) if r["pid"].isdigit() else 999)

    print(f"{'PR':>4} {'eco':<14} {'build':<5} {'risk':<7} {'reach':<16} "
          f"{'verdict':<11} {'expected':<12} grade")
    print("-" * 92)
    fg = 0
    labeled = ok = 0
    for r in rows:
        if r["grade"] == "FALSE_GREEN":
            fg += 1
        if r["expected"] != "(unlabeled)":
            labeled += 1
            if r["grade"] == "OK":
                ok += 1
        print(f"{r['pid']:>4} {r['eco']:<14} {r['build']:<5} {r['tag']:<7} "
              f"{r['reach']:<16} {r['pred']:<11} {r['expected']:<12} {r['grade']}")

    print("-" * 92)
    auto = sum(1 for r in rows if r["pred"] == "auto_clear")
    rev = sum(1 for r in rows if r["pred"] == "review")
    fix = sum(1 for r in rows if r["pred"] == "fix")
    n = len(rows)
    print(f"TOTAL {n} PRs | auto_clear={auto} review={rev} fix={fix} "
          f"| auto-clear rate={auto/n*100:.0f}%")
    print(f"LABELED {labeled} | correct={ok} "
          f"({(ok/labeled*100) if labeled else 0:.0f}%) | FALSE_GREEN={fg}")
    if fg:
        print("\n*** HARD INVARIANT VIOLATED: false-green present ***")
        return 1
    print("\nFALSE_GREEN=0 (hard invariant holds)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
