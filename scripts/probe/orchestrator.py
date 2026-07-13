"""Main orchestration loop for the differential probe."""
import json
import os
import shutil
import sys
import tempfile
import time

from break_class_router import classify_break, NOT_OBSERVABLE, AMBIGUOUS, CALL_OBSERVABLE
from cross_pr_reconciler import reconcile_release_train_grades

from .config import (
    RESULTS, PROMPT_FILE, REASON_PROMPT_FILE, AGENT_CMD,
    MAX_PRS, MAX_REASON, MAX_BULLETS, MAX_USAGES,
    PROBE_TIMEOUT, REASON_TIMEOUT,
    PROMPT_VERSION, REASON_PROMPT_VERSION,
    DETERMINISTIC_ONLY,
)
from .utils import log, is_residual, clean_bullets
from .evidence import first_prod_site, affected_files, our_usages, targeted_note
from .grading import build_grade_from_contract, build_reasoning_grade
from .sandbox import run_agent
from .npm_probe import is_npm_probe_candidate, run_npm_differential_probe
from .gomod_probe import is_gomod_probe_candidate, run_gomod_differential_probe
from .cache import cache_key, cache_get, cache_put

__all__ = [
    "grade_residual", "run_reasoning", "main",
    # Internal names exported for backward compatibility
    "_grade_residual_inner",
]


# ── main ────────────────────────────────────────────────────────────────────
def grade_residual(num, pr, budgets):
    """Return a committed behavioral_grade dict for one residual PR (never None).

    budgets = {"probe": int, "reason": int} -- remaining AI-call allowances. The
    returned dict may carry a private "_ai_kind" ("probe"/"reason") + "_ai_attempted"
    so main() can decrement the right budget.

    The returned dict always includes "call_site_import_path" (the import path of the
    package at the selected call site) so the cross-PR reconciler can detect
    package-mismatch grades (e.g. a trace PR whose site resolved to a prometheus
    exporter because that entry appeared first in the evidence list).
    """
    g = _grade_residual_inner(num, pr, budgets)
    site = first_prod_site(pr)
    if isinstance(g, dict) and site:
        g.setdefault("call_site_import_path", site.get("import_path") or "")
        if site.get("_package_mismatch"):
            g.setdefault("_call_site_package_mismatch", True)
    return g


def _grade_residual_inner(num, pr, budgets):
    if is_npm_probe_candidate(pr):
        log(f"PR {num}: deterministic npm runtime-shape probe for {pr.get('package')} "
            f"{pr.get('from')}->{pr.get('to')}")
        return run_npm_differential_probe(num, pr)

    if is_gomod_probe_candidate(pr):
        log(f"PR {num}: deterministic Go API-surface probe for {pr.get('package')} "
            f"{pr.get('from')}->{pr.get('to')}")
        return run_gomod_differential_probe(num, pr)

    det = pr.get("deterministic") or {}
    sig = det.get("changelogSignal") or {}
    # Classify over ALL bullets before truncating prompt payload. A not-observable
    # runtime/default/config bullet hidden after MAX_BULLETS must still veto probing;
    # otherwise earlier call-observable bullets can create a false-green probe.
    all_bullets = clean_bullets(sig.get("bullets") or [])
    bullets = all_bullets[:MAX_BULLETS]
    site = first_prod_site(pr)
    router = classify_break(all_bullets)

    if not all_bullets or not site:
        # No usable scope -> honest Medium (still committed, no punt).
        return {
            "grade": "medium", "source": "insufficient_context",
            "behavior_changed": "unverified",
            "rationale": "declared behavioral break is import-reachable but lacks a precise "
                         "call site/changelog bullet to verify.",
            "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"],
        }

    if not router["probe_recommended"]:
        # NOT_OBSERVABLE / AMBIGUOUS: a probe would be structurally blind (false-green
        # risk). Instead, reason over the release notes + our usage like a senior dev
        # would -- a graded, cited verdict, NOT a shrug-Medium. Falls back to the
        # honest targeted note if the reasoning budget is spent (no AI attempt).
        bullet = router["observable_bullet"] or bullets[0]
        if budgets.get("reason", 0) > 0:
            return run_reasoning(num, pr, site, router, bullets)
        note = targeted_note(pr, bullet, site, router)
        note["router_class"] = router["class"]
        log(f"PR {num}: router={router['class']} -> Medium note (reason budget spent; {router['reason']})")
        return note

    if budgets.get("probe", 0) <= 0:
        loc = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
        return {
            "grade": "medium", "source": "budget_exhausted",
            "behavior_changed": "unverified",
            "rationale": "probe budget exhausted for this run; committed at Medium.",
            "guidance": f"Affected package used at {loc}." if loc else "",
            "call_site": loc, "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"],
        }

    # call-observable -> probe (with cache)
    ctx = {
        "pr": str(num), "kind": "probe", "prompt_version": PROMPT_VERSION,
        "package": pr.get("package", ""), "ecosystem": pr.get("ecosystem", ""),
        "from": pr.get("from", ""), "to": pr.get("to", ""),
        "bullet": router["observable_bullet"], "dimension_hint": router.get("markers", []),
        "call_site": site,
    }
    key = cache_key(ctx)
    contract = cache_get(key)
    cached = contract is not None
    ai_attempted = False
    if not cached:
        ai_attempted = True  # a cache miss reaches run_agent -> counts against the budget
        workdir = tempfile.mkdtemp(prefix=f"dp-{num}-")
        try:
            log(f"PR {num}: router=call_observable -> probing {ctx['package']} {ctx['from']}->{ctx['to']}")
            contract = run_agent(ctx, workdir, PROMPT_FILE, PROBE_TIMEOUT)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if contract is not None:
            cache_put(key, contract)

    if contract is None:
        # Probe failed -> committed Medium floor (no false-green).
        loc = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
        return {
            "grade": "medium", "source": "probe_failed",
            "behavior_changed": "unverified",
            "rationale": f"{ctx['bullet'][:200]} — probe could not be built/run; committed at Medium.",
            "guidance": f"Affected package used at {loc}." if loc else "",
            "call_site": loc, "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"], "_ai_kind": "probe", "_ai_attempted": ai_attempted,
        }

    try:
        source_ctx = {
            "bullet": ctx.get("bullet", ""),
            "changelog_text": str((pr.get("deterministic") or {}).get("changelogText", "")),
            "call_site": site,
        }
        g = build_grade_from_contract(contract, source_ctx)
    except Exception as e:
        # Agent already ran (budget consumed); a malformed contract must NOT escape
        # un-counted or crash the loop. Commit Medium, preserve the budget flag.
        loc = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
        log(f"PR {num}: contract parse failed ({e}); committing Medium")
        return {
            "grade": "medium", "source": "probe_contract_invalid",
            "behavior_changed": "unverified",
            "rationale": f"{ctx['bullet'][:200]} — probe ran but returned an unusable proof contract; committed at Medium.",
            "guidance": f"Affected package used at {loc}." if loc else "",
            "call_site": loc, "confidence": "low", "generated_at": int(time.time()),
            "router_class": router["class"], "_ai_kind": "probe", "_ai_attempted": ai_attempted,
        }
    g["router_class"] = router["class"]
    g["cached"] = cached
    g["_ai_kind"] = "probe"
    g["_ai_attempted"] = ai_attempted
    g["call_site"] = f"{site['file']}:{site['line']}" if site.get("line") else site.get("file", "")
    log(f"PR {num}: probe grade={g['grade']} ({g['rationale'][:80]})")
    return g


def run_reasoning(num, pr, site, router, bullets):
    """Release-notes + usage reasoning oracle for not-observable breaks. ALWAYS returns
    a committed grade dict (never None). On a cache MISS it consumes one reason-budget
    AI call (marked via _ai_kind/_ai_attempted); on failure it falls back to the honest
    Medium targeted note BUT keeps the budget marker so a flapping oracle can't burn
    unbounded calls."""
    det = pr.get("deterministic") or {}
    bullet = router["observable_bullet"] or bullets[0]
    ctx = {
        "pr": str(num), "kind": "reason", "prompt_version": REASON_PROMPT_VERSION,
        "package": pr.get("package", ""), "ecosystem": pr.get("ecosystem", ""),
        "from": pr.get("from", ""), "to": pr.get("to", ""),
        "bullet": bullet, "all_bullets": bullets,
        "changelog_text": str(det.get("changelogText", ""))[:4000],
        "dimension_hint": router.get("markers", []),
        "call_site": site, "our_usages": our_usages(pr),
    }
    key = cache_key(ctx)
    contract = cache_get(key)
    cached = contract is not None
    ai_attempted = not cached
    if not cached:
        workdir = tempfile.mkdtemp(prefix=f"dr-{num}-")
        try:
            log(f"PR {num}: router={router['class']} -> reasoning over release notes for "
                f"{ctx['package']} {ctx['from']}->{ctx['to']}")
            contract = run_agent(ctx, workdir, REASON_PROMPT_FILE, REASON_TIMEOUT)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        if contract is not None:
            cache_put(key, contract)

    if contract is None:
        # Oracle failed -> honest Medium note, but the AI attempt still counts.
        note = targeted_note(pr, bullet, site, router)
        note["router_class"] = router["class"]
        note["source"] = "reasoning_failed"
        note["_ai_kind"] = "reason"
        note["_ai_attempted"] = ai_attempted
        log(f"PR {num}: reasoning oracle produced no contract; committed Medium note")
        return note
    try:
        source_ctx = {
            "bullet": ctx.get("bullet", ""),
            "changelog_text": ctx.get("changelog_text", ""),
            "call_site": ctx.get("call_site"),
        }
        g = build_reasoning_grade(contract, site, router, source_ctx)
    except Exception as e:
        log(f"PR {num}: reasoning contract invalid ({e}); falling back to Medium note")
        g = targeted_note(pr, bullet, site, router)
        g["router_class"] = router["class"]
        g["source"] = "reasoning_invalid"
    g["cached"] = cached
    g["_ai_kind"] = "reason"
    g["_ai_attempted"] = ai_attempted
    log(f"PR {num}: reasoning grade={g['grade']} ({g.get('rationale','')[:80]})")
    return g


def main():
    if not os.path.isfile(RESULTS):
        log(f"no results file at {RESULTS}; nothing to do")
        return 0
    try:
        data = json.load(open(RESULTS))
    except Exception as e:
        log(f"cannot parse {RESULTS}: {e}")
        return 0
    prs = data.get("prs") or {}
    if DETERMINISTIC_ONLY:
        candidates = [
            (n, pr) for n, pr in prs.items()
            if isinstance(pr, dict) and (is_npm_probe_candidate(pr) or is_gomod_probe_candidate(pr))
        ]
        if not candidates:
            log("deterministic-only mode: no npm/gomod probe candidates; nothing to grade")
            return 0
    else:
        candidates = [
            (n, pr) for n, pr in prs.items()
            if isinstance(pr, dict) and (is_residual(pr) or is_npm_probe_candidate(pr) or is_gomod_probe_candidate(pr))
        ]
        if not candidates:
            log("no declared-behavioral residual or npm/gomod probe candidate PRs; nothing to grade")
            return 0
    probe_budget = MAX_PRS
    reason_budget = MAX_REASON
    annotated = 0

    def _persist():
        # Atomic: write to a temp file in the same dir, then os.replace() over RESULTS so a
        # mid-loop kill can never leave a truncated/corrupt artifact for downstream steps.
        try:
            d = os.path.dirname(os.path.abspath(RESULTS)) or "."
            fd, tmp = tempfile.mkstemp(prefix=".dp-results-", dir=d)
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f)
                os.replace(tmp, RESULTS)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return True
        except Exception as e:
            log(f"failed to write {RESULTS}: {e}")
            return False

    for num, pr in candidates:
        try:
            g = grade_residual(num, pr, {"probe": probe_budget, "reason": reason_budget})
            kind = g.pop("_ai_kind", None)
            if g.pop("_ai_attempted", False):
                if kind == "probe":
                    probe_budget -= 1
                elif kind == "reason":
                    reason_budget -= 1
            pr["behavioral_grade"] = g
            annotated += 1
        except Exception as e:
            log(f"PR {num}: grading failed ({e}); committing Medium")
            pr["behavioral_grade"] = {
                "grade": "medium", "source": "error", "behavior_changed": "unverified",
                "rationale": "behavioral grading failed; committed at Medium.",
                "confidence": "low", "generated_at": int(time.time()),
            }
            annotated += 1
        # Persist after EACH PR so a mid-loop timeout/kill (budgets can exceed the step
        # timeout) does not discard grades already committed.
        _persist()

    # ── Cross-PR reconciliation: detect package-mismatch and grade inconsistency ──
    # Run AFTER all PRs are graded so every behavioral_grade is populated.
    # This catches the "trace PR reasoning about a prometheus exporter callsite" class
    # of bugs and flags same-evidence -> different-grade inconsistencies within
    # release-train groups (otel #23/#27/#36, k8s modules, etc.).
    try:
        reconcile_notes = reconcile_release_train_grades(prs, data.get("cross_pr_deps") or [])
        if reconcile_notes:
            log(f"cross-PR reconciliation flagged {len(reconcile_notes)} PR(s)")
            for num, note in reconcile_notes.items():
                if num in prs and isinstance(prs[num].get("behavioral_grade"), dict):
                    prs[num]["behavioral_grade"]["reconciliation_note"] = note
                    log(f"  PR {num}: {note[:120]}")
            _persist()
    except Exception as e:
        log(f"cross-PR reconciliation failed (non-fatal): {e}")

    if annotated:
        if _persist():
            log(f"committed behavioral grades for {annotated} residual PR(s); wrote {RESULTS}")
    return 0
