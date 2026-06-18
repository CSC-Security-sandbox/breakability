#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# post-fallback-comments.sh — Deterministic build analysis comments + merge plan
#
# PRIMARY comment system: posts structured build analysis comments for every PR
# and generates a merge plan issue. Comments are generated directly from the
# deterministic JSON results — no AI required. If the AI agent already posted
# richer comments (<!-- breakability-agent -->), those PRs are skipped.
# CR5-1/M3: This is the primary system, not a fallback. The AI agent is optional.
#
# Comment quality by scenario:
#   - Trivially safe (patch/transitive/actions/docker): brief SAFE one-liner
#   - Build pass with new errors or major bump: BUILD ANALYSIS with details
#   - Build fail: BUILD_FAILS with error excerpt and remediation note
#   - Build not run (skip/infra_error): REVIEW with infrastructure context
#   - Security fix: SECURITY FIX with severity and MERGE NOW recommendation
#   - Pre-existing (L1, zero new errors): LIKELY SAFE
# ──────────────────────────────────────────────────────────────────────────────
set -u
export LC_ALL=en_US.UTF-8
unset GH_TOKEN

# ── Local dry-run mode ────────────────────────────────────────────────────────
# When DRY_RUN=1, never post or delete anything on GitHub. Instead render each
# PR's comment body to $DRY_RUN_DIR/pr-<N>.md so we can iterate on the comment
# content locally in seconds (see .github/scripts/run-local.sh). Destructive gh
# calls are routed through these guards.
DRY_RUN="${DRY_RUN:-0}"
DRY_RUN_DIR="${DRY_RUN_DIR:-/tmp/breakability-local/comments}"
if [[ "$DRY_RUN" == "1" ]]; then
  mkdir -p "$DRY_RUN_DIR"
fi
gh_pr_comment() {  # gh_pr_comment <pr> <body>
  local pr="$1" body="$2"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '%s\n' "$body" > "$DRY_RUN_DIR/pr-${pr}.md"
    echo "  [dry-run] wrote comment -> $DRY_RUN_DIR/pr-${pr}.md"
    return 0
  fi
  gh pr comment "$pr" --body "$body" 2>/dev/null
}
gh_delete_comment() {  # gh_delete_comment <owner> <repo> <comment_id>
  if [[ "$DRY_RUN" == "1" ]]; then return 0; fi
  gh api -X DELETE "repos/$1/$2/issues/comments/$3" 2>/dev/null || true
}

RESULTS_FILE="/tmp/build-results.json"
CLI_PATH="${CLI_PATH:-.github/actions/breakability-check/index.js}"

if [[ ! -f "$RESULTS_FILE" ]]; then
  echo "No build-results.json found — nothing to do"
  exit 0
fi

OWNER_REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null || echo "unknown/unknown")
OWNER="${OWNER_REPO%%/*}"
REPO="${OWNER_REPO##*/}"

POSTED=0
SKIPPED=0
FAILED=0

# Read advisory mode from results metadata
BC_MODE=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
print(data.get('metadata', {}).get('mode', 'advisory'))
" 2>/dev/null || echo "advisory")

ADVISORY_FOOTER=""
if [[ "$BC_MODE" == "advisory" ]]; then
  ADVISORY_FOOTER="
> 🔬 **Advisory mode** — This analysis is informational. No merges are blocked."
fi

# EU-18: Actions run link for verifiability
RUN_LINK=""
if [[ -n "${GITHUB_RUN_ID:-}" ]]; then
  RUN_LINK="
🔗 [View analysis run](${GITHUB_SERVER_URL:-https://github.com}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID})"
fi

echo "Posting deterministic analysis comments (mode: $BC_MODE)..."

# V8 FIX (C2): Detect discovered-but-not-analyzed PRs (cancelled batch timeout).
# Compare the discover list (all open Dependabot PRs) against the build-results.json.
# Post a "Skipped — batch was cancelled" comment for missing PRs.
echo "Checking for cancelled/missing PRs..."
DISCOVERED_PRS=$(gh pr list --label "dependencies" --state open \
  --json number --jq '.[].number' --limit 500 2>/dev/null | sort -n || echo "")
ANALYZED_PRS=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
for num in sorted(data.get('prs', {}).keys(), key=int):
    print(num)
" 2>/dev/null || echo "")
REQUESTED_SUBSET_PRS=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
meta = data.get('metadata', {})
if meta.get('subset_requested'):
    for num in meta.get('requested_pr_numbers', []):
        print(num)
" 2>/dev/null || echo "")

if [[ -n "$REQUESTED_SUBSET_PRS" ]]; then
  _FILTERED_DISCOVERED=""
  for _disc_pr in $DISCOVERED_PRS; do
    for _req_pr in $REQUESTED_SUBSET_PRS; do
      if [[ "$_disc_pr" == "$_req_pr" ]]; then
        _FILTERED_DISCOVERED="$_FILTERED_DISCOVERED $_disc_pr"
        break
      fi
    done
  done
  DISCOVERED_PRS="$_FILTERED_DISCOVERED"
  echo "Subset run: missing-PR check limited to requested PRs: $(echo "$REQUESTED_SUBSET_PRS" | tr '\n' ',' | sed 's/,$//')"
fi

# Find PRs in discovered list but NOT in analyzed results
CANCELLED_PRS=""
for _disc_pr in $DISCOVERED_PRS; do
  _found=false
  for _anal_pr in $ANALYZED_PRS; do
    if [[ "$_disc_pr" == "$_anal_pr" ]]; then
      _found=true
      break
    fi
  done
  if [[ "$_found" == "false" ]]; then
    CANCELLED_PRS="$CANCELLED_PRS $_disc_pr"
  fi
done

# Post "Skipped" comments for cancelled PRs and add them to results JSON
for _CANCEL_PR in $CANCELLED_PRS; do
  [[ -z "$_CANCEL_PR" ]] && continue
  # Check if we already have a recent comment — don't spam
  _HAS_RECENT=$(gh api "repos/$OWNER/$REPO/issues/$_CANCEL_PR/comments" \
    --jq '[.[] | select(.body | contains("<!-- breakability-check -->")) | select(.body | contains("batch was cancelled"))] | length' \
    2>/dev/null || echo "0")
  if [[ "$_HAS_RECENT" -gt 0 ]]; then
    continue
  fi
  # Delete old deterministic comments before posting new one
  _OLD_IDS=$(gh api "repos/$OWNER/$REPO/issues/$_CANCEL_PR/comments" \
    --jq '.[] | select(.body | contains("<!-- breakability-check -->")) | select(.body | contains("<!-- breakability-agent -->") | not) | .id' \
    2>/dev/null || true)
  for _CID in $_OLD_IDS; do
    gh_delete_comment "$OWNER" "$REPO" "$_CID"
  done
  _CANCEL_TITLE=$(gh pr view "$_CANCEL_PR" --json title --jq '.title' 2>/dev/null || echo "Unknown")
  _CANCEL_COMMENT="<!-- breakability-check -->
## ⚠️ SKIPPED — Analysis incomplete (batch was cancelled)

This PR was discovered but the analysis batch was cancelled or timed out before it could be processed.

**What to do:** Re-run the analysis: \`gh workflow run breakability-agent.yml\`

${RUN_LINK}
> 🔬 *Deterministic analysis — batch incomplete*"
  gh_pr_comment "$_CANCEL_PR" "$_CANCEL_COMMENT" && \
    echo "  Posted 'cancelled' comment for PR #$_CANCEL_PR" || true

  # Add to results JSON so merge plan picks it up
  python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
data['prs']['$_CANCEL_PR'] = {
    'package': '$(echo "$_CANCEL_TITLE" | sed "s/'/\\\\'/g")',
    'from': '?', 'to': '?', 'ecosystem': 'unknown', 'bump': 'unknown',
    'dep_type': 'unknown', 'dep_relation': 'unknown', 'cves': [],
    'build': {'verdict': 'cancelled', 'main_exit': -1, 'pr_exit': -1,
              'output_tail': '', 'new_errors': [], 'install_method': 'none', 'error_class': ''},
    'test': {'ran': False, 'exit': None, 'output_tail': ''},
    'files_importing': [], 'pkg_dir': '/', 'install_ok': False,
    'verification_level': -1, 'verification_label': 'NA_cancelled',
    'verification_steps': [], 'skip_reason': 'batch cancelled/timed out'
}
with open('$RESULTS_FILE', 'w') as f:
    json.dump(data, f, indent=2)
" 2>/dev/null || true
done
if [[ -n "$CANCELLED_PRS" ]]; then
  echo "  Cancelled PRs:$CANCELLED_PRS"
else
  echo "  No cancelled PRs detected"
fi

if [[ -f ".github/scripts/policy_lowering.py" ]]; then
  if python3 .github/scripts/policy_lowering.py "$RESULTS_FILE" --enrich -o /tmp/build-results.policy.json 2>/tmp/policy-lowering.err; then
    mv /tmp/build-results.policy.json "$RESULTS_FILE"
  else
    echo "[warn] policy lowering unavailable; using legacy verdict map"
    cat /tmp/policy-lowering.err 2>/dev/null || true
  fi
fi

node "$CLI_PATH" verdict-map "$RESULTS_FILE" >/dev/null 2>&1 || echo "[warn] verdict-map unavailable; rendering will fail-closed to REVIEW"

# ── Re-assert AI adjudication as the LAST word ────────────────────────────────
# policy_lowering.py --enrich and `verdict-map` (above) rebuild verdict_v2 / the
# policy decision from raw deterministic evidence, clobbering any AI downgrade the
# reconcile step applied. The AI arbiter is authoritative for the break-reachable
# residue it resolved, so re-apply its decision here, after the clobbering steps
# and before the overlay. Without this, a verified false-positive the AI cleared
# (e.g. dep not imported in the bumped module) snaps back to REVIEW.
python3 - "$RESULTS_FILE" <<'PYEOF' || echo "[warn] ai re-assertion skipped"
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
changed = False
for pr in (data.get("prs") or {}).values():
    if not isinstance(pr, dict):
        continue
    adj = pr.get("ai_adjudication")
    if not isinstance(adj, dict):
        continue
    applied = adj.get("applied")
    if applied == "downgrade_to_safe":
        ev = adj.get("evidence") or ""
        pr["verdict_v2"] = {
            "verdict": "SAFE", "confidence": "L4", "priority": "P3", "severity": "low",
            "evidenceState": {"api_diff": "NONE", "usage": "NONE"},
            "residual": {"summary": ev, "check": adj.get("reason_code") or "safe:ai-resolved"},
            "reason": ev,
        }
        dec = (pr.get("policy_lowering") or {}).get("decision")
        if isinstance(dec, dict):
            dec.update({"verdict": "SAFE", "reason_code": adj.get("reason_code") or "safe:ai-resolved",
                        "severity": "low", "display_reason": ev})
        # Neutralize the raw deterministic merge-risk so the collapsed "Internal merge-risk
        # detail" block stops shouting REVIEW REQUIRED / High while the headline says SAFE.
        mr = pr.get("merge_risk")
        if isinstance(mr, dict):
            mr.update({"tag": "Low",
                       "reason": ev,
                       "evidenceAxis": "AI-adjudicated: change not reachable in the bumped module"})
        changed = True
    elif applied == "needs_change":
        v2 = pr.setdefault("verdict_v2", {})
        v2["verdict"] = "REVIEW"
        v2.setdefault("confidence", "L3")
        v2.setdefault("priority", "P2")
        v2.setdefault("severity", "medium")
        v2["residual"] = {"summary": adj.get("evidence") or "",
                          "check": "review:ai-needs-change"}
        v2["reason"] = adj.get("evidence") or v2.get("reason")
        changed = True
if changed:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    print("  AI adjudication re-asserted over deterministic rebuild")
PYEOF

python3 - "$RESULTS_FILE" <<'PYEOF' || echo "[warn] policy lowering overlay unavailable; using legacy verdict_v2"
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)

severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
rank_severity = {v: k for k, v in severity_rank.items()}
valid_v2_verdicts = {"SAFE", "REVIEW", "BLOCKED"}


def confidence_to_level(conf, action):
    if action == "ABSTAIN":
        return "L0"
    return {"high": "L4", "medium": "L3", "low": "L2"}.get(str(conf).lower(), "L2")


def priority(action, severity):
    if action == "FIX":
        return "P0"
    if severity == "high":
        return "P1"
    if severity == "medium":
        return "P2"
    return "P3"


def map_policy(decision):
    # CANONICAL mapping lives in .github/scripts/verdict_contract.py::map_policy_decision.
    # Prefer it so the renderer, reconcile, and the gate never drift again; fall back to the
    # inline copy (kept in sync) if the module can't be imported in this heredoc context.
    try:
        import os as _os, sys as _sys
        _sd = _os.path.join(_os.getcwd(), ".github", "scripts")
        if _sd not in _sys.path:
            _sys.path.insert(0, _sd)
        from verdict_contract import map_policy_decision as _canon
        return _canon(decision)
    except Exception:
        pass
    action = decision.get("verdict")
    severity = decision.get("severity")
    if severity not in severity_rank:
        severity = {"FIX": "high", "ABSTAIN": "medium", "REVIEW": "medium", "GLANCE": "low", "MERGE": "none"}.get(action, "medium")
    if action == "FIX":
        verdict = "BLOCKED"
    elif action in {"REVIEW", "ABSTAIN"}:
        verdict = "REVIEW"
    elif action in {"MERGE", "GLANCE"}:
        # GLANCE = clean build/tests, only soft/missing-changelog uncertainty -> auto-clear
        # (Safe to merge / optional glance, Low). Mapping GLANCE->REVIEW was the #121->#128
        # review-wall regression. Keep in sync with verdict_contract._ACTION_TO_BUCKET.
        verdict = "SAFE"
    else:
        return None
    return {
        "verdict": verdict,
        "severity": severity,
        "confidence": confidence_to_level(decision.get("confidence"), action),
        "priority": priority(action, severity),
        "reason": decision.get("display_reason") or decision.get("reason_code") or "",
        "residual": {
            "summary": decision.get("display_reason") or decision.get("reason_code") or "",
            "check": decision.get("reason_code") or "",
        },
        "policyDecision": decision,
    }


def stronger_review(existing, mapped):
    existing_sev = existing.get("severity")
    if existing_sev not in severity_rank:
        existing_sev = {"BLOCKED": "high", "REVIEW": "medium", "SAFE": "low"}.get(existing.get("verdict"), "medium")
    mapped_sev = mapped.get("severity")
    return severity_rank.get(mapped_sev, 2) >= severity_rank.get(existing_sev, 2)


def policy_has_hard_fail(policy):
    bundle = policy.get("bundle") if isinstance(policy, dict) else None
    signals = bundle.get("signals") if isinstance(bundle, dict) else None
    if not isinstance(signals, dict):
        return False
    for name in ("build", "test", "api_diff", "probe", "security"):
        record = signals.get(name)
        if isinstance(record, dict) and record.get("status") == "fail":
            return True
    return False


def policy_evidence_state(policy, existing_state):
    state = dict(existing_state) if isinstance(existing_state, dict) else {}
    bundle = policy.get("bundle") if isinstance(policy, dict) else None
    signals = bundle.get("signals") if isinstance(bundle, dict) else None
    if not isinstance(signals, dict):
        return state

    mapping = {
        "build": "build",
        "test": "test",
        "api_diff": "api_diff",
        "security": "vuln",
        "release_notes": "changelog",
        "reachability": "usage",
    }
    for source, target in mapping.items():
        record = signals.get(source)
        if not isinstance(record, dict):
            continue
        status = record.get("status")
        if status == "fail":
            state[target] = "POSITIVE"
        elif status == "pass":
            state[target] = "NEGATIVE"
        elif status == "not_applicable":
            state[target] = "N_A"
        elif status == "unavailable":
            state[target] = "UNAVAILABLE"
        elif status == "unknown":
            state[target] = "NONE"
    return state


changed = False
for pr in (data.get("prs") or {}).values():
    if not isinstance(pr, dict):
        continue
    policy = pr.get("policy_lowering") or {}
    # The AI arbiter (independent-first + deterministic-audit) is authoritative for the
    # break-reachable residue it resolved. Do not let the legacy policy overlay revert an
    # AI-applied downgrade/finding using the stale pre-reconcile policy decision.
    adj = pr.get("ai_adjudication")
    if isinstance(adj, dict) and adj.get("applied") in ("downgrade_to_safe", "needs_change"):
        continue
    decision = policy.get("decision") if isinstance(policy, dict) else None
    if not isinstance(decision, dict):
        continue
    mapped = map_policy(decision)
    if not mapped:
        continue

    existing = pr.get("verdict_v2")
    if not isinstance(existing, dict) or existing.get("verdict") not in valid_v2_verdicts:
        existing = {}
    existing_verdict = existing.get("verdict")

    if existing_verdict == "BLOCKED" and mapped["verdict"] != "BLOCKED":
        if not (
            mapped["verdict"] == "REVIEW"
            and decision.get("reason_code") == "review:uncertain-critical-signal"
            and not policy_has_hard_fail(policy)
        ):
            continue
    if mapped["verdict"] == "SAFE" and existing_verdict == "BLOCKED":
        continue
    if mapped["verdict"] == "REVIEW" and existing_verdict == "REVIEW" and not stronger_review(existing, mapped):
        existing_sev = existing.get("severity")
        allow_glance_lowering = (
            decision.get("verdict") == "GLANCE"
            and str(decision.get("reason_code") or "").startswith("glance:")
            and existing_sev != "high"
            and not policy_has_hard_fail(policy)
        )
        if not allow_glance_lowering:
            continue
    if mapped["verdict"] == "SAFE" and existing_verdict == "REVIEW" and decision.get("verdict") == "GLANCE":
        # A clean-build GLANCE auto-clears to SAFE, but it must NOT override an existing
        # high-risk REVIEW (e.g. a declared-break flagged by another layer). Only lower a
        # SOFT (non-high) glance-class REVIEW. MERGE (hard-clean) keeps its prior override.
        #
        # Exception: `glance:tests-pass-soft-api-uncertain` is emitted by the evidence contract
        # ONLY when build, tests AND release-notes are all clean and the API diff found just
        # non-breaking uncertainty (after structural-fallback noise suppression). When the JS
        # verdict-map still rates such a PR a high REVIEW, that high is a structural-fallback
        # false break-reachable (a `go doc` type_changed whose old==new definition) — the
        # tested policy layer has authoritatively cleared it, so it may lower even a high REVIEW.
        existing_sev = existing.get("severity")
        soft_api_glance = decision.get("reason_code") == "glance:tests-pass-soft-api-uncertain"
        allow_glance_lowering = (
            str(decision.get("reason_code") or "").startswith("glance:")
            and (existing_sev != "high" or soft_api_glance)
            and not policy_has_hard_fail(policy)
        )
        if not allow_glance_lowering:
            continue
    if mapped["verdict"] == "SAFE" and existing_verdict == "SAFE":
        continue

    merged = dict(existing)
    merged.update(mapped)
    merged["evidenceState"] = policy_evidence_state(policy, existing.get("evidenceState"))
    pr["verdict_v2"] = merged
    # When we auto-clear a REVIEW to SAFE, the raw JS-baked merge_risk may still shout a High
    # "BREAK-reachable ..." that the tested policy layer has cleared (a structural-fallback
    # false break — go-doc type_changed with old==new definition). Neutralize it so the
    # collapsed merge-risk detail block and the overclaim audit agree with the SAFE headline.
    if mapped["verdict"] == "SAFE" and existing_verdict == "REVIEW":
        mr = pr.get("merge_risk")
        if isinstance(mr, dict) and mr.get("tag") == "High" and "break-reachable" in str(mr.get("reason") or "").lower():
            mr.update({
                "tag": "Low",
                "reason": merged.get("reason") or "API diff found only non-breaking uncertainty; not break-reachable",
                "evidenceAxis": "policy-cleared: structural API-diff noise, not a real break-reachable change",
            })
    changed = True

if changed:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
PYEOF

get_verdict_v2() {
  local _pr_number="${1:-}" _BC_V2_PR _BC_V2_RESULTS
  _BC_V2_PR="$_pr_number"
  _BC_V2_RESULTS="$RESULTS_FILE"
  export _BC_V2_PR _BC_V2_RESULTS
  python3 - <<'PYEOF'
import json
import os
import re
import shlex

SIGNALS = ("resolve", "build", "test", "api_diff", "usage", "vuln", "changelog")
SIGNAL_STATES = {"POSITIVE", "NEGATIVE", "NONE", "UNAVAILABLE", "N_A"}
DEFAULTS = {
    "V2_OK": "0",
    "V2_VERDICT": "REVIEW",
    "V2_SEVERITY": "medium",
    "V2_CONF": "L0",
    "V2_PRIO": "P2",
    "V2_REASON": "verdict map unavailable — manual review",
    "V2_BREAK_GRADE": "MEDIUM_BREAKING",
    "V2_RESIDUAL_SUMMARY": "",
    "V2_RESIDUAL_CHECK": "",
    "V2_RESIDUAL_CHANGELOG": "",
    "V2_RESIDUAL_REACH": "",
}

def emit(values):
    for key in (
        "V2_OK",
        "V2_VERDICT",
        "V2_SEVERITY",
        "V2_CONF",
        "V2_PRIO",
        "V2_REASON",
        "V2_BREAK_GRADE",
        "V2_RESIDUAL_SUMMARY",
        "V2_RESIDUAL_CHECK",
        "V2_RESIDUAL_CHANGELOG",
        "V2_RESIDUAL_REACH",
    ):
        value = "" if values.get(key) is None else str(values.get(key, ""))
        value = value.replace("\r", "\n")
        print(f"{key}={shlex.quote(value)}")
    signal_values = values.get("_signals", {})
    for signal in SIGNALS:
        state = signal_values.get(signal, "UNAVAILABLE")
        if state not in SIGNAL_STATES:
            state = "UNAVAILABLE"
        print(f"V2_SIG_{signal}={shlex.quote(state)}")

def fail():
    emit(DEFAULTS)

def clean_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    return str(value).replace("\r\n", "\n").replace("\r", "\n")

try:
    pr_number = os.environ.get("_BC_V2_PR", "")
    results_path = os.environ.get("_BC_V2_RESULTS", "")
    with open(results_path, encoding="utf-8") as fh:
        data = json.load(fh)
    verdict_v2 = ((data.get("prs") or {}).get(pr_number) or {}).get("verdict_v2")
    if not isinstance(verdict_v2, dict):
        fail()
        raise SystemExit(0)

    verdict = verdict_v2.get("verdict")
    confidence = verdict_v2.get("confidence")
    priority = verdict_v2.get("priority")
    if verdict not in {"SAFE", "REVIEW", "BLOCKED"}:
        fail()
        raise SystemExit(0)
    if not isinstance(confidence, str) or not re.fullmatch(r"L[0-5]", confidence):
        fail()
        raise SystemExit(0)
    if not isinstance(priority, str) or not re.fullmatch(r"P[0-3]", priority):
        fail()
        raise SystemExit(0)

    residual = verdict_v2.get("residual") or {}
    if not isinstance(residual, dict):
        residual = {}
    reachability = residual.get("reachability") or {}
    if not isinstance(reachability, dict):
        reachability = {}
    evidence_state = verdict_v2.get("evidenceState") or {}
    if not isinstance(evidence_state, dict):
        evidence_state = {}

    severity = verdict_v2.get("severity")
    if severity not in {"none", "low", "medium", "high"}:
        # Fail-safe derivation if the bundle predates the severity field.
        severity = {"BLOCKED": "high", "SAFE": "low", "REVIEW": "medium"}.get(verdict, "medium")
    
    breakability_grade = verdict_v2.get("breakability_grade", "MEDIUM_BREAKING")

    values = {
        "V2_OK": "1",
        "V2_VERDICT": verdict,
        "V2_SEVERITY": severity,
        "V2_CONF": confidence,
        "V2_PRIO": priority,
        "V2_REASON": clean_text(verdict_v2.get("reason")),
        "V2_BREAK_GRADE": breakability_grade,
        "V2_RESIDUAL_SUMMARY": clean_text(residual.get("summary")),
        "V2_RESIDUAL_CHECK": clean_text(residual.get("check")),
        "V2_RESIDUAL_CHANGELOG": clean_text(residual.get("changelogLine")),
        "V2_RESIDUAL_REACH": clean_text(reachability.get("path") or reachability.get("kind")),
        "_signals": {signal: str(evidence_state.get(signal, "UNAVAILABLE")) for signal in SIGNALS},
    }
    emit(values)
except Exception:
    fail()
PYEOF
}

get_behavioral_grade() {
  local _pr_number="${1:-}"
  _BC_BG_PR="$_pr_number" _BC_BG_RESULTS="$RESULTS_FILE" python3 - <<'PYEOF'
import json, os, shlex

KEYS = ("BG_OK", "BG_GRADE", "BG_SOURCE", "BG_RATIONALE", "BG_GUIDANCE",
        "BG_EVIDENCE", "BG_CALLSITE", "BG_CHANGED", "BG_CONFIDENCE")
DEFAULTS = {k: "" for k in KEYS}
DEFAULTS["BG_OK"] = "0"

def emit(v):
    for k in KEYS:
        val = "" if v.get(k) is None else str(v.get(k, "")).replace("\r", " ")
        print(f"{k}={shlex.quote(val)}")

try:
    pr = os.environ.get("_BC_BG_PR", "")
    with open(os.environ.get("_BC_BG_RESULTS", ""), encoding="utf-8") as fh:
        data = json.load(fh)
    g = ((data.get("prs") or {}).get(pr) or {}).get("behavioral_grade")
    if not isinstance(g, dict) or str(g.get("grade", "")).lower() not in ("none", "low", "medium", "high"):
        emit(DEFAULTS); raise SystemExit(0)
    emit({
        "BG_OK": "1",
        "BG_GRADE": str(g.get("grade", "")).lower(),
        "BG_SOURCE": str(g.get("source", "")),
        "BG_RATIONALE": str(g.get("rationale", "")),
        "BG_GUIDANCE": str(g.get("guidance", "")),
        "BG_EVIDENCE": str(g.get("evidence", "")),
        "BG_CALLSITE": str(g.get("call_site", "")),
        "BG_CHANGED": str(g.get("behavior_changed", "")),
        "BG_CONFIDENCE": str(g.get("confidence", "")),
    })
except Exception:
    emit(DEFAULTS)
PYEOF
}

v2_signal_label() {
  case "${1:-UNAVAILABLE}" in
    POSITIVE) printf '⚠️ concern' ;;
    NEGATIVE) printf '✅ checked-clean' ;;
    NONE) printf '· not observed' ;;
    UNAVAILABLE) printf '⚪ not available' ;;
    N_A) printf '– n/a' ;;
    *) printf '⚪ not available' ;;
  esac
}

# Get all PR numbers from build-results.json
PR_NUMBERS=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
for num in sorted(data.get('prs', {}).keys(), key=int):
    print(num)
")

build_go_changelog_block() {
  local _pkg="$1" _from="$2" _to="$3" _gh_path _releases _changelog _content _signals
  [[ -z "$_pkg" || -z "$_from" || -z "$_to" ]] && return 0
  _gh_path=$(echo "$_pkg" | grep -oE '^github\.com/[^/]+/[^/]+' || echo "")
  if [[ -z "$_gh_path" ]]; then
    _gh_path=$(echo "$_pkg" | sed -n 's|^golang.org/x/\([^/]*\)|github.com/golang/\1|p')
  fi
  if [[ -z "$_gh_path" ]]; then
    _gh_path=$(echo "$_pkg" | sed -n 's|^go.opentelemetry.io/.*|github.com/open-telemetry/opentelemetry-go|p' | head -1)
  fi
  [[ -z "$_gh_path" ]] && return 0

  unset GH_TOKEN
  _releases=$(gh api "repos/${_gh_path#github.com/}/releases?per_page=100" --jq '[.[] | {tag_name,name,body: ((.body // "")[0:4000])}]' 2>/dev/null || echo '[]')
  _changelog=""
  for _candidate in CHANGELOG.md CHANGES.md HISTORY.md RELEASES.md; do
    unset GH_TOKEN
    _content=$(gh api "repos/${_gh_path#github.com/}/contents/${_candidate}" --jq '.content // ""' 2>/dev/null | python3 -c 'import base64,sys; data=sys.stdin.read().strip(); print(base64.b64decode(data).decode("utf-8","replace") if data else "")' 2>/dev/null | head -c 24000 || true)
    if [[ -n "$_content" ]]; then
      _changelog="$_content"
      break
    fi
  done

  _signals=$(_BC_RELEASES="$_releases" _BC_CHANGELOG="$_changelog" _BC_FROM="$_from" _BC_TO="$_to" _BC_GH_PATH="$_gh_path" python3 -c '
import json, os, re
releases = json.loads(os.environ.get("_BC_RELEASES", "[]") or "[]")
changelog = os.environ.get("_BC_CHANGELOG", "") or ""
from_v = os.environ.get("_BC_FROM", "")
to_v = os.environ.get("_BC_TO", "")
gh_path = os.environ.get("_BC_GH_PATH", "")

def norm(v):
    v = (v or "").strip().lstrip("v")
    m = re.search(r"(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", v)
    return m.group(1) if m else v
def _clip(x, n=220):
    x = (x or "").strip()
    if len(x) <= n:
        return x
    head = x[:n].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (head or x[:n]) + "…"
def tup(v):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", norm(v))
    return tuple(map(int, m.groups())) if m else None
lo, hi = tup(from_v), tup(to_v)
def in_range(tag):
    tv = tup(tag)
    if not tv or not lo or not hi:
        return norm(to_v) in norm(tag)
    return lo < tv <= hi
patterns = re.compile(r"\b(BREAKING|removed?|incompatible|migration|required|default(?:s| value)? change|deprecated|renamed|deleted|no longer|behavior change|API change)\b", re.I)
items = []
for rel in releases:
    tag = rel.get("tag_name") or rel.get("name") or ""
    if not in_range(tag):
        continue
    text = "\n".join([str(rel.get("name") or tag), str(rel.get("body") or "")])
    for line in text.splitlines():
        line = line.strip(" -*\t")
        if line and patterns.search(line):
            items.append((tag, _clip(line, 220)))
            break
if changelog:
    for line in changelog.splitlines():
        line = line.strip(" -*\t")
        if line and patterns.search(line):
            items.append(("CHANGELOG", _clip(line, 220)))
            if len(items) >= 10:
                break
seen=[]
for tag,line in items:
    val=(tag,line)
    if val not in seen:
        seen.append(val)
if seen:
    print("### Changelog signals")
    print(f"Source: GitHub releases/CHANGELOG for `{gh_path}` between `{from_v}` → `{to_v}`")
    for tag,line in seen[:10]:
        print(f"- `{tag}`: {line}")
else:
    print("### Changelog signals")
    print(f"Source: [{gh_path} compare](https://{gh_path}/compare/v{from_v}...v{to_v})")
    print("- No deterministic breaking-change markers found in fetched Releases/CHANGELOG (checked for BREAKING, removed APIs, incompatible/default-value changes).")
' 2>/dev/null || true)
  [[ -n "$_signals" ]] && printf '
%s
' "$_signals"
}

# G6: render "### Changelog signals" from the PERSISTED deterministic changelog analysis
# (deterministic.changelogSignal / changelogText) so the below-the-fold list and the verdict
# residual are ONE source of truth. Emits nothing when no persisted analysis exists, letting
# the caller fall back to the live GitHub re-fetch for legacy records. Arg 1 = PR_FIELDS JSON.
build_changelog_block_persisted() {
  local _bcp
  _bcp=$(_BC_PRF="$1" python3 - <<'PYEOF' 2>/dev/null
import json, os, re
def _clip(x, n=220):
    x = (x or '').strip()
    if len(x) <= n:
        return x
    head = x[:n].rsplit(' ', 1)[0].rstrip(' ,.;:-')
    return (head or x[:n]) + '…'
data = json.loads(os.environ.get('_BC_PRF', '') or '{}')
det = data.get('deterministic') or {}
sig = det.get('changelogSignal') or {}
status = sig.get('status')
text = det.get('changelogText') or ''
# No persisted analysis at all -> let the caller fall back to the live re-fetch.
if not status and not text:
    raise SystemExit(0)
clean, seen = [], set()
for b in (sig.get('bullets') or []):
    if not isinstance(b, str):
        continue
    s = re.sub(r'\s+', ' ', b.replace('\r', ' ').replace('\n', ' ')).strip(' -*\t')
    if not s or s.startswith('#'):  # drop pure markdown headers
        continue
    s = _clip(s, 220)
    k = s.lower()
    if k in seen:
        continue
    seen.add(k)
    clean.append(s)
    if len(clean) >= 10:
        break
print("### Changelog signals")
print("Source: deterministic changelog analysis (same source as the verdict)")
if clean:
    for s in clean:
        print(f"- {s}")
elif status and status != 'breaking':
    print("- No breaking-change markers found in the analyzed changelog.")
else:
    snippet = _clip(re.sub(r'\s+', ' ', text), 220)
    print(f"- {snippet}" if snippet else "- Changelog analyzed; see release notes for details.")
PYEOF
)
  # Lead with a blank line so the block never fuses to preceding inline text (markdown heading).
  [[ -n "$_bcp" ]] && printf '\n\n%s\n' "$_bcp"
}

# ── Pre-create the merge-plan issue so per-PR comments link the LIVE plan ─────
# The plan BODY is generated after the per-PR loop, but the comments posted inside
# that loop must link the plan THIS run produces — not the previous run's still-open
# issue. So close stale plans and create a fresh placeholder NOW, capture its number,
# and EDIT it with the real body at the end. (Fixes stale "Merge plan: #NNN" links.)
_MP_LABEL="breakability-merge-plan"
if [[ "$DRY_RUN" == "1" ]]; then
  MERGE_PLAN_NUM="LOCAL"
  export MERGE_PLAN_NUM
  echo "  [dry-run] skipping merge-plan issue close/create (number=LOCAL)"
else
# Ensure the merge-plan label exists — `gh issue create --label` hard-fails if the
# label is absent (a fresh repo has none), which previously emptied MERGE_PLAN_NUM and
# cascaded into "Failed to create merge plan issue". Create it idempotently.
gh label create "$_MP_LABEL" --color "0e8a16" --description "Breakability merge plan" >/dev/null 2>&1 || true
_MP_OLD=$(gh issue list --label "$_MP_LABEL" --state open --json number -q '.[].number' 2>/dev/null || echo "")
_MP_OLD_LEGACY=$(gh issue list --label "dependencies" --state open --json number,title \
  -q '.[] | select(.title | test("📋.*[Mm]erge [Pp]lan|[Dd]ependabot [Mm]erge [Pp]lan|[Bb]reakability [Mm]erge [Pp]lan")) | .number' 2>/dev/null || echo "")
_MP_OLD_UNLABELED=$(gh issue list --state open --json number,title,labels \
  -q '.[] | select((.labels | length) == 0) | select(.title | test("[Dd]ependabot [Mm]erge [Pp]lan|📋.*[Mm]erge [Pp]lan")) | .number' 2>/dev/null || echo "")
for _OLD_NUM in $_MP_OLD $_MP_OLD_LEGACY $_MP_OLD_UNLABELED; do
  [[ -z "$_OLD_NUM" ]] && continue
  gh issue close "$_OLD_NUM" --comment "Superseded by new merge plan run at $(date -u '+%Y-%m-%d %H:%M UTC')." 2>/dev/null && \
    echo "  Closed old merge plan issue #$_OLD_NUM" || true
done
_MP_PLACEHOLDER_URL=$(gh issue create \
  --title "📋 Breakability Merge Plan $(date -u '+%Y-%m-%d %H:%M UTC') (generating…)" \
  --body "⏳ Merge plan is being generated from the latest build results — this issue updates momentarily." \
  --label "$_MP_LABEL" 2>/dev/null || echo "")
MERGE_PLAN_NUM=$(echo "$_MP_PLACEHOLDER_URL" | grep -oE '[0-9]+$' || echo "")
export MERGE_PLAN_NUM
if [[ -n "$MERGE_PLAN_NUM" ]]; then
  echo "  Reserved merge plan issue #$MERGE_PLAN_NUM"
else
  echo "  ⚠️  Could not pre-create merge plan issue — comments will omit the plan link"
fi
fi

for PR_NUM in $PR_NUMBERS; do
  # Per-PR atomic comment management (A3-9):
  # 1. Check for existing AI agent comments (preserve those)
  # 2. Delete old deterministic comments (<!-- breakability-check --> without <!-- breakability-agent -->)
  # 3. Post new deterministic comment
  # This avoids the race where merge-results.sh deletes comments before this script posts.
  # V9.8 iter6 (D): only preserve AGENT comments from THIS run (or within last 2 hours).
  # Stale agent comments from previous runs were surviving forever, causing dual-comment contradictions.
  _WF_STARTED_AT="${GITHUB_RUN_STARTED_AT:-}"
  if [[ -z "$_WF_STARTED_AT" ]]; then
    # Fallback: anything created in the last 2 hours is "current run"
    _CUTOFF=$(date -u -d "2 hours ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v-2H +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "1970-01-01T00:00:00Z")
  else
    _CUTOFF="$_WF_STARTED_AT"
  fi
  HAS_AGENT_COMMENT=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
    --jq "[.[] | select(.body | contains(\"<!-- breakability-agent -->\")) | select(.created_at >= \"$_CUTOFF\")] | length" \
    2>/dev/null || echo "0")

  if [[ "$HAS_AGENT_COMMENT" -gt 0 ]]; then
    # AI agent already posted a richer comment IN THIS RUN — skip deterministic fallback.
    # Still delete stale pre-cutoff agent comments so only the current one remains.
    STALE_AGENT_IDS=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
      --jq ".[] | select(.body | contains(\"<!-- breakability-agent -->\")) | select(.created_at < \"$_CUTOFF\") | .id" \
      2>/dev/null || true)
    for CID in $STALE_AGENT_IDS; do
      gh_delete_comment "$OWNER" "$REPO" "$CID"
    done
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # No current-run agent comment → delete ALL previous breakability comments (both markers)
  # so the new deterministic comment is the single source of truth.
  OLD_COMMENT_IDS=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
    --jq '.[] | select(.body | contains("<!-- breakability-check -->") or (.body | contains("<!-- breakability-agent -->"))) | .id' \
    2>/dev/null || true)
  for CID in $OLD_COMMENT_IDS; do
    gh_delete_comment "$OWNER" "$REPO" "$CID"
  done

  # Extract all fields for this PR in one python call
  PR_FIELDS=$(python3 -c "
import json, sys
with open('$RESULTS_FILE') as f:
    data = json.load(f)
pr = data['prs'].get('$PR_NUM', {})
build = pr.get('build', {})
test = pr.get('test', {})
sec = data.get('security_posture', {}).get('prs_fixing_alerts', {}).get('$PR_NUM', {})
cve_severities = sec.get('severities', [])
cve_ids = sec.get('cve_ids', [])
print(json.dumps({
    'package':      pr.get('package', '?'),
    'from':         pr.get('from', '?'),
    'to':           pr.get('to', '?'),
    'bump':         pr.get('bump', '?'),
    'dep_type':     pr.get('dep_type', '?'),
    'dep_relation': pr.get('dep_relation', '?'),
    'ecosystem':    pr.get('ecosystem', '?'),
    'verdict':      build.get('verdict', '?'),
    'install_method': build.get('install_method', ''),
    'install_ok':   build.get('install_ok', False),
    'new_errors':   build.get('new_errors', []),
    'output_tail':  build.get('output_tail', ''),
    'test_ran':     test.get('ran', False),
    'test_exit':    test.get('exit', -1),
    'test_output_tail': test.get('output_tail', ''),
    'main_test_exit': test.get('main_test_exit', -1),
    'verification_label': pr.get('verification_label', ''),
    'files_importing': pr.get('files_importing', []),
    'cves':         pr.get('cves', []),
    'error_class':  build.get('error_class', ''),
    'pkg_dir':      pr.get('pkg_dir', '/'),
    'main_exit':    build.get('main_exit', -1),
    'pr_exit':      build.get('pr_exit', -1),
    'cve_severities': cve_severities,
    'cve_ids':      cve_ids,
    'gosum_new_count': pr.get('gosum_new_count', 0),
    'gosum_new_names': pr.get('gosum_new_names', ''),
    'gosum_total_pr':  pr.get('gosum_total_pr', 0),
    'gosum_total_main': pr.get('gosum_total_main', 0),
    'vuln_status':     pr.get('vuln_status', 'unknown'),
    'vuln_finding':    pr.get('vuln_finding', ''),
    'vuln_new_findings': pr.get('vuln_new_findings', []),
    'vuln_output':     pr.get('vuln_output', ''),
    'vuln_preexisting_count': pr.get('vuln_preexisting_count', 0),
    'go_resolution': pr.get('go_resolution', {}),
    'no_test_confidence': pr.get('no_test_confidence', {}),
    'deterministic': pr.get('deterministic', {}),
    'merge_risk': pr.get('merge_risk', {}) or (pr.get('deterministic', {}) or {}).get('merge_risk', {}),
    'declared_break_reachability': pr.get('declared_break_reachability', {}),
    'ai_behavioral_assessment': pr.get('ai_behavioral_assessment', {}),
    'behavioral_grade': pr.get('behavioral_grade', {}),
    'cve_details': pr.get('cve_details', []),
    'verification_steps': pr.get('verification_steps', []),
    'fixes_cves': pr.get('fixes_cves', []),
}))
" 2>/dev/null || echo '{}')

  # Extract all fields in a single Python call instead of 13 separate spawns (CR2-7).
  # This reduces per-PR Python process spawns from 15 to 3 (fields + CVE extraction).
  _FIELDS_EXTRACTED=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
fields = {
    'PKG': d.get('package', '?'),
    'FROM': d.get('from', '?'),
    'TO': d.get('to', '?'),
    'BUMP': d.get('bump', '?'),
    'DEP_TYPE': d.get('dep_type', '?'),
    'DEP_REL': d.get('dep_relation', '?'),
    'ECOSYSTEM': d.get('ecosystem', '?'),
    'VERDICT': d.get('verdict', '?'),
    'INSTALL_METHOD': d.get('install_method', ''),
    'INSTALL_OK': str(d.get('install_ok', False)),
    'VER_LABEL': d.get('verification_label', ''),
    'NEW_ERR_COUNT': str(len(d.get('new_errors', []))),
    'FILES_COUNT': str(len(d.get('files_importing', []))),
    'PKG_DIR': d.get('pkg_dir', '/'),
    'ERROR_CLASS': d.get('error_class', ''),
    'OOM_OVERRIDE': str(d.get('oom_override', False)),
    'OOM_PACKAGES': ','.join(d.get('oom_packages', [])),
    'GOSUM_NEW_COUNT': str(d.get('gosum_new_count', 0)),
    'GOSUM_NEW_NAMES': d.get('gosum_new_names', ''),
    'GOSUM_TOTAL_PR': str(d.get('gosum_total_pr', 0)),
    'GOSUM_TOTAL_MAIN': str(d.get('gosum_total_main', 0)),
    'VULN_STATUS': d.get('vuln_status', 'unknown'),
    'VULN_FINDING': d.get('vuln_finding', ''),
    'VULN_NEW_COUNT': str(len(d.get('vuln_new_findings', []))),
    'VULN_NEW_LIST': ','.join(d.get('vuln_new_findings', [])),
    'VULN_PREEXISTING_COUNT': str(d.get('vuln_preexisting_count', 0)),
    'VULN_EVIDENCE': (lambda f: '\n'.join(f.splitlines()[:10]) if f else '')(d.get('vuln_finding', '')),
    'TEST_SUMMARY': (lambda t: '\n'.join(l for l in t.splitlines() if 'ok' in l.lower() or 'PASS' in l or 'FAIL' in l or 'pass' in l or '--- PASS' in l or 'passed' in l or 'failed' in l or 'tests' in l.lower())[:500] if t else '')(d.get('test_output_tail', '')),
    'FILES_LIST': '|'.join((f.split(':')[0] if ':' in f else f) for f in d.get('files_importing', [])[:8]),
    'TEST_FAIL_DETAIL': next((s.get('detail','') for s in d.get('verification_steps',[]) if s.get('step')=='test_suite' and s.get('status')=='pre_existing'), ''),
    'BUILD_EXIT': str(d.get('pr_exit', -1)),       # PR-branch build exit
    'PR_BUILD_EXIT': str(d.get('pr_exit', -1)),
    'MAIN_BUILD_EXIT': str(d.get('main_exit', -1)),
    'TEST_EXIT_CODE': str(d.get('test_exit', -1)),
    'TEST_RAN': str(d.get('test_ran', False)),
    'MAIN_TEST_EXIT': str(d.get('main_test_exit', -1)),
    'BUILD_EVIDENCE': (lambda t: next((l.strip() for l in t.splitlines() if 'targeted build' in l or 'full build' in l or 'npm run build' in l), ''))(d.get('output_tail', '')),
    'BUILD_DIRS': (lambda t: next((l.strip() for l in t.splitlines() if 'dirs:' in l), ''))(d.get('output_tail', '')),
    'MERGE_RISK_TAG': (d.get('merge_risk') or {}).get('tag', ''),
    'MERGE_RISK_REASON': (d.get('merge_risk') or {}).get('reason', ''),
    'MERGE_RISK_EVIDENCE': (d.get('merge_risk') or {}).get('evidenceAxis', ''),
    'MERGE_RISK_BUILD_VERIFICATION': (d.get('merge_risk') or {}).get('buildVerificationAxis', '') or (d.get('merge_risk') or {}).get('confidenceAxis', ''),
}
for k, v in fields.items():
    # Use null byte as delimiter to safely handle any value content
    print(f'{k}={v}')
" 2>/dev/null || echo "")
  # Parse the output into shell variables
  PKG=$(echo "$_FIELDS_EXTRACTED" | grep '^PKG=' | cut -d= -f2-)
  FROM=$(echo "$_FIELDS_EXTRACTED" | grep '^FROM=' | cut -d= -f2-)
  TO=$(echo "$_FIELDS_EXTRACTED" | grep '^TO=' | cut -d= -f2-)
  BUMP=$(echo "$_FIELDS_EXTRACTED" | grep '^BUMP=' | cut -d= -f2-)
  # 0.x semver: only flag 0.x major bumps, not real v1→v2 upgrades
  FROM_MAJOR="${FROM%%.*}"
  FROM_MAJOR="${FROM_MAJOR#v}"
  if [[ "$BUMP" == "major" && "$FROM_MAJOR" == "0" ]]; then
    BUMP_DISPLAY="major ⚠️ (0.x unstable — treat as breaking)"
  else
    BUMP_DISPLAY="$BUMP"
  fi
  DEP_TYPE=$(echo "$_FIELDS_EXTRACTED" | grep '^DEP_TYPE=' | cut -d= -f2-)
  DEP_REL=$(echo "$_FIELDS_EXTRACTED" | grep '^DEP_REL=' | cut -d= -f2-)
  ECOSYSTEM=$(echo "$_FIELDS_EXTRACTED" | grep '^ECOSYSTEM=' | cut -d= -f2-)
  # ── CI review tier ───────────────────────────────────────────────────────────
  # "CI-only" is NOT automatically "safe". Classify CI (actions/docker) deps into:
  #   secsens — handles tokens/creds/registry/cloud auth, code signing, OR deploy/publish.
  #             A breaking/compromised release here is a supply-chain risk -> security review.
  #   ""      — benign CI dep -> auto-safe changelog glance. Majorness alone is NOT a review
  #             trigger (a major setup-* bump is still a glance per the breakability oracle).
  # MUST stay in sync with ci_classifier.py (the policy-layer source of truth).
  _CI_TIER=""
  if printf '%s' "$PKG" | grep -qiE 'token|credential|secret|password|login|oauth|oidc|/auth|-auth|ssh-agent|import-gpg|gpg|cosign|sigstore|vault|kms|aws-actions|azure/login|google-github-actions/auth|configure-aws-credentials|registry|ghcr|ecr|gcr|deploy|release|publish|pages'; then
    _CI_TIER="secsens"
  fi
  VERDICT=$(echo "$_FIELDS_EXTRACTED" | grep '^VERDICT=' | cut -d= -f2-)
  INSTALL_METHOD=$(echo "$_FIELDS_EXTRACTED" | grep '^INSTALL_METHOD=' | cut -d= -f2-)
  INSTALL_OK=$(echo "$_FIELDS_EXTRACTED" | grep '^INSTALL_OK=' | cut -d= -f2-)
  VER_LABEL=$(echo "$_FIELDS_EXTRACTED" | grep '^VER_LABEL=' | cut -d= -f2-)
  NEW_ERR_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^NEW_ERR_COUNT=' | cut -d= -f2-)
  FILES_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^FILES_COUNT=' | cut -d= -f2-)
  PKG_DIR=$(echo "$_FIELDS_EXTRACTED" | grep '^PKG_DIR=' | cut -d= -f2-)
  ERROR_CLASS=$(echo "$_FIELDS_EXTRACTED" | grep '^ERROR_CLASS=' | cut -d= -f2-)
  OOM_OVERRIDE=$(echo "$_FIELDS_EXTRACTED" | grep '^OOM_OVERRIDE=' | cut -d= -f2-)
  OOM_PACKAGES=$(echo "$_FIELDS_EXTRACTED" | grep '^OOM_PACKAGES=' | cut -d= -f2-)
  GOSUM_NEW_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_NEW_COUNT=' | cut -d= -f2-)
  GOSUM_NEW_NAMES=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_NEW_NAMES=' | cut -d= -f2-)
  GOSUM_TOTAL_PR=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_TOTAL_PR=' | cut -d= -f2-)
  GOSUM_TOTAL_MAIN=$(echo "$_FIELDS_EXTRACTED" | grep '^GOSUM_TOTAL_MAIN=' | cut -d= -f2-)
  VULN_STATUS=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_STATUS=' | cut -d= -f2-)
  VULN_FINDING=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_FINDING=' | cut -d= -f2-)
  VULN_NEW_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_NEW_COUNT=' | cut -d= -f2-)
  VULN_NEW_LIST=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_NEW_LIST=' | cut -d= -f2-)
  VULN_PREEXISTING_COUNT=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_PREEXISTING_COUNT=' | cut -d= -f2-)
  TEST_FAIL_DETAIL=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_FAIL_DETAIL=' | cut -d= -f2-)
  BUILD_EXIT_CODE=$(echo "$_FIELDS_EXTRACTED" | grep '^BUILD_EXIT=' | cut -d= -f2-)
  PR_BUILD_EXIT=$(echo "$_FIELDS_EXTRACTED" | grep '^PR_BUILD_EXIT=' | cut -d= -f2-)
  MAIN_BUILD_EXIT=$(echo "$_FIELDS_EXTRACTED" | grep '^MAIN_BUILD_EXIT=' | cut -d= -f2-)
  # P1 (reviewer): a timeout/OOM-killed build (exit 124/137) cannot be trusted for an
  # "errors are identical" comparison — compilation was killed before all packages
  # were checked. Surface this caveat wherever we'd otherwise say "LIKELY SAFE".
  _TIMEOUT_CAVEAT=""
  if [[ "$PR_BUILD_EXIT" == "124" || "$PR_BUILD_EXIT" == "137" || "$MAIN_BUILD_EXIT" == "124" || "$MAIN_BUILD_EXIT" == "137" ]]; then
    _TIMEOUT_CAVEAT=" ⚠️ **Build was killed (timeout/OOM, exit ${PR_BUILD_EXIT}/${MAIN_BUILD_EXIT}) — the error comparison is INCOMPLETE.** Packages after the kill point were never compiled, so new type errors there would not be detected. Treat as inconclusive, not verified-safe."
  fi
  TEST_EXIT_CODE=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_EXIT_CODE=' | cut -d= -f2-)
  TEST_RAN=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_RAN=' | cut -d= -f2-)
  MAIN_TEST_EXIT=$(echo "$_FIELDS_EXTRACTED" | grep '^MAIN_TEST_EXIT=' | cut -d= -f2-)
  # ── Single-source, HONEST test-result framing (one derivation feeds BOTH the signals
  # table and the "how we checked" block, so the same fact can never read alarming in one
  # place and exculpatory in another — PR#16). We only call a failure "pre-existing" when
  # we can PROVE main also fails (upstream classified it via TEST_FAIL_DETAIL, or
  # MAIN_TEST_EXIT>0). When main never tested (its build broke -> main_test_exit=-1) we say
  # "could not confirm" — we do NOT fabricate "same failure on main". Safe direction:
  # underclaim pre-existing.
  _TESTS_CLEAN=0
  _TEST_FAILED=0
  if [[ "${TEST_RAN:-False}" == "True" && "${TEST_EXIT_CODE:-}" == "0" ]]; then
    _TESTS_CLEAN=1
  elif [[ "${TEST_RAN:-False}" == "True" && -n "${TEST_EXIT_CODE:-}" && "${TEST_EXIT_CODE}" != "-1" ]]; then
    _TEST_FAILED=1
  fi
  _TEST_PREEXIST_VERIFIED=0
  if [[ -n "${TEST_FAIL_DETAIL:-}" ]]; then
    _TEST_PREEXIST_VERIFIED=1
  elif [[ "${MAIN_TEST_EXIT:-}" =~ ^[0-9]+$ && "${MAIN_TEST_EXIT}" -gt 0 ]]; then
    _TEST_PREEXIST_VERIFIED=1
  fi
  # Shared phrases (pipe-safe for the markdown table; backticks escaped so bash never runs them).
  _TEST_SIGNAL_CELL=""
  _TEST_HOWCHECKED=""
  if [[ "$_TEST_FAILED" == "1" ]]; then
    if [[ "$_TEST_PREEXIST_VERIFIED" == "1" ]]; then
      _TEST_SIGNAL_CELL="⚠️ tests fail (classified pre-existing — \`main\` tests also fail, not introduced by this PR)"
      _TEST_HOWCHECKED="⚙️ Automated tests fail — classified pre-existing: \`main\` tests also fail, so this PR did not introduce them"
    else
      _TEST_SIGNAL_CELL="⚠️ tests fail (exit ${TEST_EXIT_CODE}) — could not confirm against \`main\` (its build/tests did not run clean); NOT verified pre-existing"
      _TEST_HOWCHECKED="⚙️ Automated tests fail (exit ${TEST_EXIT_CODE}) — could NOT confirm against \`main\` (its build/tests did not run clean); not verified as pre-existing — treat as unresolved"
    fi
  fi
  BUILD_EVIDENCE=$(echo "$_FIELDS_EXTRACTED" | grep '^BUILD_EVIDENCE=' | cut -d= -f2-)
  BUILD_DIRS=$(echo "$_FIELDS_EXTRACTED" | grep '^BUILD_DIRS=' | cut -d= -f2-)
  MERGE_RISK_TAG=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_TAG=' | cut -d= -f2-)
  MERGE_RISK_REASON=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_REASON=' | cut -d= -f2-)
  MERGE_RISK_EVIDENCE=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_EVIDENCE=' | cut -d= -f2-)
  MERGE_RISK_BUILD_VERIFICATION=$(echo "$_FIELDS_EXTRACTED" | grep '^MERGE_RISK_BUILD_VERIFICATION=' | cut -d= -f2-)
  # The deterministic merge-risk reason is built BEFORE the behavioral oracle runs, so it
  # ends in a "verify against the release notes" punt. Once the oracle has committed a CITED
  # grade, that punt is stale — the oracle already read the notes and graded the exposure.
  # Replace the punt tail with a pointer to the graded verdict + the oracle's runtime check,
  # and pick a non-punt tail for the High-branch REVIEW_WHY. Fail-open keeps the honest punt.
  eval "$(get_behavioral_grade "$PR_NUM")"
  _BG_SRC_LC=$(printf '%s' "${BG_SOURCE:-}" | tr '[:upper:]' '[:lower:]')
  _BG_CITED=0
  if [[ "${BG_OK:-0}" == "1" && ( "$_BG_SRC_LC" == "reasoning" || "$_BG_SRC_LC" == "probe" ) \
        && ( -n "${BG_RATIONALE:-}" || -n "${BG_GUIDANCE:-}" || -n "${BG_EVIDENCE:-}" ) ]]; then
    _BG_CITED=1
  fi
  _BG_CONF_LC=$(printf '%s' "${BG_CONFIDENCE:-}" | tr '[:upper:]' '[:lower:]')
  if [[ "$_BG_CITED" == "1" ]]; then
    case "$_BG_CONF_LC" in
      low|medium|high) MERGE_RISK_ORACLE_CONFIDENCE="$_BG_CONF_LC" ;;
      *) MERGE_RISK_ORACLE_CONFIDENCE="cited" ;;
    esac
  else
    MERGE_RISK_ORACLE_CONFIDENCE="not available"
  fi
  _REVIEW_WHY_TAIL=" Verify the affected behavior against the release notes before merging."
  if [[ "$_BG_CITED" == "1" ]]; then
    _REVIEW_WHY_TAIL=" The behavioral verdict below grades your actual exposure."
    if [[ "$MERGE_RISK_REASON" == *"verify against the release notes"* ]]; then
      _BG_LABEL=$(printf '%s' "${BG_GRADE:-medium}" | awk '{print toupper(substr($0,1,1)) substr($0,2)}')
      _RS="$MERGE_RISK_REASON"
      _RS="${_RS/ — verify against the release notes/}"
      _RS="${_RS/; verify against the release notes/}"
      _RS="${_RS/, but verify against the release notes/}"
      _RS="${_RS/ verify against the release notes/}"
      _BG_TAIL=" — the behavioral oracle graded your actual exposure **${_BG_LABEL}** (see the verdict above)"
      [[ -n "${BG_GUIDANCE:-}" ]] && _BG_TAIL+=": ${BG_GUIDANCE}"
      MERGE_RISK_REASON="${_RS}${_BG_TAIL}"
    fi
  fi
  VULN_EVIDENCE=$(echo "$_FIELDS_EXTRACTED" | grep '^VULN_EVIDENCE=' | cut -d= -f2-)
  TEST_SUMMARY=$(echo "$_FIELDS_EXTRACTED" | grep '^TEST_SUMMARY=' | cut -d= -f2-)
  FILES_LIST=$(echo "$_FIELDS_EXTRACTED" | grep '^FILES_LIST=' | cut -d= -f2-)

  # CVE extraction — core security data
  CVE_LIST=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
cves = d.get('cves', [])
if cves:
    print(','.join(cves))
else:
    print('')
" 2>/dev/null || echo "")
  CVE_COUNT=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('cves',[])))")

  # Extract CVE severity info (end-user P1: make CVEs impossible to miss)
  CVE_MAX_SEVERITY=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
sevs = d.get('cve_severities', [])
if not sevs:
    print('')
else:
    order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
    worst = min(sevs, key=lambda s: order.get(s.lower(), 99))
    print(worst.upper())
" 2>/dev/null || echo "")

  # V8 FIX: Build enriched security line with severity, CVSS, and advisory links
  CVE_LINE=""
  CVE_DETAIL_BLOCK=""
  if [[ "$CVE_COUNT" -gt 0 && "$CVE_COUNT" != "0" ]]; then
    # Build per-CVE detail lines with severity and advisory link
    CVE_DETAIL_BLOCK=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
# Look up cve_details from build-results.json (richer than PR_FIELDS inline)
pr_num = '$PR_NUM'
try:
    with open('/tmp/build-results.json') as f:
        results = json.load(f)
    details = results.get('prs', {}).get(pr_num, {}).get('cve_details', [])
except:
    details = []
if not details:
    sys.exit(0)
for det in details:
    _id = det.get('id', '?')
    sev = det.get('severity', 'unknown').upper()
    cvss = det.get('cvss_score')
    summary = det.get('summary', '')
    url = det.get('advisory_url', '')
    line = f'- **{_id}** ({sev}'
    if cvss:
        line += f', CVSS {cvss}'
    line += ')'
    if summary:
        line += f': {summary}'
    if url:
        line += f' — [advisory]({url})'
    print(line)
" 2>/dev/null || echo "")

    if [[ -n "$CVE_MAX_SEVERITY" ]]; then
      CVE_LINE="
🔴 **Security ($CVE_MAX_SEVERITY): $CVE_COUNT CVE(s) fixed by this upgrade:**"
    else
      CVE_LINE="
🔴 **Security: $CVE_COUNT CVE(s) fixed by this upgrade:** $CVE_LIST"
    fi
    if [[ -n "$CVE_DETAIL_BLOCK" ]]; then
      CVE_LINE="$CVE_LINE
$CVE_DETAIL_BLOCK"
    else
      CVE_LINE="$CVE_LINE $CVE_LIST"
    fi
  fi

  # Module line for monorepo context (end-user feedback: which module does this affect?)
  MODULE_LINE=""
  if [[ "$ECOSYSTEM" == "gomod" && "$PKG_DIR" != "/" && -n "$PKG_DIR" ]]; then
    MODULE_LINE=" · Module: \`$PKG_DIR\`"
  fi

  # V9.9 iter8: Show CVEs this PR fixes (from Dependabot alert matching) even if
  # the PR body doesn't mention them. This ensures PR #19 etc. show the fix.
  FIXES_CVE_LINE=""
  _FIXES_CVE_DATA=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
fixes = d.get('fixes_cves', [])
if not fixes:
    sys.exit(0)
sev_order = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
fixes.sort(key=lambda x: sev_order.get((x.get('severity') or '').lower(), 9))
parts = []
for f in fixes:
    cve = f.get('cve_id', '?')
    sev = (f.get('severity') or 'unknown').upper()
    parts.append(f'{cve} ({sev})')
print(' · '.join(parts))
" 2>/dev/null || echo "")
  if [[ -n "$_FIXES_CVE_DATA" && -z "$CVE_LINE" ]]; then
    # Only add if CVE_LINE is empty (PR body didn't have CVEs)
    FIXES_CVE_LINE="
🛡️ **This PR fixes known vulnerabilities:** $_FIXES_CVE_DATA — **merge with priority** (CVE reachability is a hint only)"
  elif [[ -n "$_FIXES_CVE_DATA" && -n "$CVE_LINE" ]]; then
    # PR body already has CVEs, append Dependabot-matched ones if different
    FIXES_CVE_LINE=""
  fi

  # V9.9 iter9/G2: Changelog evidence for Go/npm packages
  # G6: prefer the persisted deterministic changelog analysis (one source of truth with the
  # verdict); fall back to the live GitHub re-fetch only for legacy records lacking it.
  CHANGELOG_LINK=""
  CHANGELOG_BLOCK=""
  CHANGELOG_BLOCK=$(build_changelog_block_persisted "$PR_FIELDS")
  if [[ -z "$CHANGELOG_BLOCK" && "$ECOSYSTEM" == "gomod" && -n "$PKG" && -n "$FROM" && -n "$TO" ]]; then
    CHANGELOG_BLOCK=$(build_go_changelog_block "$PKG" "$FROM" "$TO")
  fi
  if [[ -n "$CHANGELOG_BLOCK" ]]; then
    CHANGELOG_LINK="${CHANGELOG_BLOCK}"
  elif [[ "$ECOSYSTEM" == "npm" && -n "$PKG" && -n "$FROM" && -n "$TO" ]]; then
    # npm: try npmjs changelog
    CHANGELOG_LINK="
📝 [Changelog](https://github.com/search?q=repo%3A${PKG}+path%3ACHANGELOG&type=code)"
  fi

  MERGE_RISK_TAG="${MERGE_RISK_TAG:-Medium}"
  MERGE_RISK_REASON="${MERGE_RISK_REASON:-change evidence is limited; default caution}"
  MERGE_RISK_EVIDENCE="${MERGE_RISK_EVIDENCE:-limited evidence}"
  MERGE_RISK_BUILD_VERIFICATION="${MERGE_RISK_BUILD_VERIFICATION:-${VER_LABEL:-unverified}}"
  MERGE_RISK_LINE="**Merge Risk: ${MERGE_RISK_TAG}** (Evidence: ${MERGE_RISK_EVIDENCE} × Build verification: ${MERGE_RISK_BUILD_VERIFICATION} × Oracle confidence: ${MERGE_RISK_ORACLE_CONFIDENCE:-not available}) — ${MERGE_RISK_REASON}"

    # Build "How we checked" checklist from verification_label
  # Build file-list detail block for evidence
  _FILES_DETAIL_BLOCK=""
  if [[ -n "$FILES_LIST" && "$FILES_LIST" != "" ]]; then
    _SHOWN_FILES=$(echo "$FILES_LIST" | tr '|' '\n' | head -8 | sed 's/^/- `/' | sed 's/$/`/')
    _TOTAL_FILES="${FILES_COUNT:-0}"
    _SHOWN_COUNT=$(echo "$FILES_LIST" | tr '|' '\n' | head -8 | wc -l | tr -d ' ')
    _MORE_NOTE=""
    if [[ "$_TOTAL_FILES" -gt "$_SHOWN_COUNT" ]]; then
      _MORE_NOTE="
- *...and $((_TOTAL_FILES - _SHOWN_COUNT)) more file(s) — see full import graph in Actions run*"
    fi
    _FILES_DETAIL_BLOCK="
<details><summary>📂 Files importing this package ($FILES_COUNT file(s))</summary>

${_SHOWN_FILES}${_MORE_NOTE}
</details>"
  fi
  _USAGE_CONTEXT_INLINE=""
  _USAGE_CONTEXT_BLOCK=""
  if [[ "$ECOSYSTEM" == "gomod" ]]; then
    _USAGE_CONTEXT_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
d = json.loads(os.environ["_BC_PRF"])
det = (d.get('deterministic') or {})
usages = (det.get('usages') or [])
active = [u for u in usages if u.get('usageType') in ('DIRECT_CALL', 'PROPERTY_ACCESS')]
if not active:
    sys.exit(0)
# The authoritative set of symbols that ACTUALLY changed in this dependency comes from apidiff
# (api_changes_detail), NOT from the broad usage scan (which also picks up stdlib/other-package
# symbols in the same files). Reachability must be the intersection of the two.
changed_detail = (det.get('api_changes_detail') or [])
changed_syms = []
sym_change_type = {}
for c in changed_detail:
    if isinstance(c, dict):
        s = (c.get('symbol') or c.get('name') or '').strip()
        if s and s not in changed_syms:
            changed_syms.append(s)
        if s:
            sym_change_type[s] = (c.get('changeType') or '').lower()
# Build matchable name components for each changed symbol (e.g. 'Float64Counter.Enabled'
# matches a usage of 'Enabled' or 'Float64Counter' or the full dotted name).
def components(sym):
    parts = [p for p in sym.split('.') if p]
    return set([sym] + parts)
changed_lookup = {sym: components(sym) for sym in changed_syms}
order = ['production', 'test', 'cicd', 'generated', 'iac']
labels = {'production': 'prod', 'test': 'test', 'cicd': 'CI/CD', 'generated': 'generated', 'iac': 'IaC'}
def files_by_context(items):
    out = {ctx: set() for ctx in order}
    for u in items:
        ctx = u.get('context') or 'production'
        if ctx not in out:
            ctx = 'production'
        f = (u.get('file') or '').split(':')[0]
        if f:
            out[ctx].add(f)
    return out
def fmt(counts):
    parts = []
    for ctx in order:
        n = len(counts.get(ctx, set()))
        if n:
            noun = 'file' if n == 1 else 'files'
            parts.append(f'{n} {labels[ctx]} {noun}')
    return ', '.join(parts)
usage_names = set((u.get('symbol') or '').strip() for u in active if (u.get('symbol') or '').strip())
# Which changed symbols are actually reached by our code?
reached = {}
for sym, comps in changed_lookup.items():
    hit = [u for u in active if (u.get('symbol') or '').strip() in comps]
    if hit:
        reached[sym] = hit
overall = files_by_context(active)
inline = fmt(overall)
print('INLINE= · Context: ' + inline if inline else 'INLINE=')
print('---BLOCK---')
print('### BREAK-reachability context')
if changed_syms:
    if not reached:
        print(f'- ✅ None of the {len(changed_syms)} changed API symbol(s) from this upgrade are reached by your code — the changed surface is unused here (raises confidence).')
        print(f'  - Changed symbols (apidiff): ' + ', '.join(f'`{s}`' for s in changed_syms[:8]) + (f' …(+{len(changed_syms)-8})' if len(changed_syms) > 8 else ''))
        print('  - Note: import-level reachability still applies (see imported files below); behavioral/transitive breaks are not visible to apidiff.')
    else:
        breaking_reached = [s for s in reached if sym_change_type.get(s) in ('removed', 'changed')]
        additive_reached = [s for s in reached if sym_change_type.get(s) not in ('removed', 'changed')]
        for sym in breaking_reached[:8]:
            loc = fmt(files_by_context(reached[sym]))
            if loc:
                print(f'- ⚠️ removed/changed API symbol `{sym}` reached in {loc} — a caller of this symbol can break')
        for sym in additive_reached[:8]:
            loc = fmt(files_by_context(reached[sym]))
            if loc:
                print(f'- ℹ️ changed API symbol `{sym}` reached in {loc} — additive (new method/symbol); only breaks code that *implements* the changed interface, not callers')
        extra = len(reached) - len(breaking_reached[:8]) - len(additive_reached[:8])
        if extra > 0:
            print(f'- …and {extra} more reached changed symbol(s)')
else:
    print('- No exported API symbols changed per apidiff; reachability is import-level only (see imported files below).')
if not overall['production'] and any(overall[ctx] for ctx in ('test', 'cicd', 'generated')):
    print('- Non-production-only reachability (test/CI/generated) is down-weighted in the merge-risk score.')
PYEOF
)
  fi
  if [[ -n "$_USAGE_CONTEXT_BLOCK" ]]; then
    _USAGE_CONTEXT_INLINE=$(echo "$_USAGE_CONTEXT_BLOCK" | grep '^INLINE=' | head -1 | cut -d= -f2-)
    _USAGE_CONTEXT_BLOCK=$(echo "$_USAGE_CONTEXT_BLOCK" | sed -n '/^---BLOCK---$/,$p' | tail -n +2)
    if [[ -n "$_USAGE_CONTEXT_BLOCK" ]]; then
      _USAGE_CONTEXT_BLOCK=$(printf '\n\n%s' "$_USAGE_CONTEXT_BLOCK")
    fi
  fi
  # Declared-break reachability proof block: turns a declared-breaking verdict from a punt
  # ("verify yourself") into evidence by naming the file that imports the affected package.
  _DECLARED_BREAK_REACH_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os
d = json.loads(os.environ["_BC_PRF"])
r = d.get('declared_break_reachability') or {}
if not r.get('checked'):
    raise SystemExit(0)
paths = r.get('affected_paths') or []
ev = r.get('evidence') or []
# Optional AI behavioral probe result (advisory only — never flips the deterministic verdict).
aba = d.get('ai_behavioral_assessment') or {}
aba_verdict = (aba.get('verdict') or '').strip().lower() if isinstance(aba, dict) else ''
# The two-oracle behavioral grade (differential probe / release-notes reasoning oracle).
# When it committed a CITED, graded verdict we must not also tell the dev to "check it
# yourself" — that is the exact unhelpful punt the grade replaces.
_bg = d.get('behavioral_grade') or {}
_bg_source = (_bg.get('source') or '').strip().lower() if isinstance(_bg, dict) else ''
_bg_cited = _bg_source in ('reasoning', 'probe') and bool(
    (_bg.get('rationale') or '').strip() or (_bg.get('guidance') or '').strip()
    or (_bg.get('evidence') or '').strip())
def _aba_bullet():
    # Render the AI probe outcome as an advisory bullet, clearly labelled as a non-proof judgment.
    rationale = (aba.get('rationale') or '').strip()
    site = (aba.get('call_site') or '').strip()
    conf = (aba.get('confidence') or '').strip().lower()
    behavior = (aba.get('checked_behavior') or '').strip()
    conf_txt = f", {conf} confidence" if conf in ('low', 'medium', 'high') else ''
    site_txt = f" (checked `{site}`)" if site else ''
    head = '🤖 **AI behavioral probe** — reasoned judgment over the release note + your call site; **not executed, not type-checked, not proof**.'
    if aba_verdict == 'affected':
        body = f"**Likely affected{conf_txt}.** {rationale}{site_txt} This strengthens the review signal but is still **not** a confirmed break — please confirm against the release notes before relying on it."
    elif aba_verdict == 'not_affected':
        body = f"**Probe found no reliance{conf_txt}.** {rationale}{site_txt} This is advisory only — the deterministic grade stays **Medium / Review**; please confirm before merging."
    else:
        return None  # uncertain / unknown → fall through to the deterministic punt
    if behavior:
        body += f" Behavior checked: {behavior}."
    return f"- {head} {body}"
lines = ['### Reachability of the declared break']
if r.get('prod_reachable'):
    sk = r.get('surface_kind') or 'unknown'
    surf = r.get('surface_evidence') or []
    named_syms = r.get('named_symbols') or []
    sbp = r.get('surface_by_path') or {}
    prod = [e for e in ev if not e.get('is_test')]
    reason = (d.get('merge_risk') or {}).get('reason') or ''
    by_path = {}
    for e in prod:
        by_path.setdefault(e['path'], e)
    ordered = sorted(by_path.values(), key=lambda e: (e['path'] not in reason, e['path']))
    def _local_for(path):
        return (sbp.get(path) or {}).get('local') or path.split('/')[-1]
    if sk == 'named':
        lines.append('- ⚠️ **Directly on the changed surface.** Your production code calls a symbol the changelog flags as changed:')
        for e in ([x for x in surf if x.get('named')] or surf)[:3]:
            lines.append(f"  - `{_local_for(e['path'])}.{e['symbol']}` at `{e['file']}:{e['line']}`  ·  package `{e['path']}`")
        lines.append('- This is the **strongest exposure signal** — your code touches the exact surface the maintainer changed. The change is *behavioral* (same type signature), so build, tests, and API-diff still cannot confirm it affects you. Graded **Medium / Review**, not a confirmed break.')
    elif sk == 'package':
        lines.append('- ⚠️ **Uses the affected package, but not the named symbol directly.** Your production code calls into the package surface:')
        for e in surf[:3]:
            lines.append(f"  - `{_local_for(e['path'])}.{e['symbol']}` at `{e['file']}:{e['line']}`  ·  package `{e['path']}`")
        nm = (', '.join(f'`{s}`' for s in named_syms[:3])) if named_syms else 'the changed behavior'
        lines.append(f"- The declared change centers on {nm}, which the library runs **internally** (e.g. during scrape / collect / serialize), not via a call you make directly. So whether it affects you depends on your **runtime data and configuration** — build, tests, and API-diff cannot see this.")
    elif sk == 'import_only':
        lines.append('- ℹ️ **Imported, but no exported surface referenced.** Your production code imports the affected package, but we found no call into its exported API (possibly a blank or transitive import):')
        for e in ordered[:3]:
            lines.append(f"  - `{e['path']}` at `{e['file']}:{e['line']}`")
        if _bg_cited:
            lines.append('- **Lower exposure** — the behavioral oracle graded this against the release notes (see the verdict above); behavior can still change behind a blank or transitive import.')
        else:
            lines.append('- **Lower exposure** — but still verify against the release notes, since behavior can change behind a blank or transitive import.')
    else:
        lines.append('- ⚠️ **Import-reachable behavioral change — unconfirmed.** Your production code imports the affected package:')
        for e in ordered[:3]:
            lines.append(f"  - `{e['path']}` at `{e['file']}:{e['line']}`")
        lines.append('- The maintainer declared a **behavioral** break (changed defaults / error or ordering semantics). Build, tests, and API-diff **cannot see** behavioral changes, so we cannot confirm — or rule out — that your usage triggers it. Importing the package is necessary but **not sufficient** to break.')
    _ai_line = _aba_bullet() if aba_verdict in ('affected', 'not_affected') else None
    if _ai_line:
        lines.append(_ai_line)
    elif _bg_cited:
        # The behavioral oracle already committed a graded, cited verdict (rendered in
        # the headline above). Point to it instead of the generic "check it yourself".
        _g = (_bg.get('grade') or 'medium').strip().lower()
        _label = {'none': 'None', 'low': 'Low', 'medium': 'Medium', 'high': 'High'}.get(_g, 'Medium')
        _guid = (_bg.get('guidance') or '').strip()
        _tail = f' {_guid}' if _guid else ''
        lines.append(f"- The behavioral oracle assessed this against the release notes and your call site and committed **Breakability: {_label}** with cited reasoning (see the verdict above) — this is a graded answer, not a \"verify it yourself\".{_tail}")
    else:
        lines.append('- This is a **manual-review signal, not a confirmed break** — graded **Medium / Review**, not High. To settle it: check whether your usage relies on the changed behavior described in the release notes. If it does not, this signal does not block the merge.')
elif r.get('test_only'):
    lines.append('- ℹ️ The declared break is only reachable from **test/CI code**, not production — verdict down-weighted to Medium.')
    for e in ev[:3]:
        lines.append(f"  - `{e['path']}` at `{e['file']}:{e['line']}` (test)")
else:
    lines.append('- ✅ The declared break is in ' + ', '.join(f'`{p}`' for p in paths[:4]) + ' — **your code does not import** ' + ('it' if len(paths) == 1 else 'them') + '. Down-weighted to Medium (not reachable).')
print('\n\n' + '\n'.join(lines))
PYEOF
)
  # Flag: declared BEHAVIORAL break that is import-reachable in production. merge-risk has graded
  # this Medium (review, not High), but a plain "build passes" headline would bury it — so we route
  # it through the REVIEW headline below with softer wording.
  _DECL_BEHAVIORAL_REVIEW=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || echo 0
import json, os
r = (json.loads(os.environ["_BC_PRF"]).get('declared_break_reachability') or {})
print('1' if (r.get('reachability_kind') == 'import' and r.get('prod_reachable')) else '0')
PYEOF
)
  _DECL_BEHAVIORAL_REVIEW=${_DECL_BEHAVIORAL_REVIEW:-0}
  # Build transitive dep note — with threshold warning for high counts
  _TRANSITIVE_NOTE=""
  if [[ -n "$GOSUM_NEW_COUNT" && "$GOSUM_NEW_COUNT" -gt 0 ]]; then
    _GOSUM_CONTEXT=""
    _GOSUM_NAMES_NOTE=""
    if [[ -n "$GOSUM_NEW_NAMES" ]]; then
      _GOSUM_NAMES_NOTE=": ${GOSUM_NEW_NAMES}"
    fi
    if [[ "$GOSUM_NEW_COUNT" -gt 20 ]]; then
      _TRANSITIVE_NOTE="
- ⚠️ go.sum: **$GOSUM_NEW_COUNT new transitive deps**${_GOSUM_NAMES_NOTE}${_GOSUM_CONTEXT} — high count, review for supply-chain risk"
    else
      _TRANSITIVE_NOTE="
- ℹ️ go.sum: $GOSUM_NEW_COUNT new transitive deps${_GOSUM_NAMES_NOTE}${_GOSUM_CONTEXT}"
    fi
  fi
  # Build govulncheck note (inline checklist item) + top-of-comment header badge.
  # CVE reachability is advisory only; break-reachability (API calls) drives merge risk.
  # V9.7b: distinguish NEW findings (this PR introduces) from pre-existing on main
  _VULN_NOTE=""
  _VULN_HEADER_BADGE=""
  _PRE_NOTE=""
  [[ "${VULN_PREEXISTING_COUNT:-0}" -gt 0 ]] && _PRE_NOTE=" (+ ${VULN_PREEXISTING_COUNT} pre-existing on main)"
  case "$VULN_STATUS" in
    ok)
      _VULN_NOTE="
- ✅ govulncheck: no known vulnerabilities (all modules scanned)"
      ;;
    ok_preexisting)
      # PR scan found vulns, but ALL were already on main — PR introduces none.
      _VULN_NOTE="
- ✅ govulncheck: PR introduces **no new vulnerabilities** (${VULN_PREEXISTING_COUNT} pre-existing on main — unaffected by this PR; CVE reachability is hint-only)"
      ;;
    vulns_found)
      _VULN_NOTE="
- 🚨 Heads-up: CVE reachability (hint only): govulncheck found **${VULN_NEW_COUNT} NEW vulnerability(ies) introduced by this PR** — ${VULN_NEW_LIST}${_PRE_NOTE}"
      _VULN_HEADER_BADGE="> 🚨 **Security:** This PR introduces **${VULN_NEW_COUNT} new vulnerability(ies)** not present on main: ${VULN_NEW_LIST}${_PRE_NOTE}. **Review before merge.**
"
      ;;
    failed_oom)
      _VULN_NOTE="
- ⚠️ govulncheck crashed (out-of-memory) — **vuln scan incomplete for this PR**"
      _VULN_HEADER_BADGE="> ⚠️ **govulncheck crashed (OOM)** — vulnerability scan did NOT complete for this PR. Do not treat absence of findings as safe.
"
      ;;
    failed_timeout)
      _VULN_NOTE="
- ⚠️ govulncheck timed out (>180s per module) — **vuln scan incomplete**"
      _VULN_HEADER_BADGE="> ⚠️ **govulncheck timed out** — vulnerability scan did NOT complete. Do not treat absence of findings as safe.
"
      ;;
    failed_error)
      _VULN_NOTE="
- ⚠️ govulncheck failed (unexpected error) — **vuln scan incomplete**"
      _VULN_HEADER_BADGE="> ⚠️ **govulncheck failed** (unexpected error) — vulnerability scan did NOT complete.
"
      ;;
    not_installed)
      _VULN_NOTE="
- ℹ️ govulncheck not installed — vuln scan skipped"
      ;;
    skipped_disabled)
      if [[ ( "${CVE_COUNT:-0}" =~ ^[0-9]+$ && "${CVE_COUNT:-0}" -gt 0 ) || -n "${_FIXES_CVE_DATA:-}" ]]; then
        # This PR touches CVEs but govulncheck (call-graph reachability) did NOT run, so
        # the reachability section below is import-level only — NOT a per-CVE call-chain.
        # Be explicit so a dev doesn't read absence-of-call-chain as "not reachable".
        _VULN_NOTE="
- ⚠️ **CVE reachability NOT computed for this PR.** govulncheck (call-graph reachability on _our_ source) is disabled by config; the CVE list comes from **Dependabot**, which matches advisory version-ranges only — it does NOT prove the vulnerable symbol is reachable from our code, nor detect NEW CVEs the target version may regress in.
  - To get a per-CVE call-chain proof, re-run with \`BREAKABILITY_GOVULNCHECK=1\`.
  - <!-- TODO(AI-LAYER): rank which of these CVEs are actually reachable from our call-graph and whether merging this PR delivers the fix; this is decision-support govulncheck/Dependabot alone cannot synthesize. --> Until then, treat the CVE list as advisory (version-match), not reachability-confirmed."
      else
        _VULN_NOTE="
- ℹ️ govulncheck: disabled by config — CVE list sourced from Dependabot alerts (govulncheck is hint-only; not a merge gate)"
      fi
      ;;
    *)
      _VULN_NOTE="
- ℹ️ govulncheck: status unknown"
      ;;
  esac
  # Suppress advisory disclaimer when security risk is flagged
  [[ -n "$_VULN_HEADER_BADGE" ]] && ADVISORY_FOOTER=""
  # Build Go dependency-resolution evidence blocks
  _DEP_RESOLUTION_LINE="- ✅ Dependency resolved — \`go get\`/\`npm install\` exit 0"
  _GO_RESOLUTION_BLOCK=""
  if [[ "$ECOSYSTEM" == "gomod" ]]; then
    _GO_RESOLUTION_PARSE=$(echo "$PR_FIELDS" | python3 -c "
import json, sys, re
d=json.load(sys.stdin)
gr=d.get('go_resolution') or {}
cmd=gr.get('command','')
out=gr.get('output_tail','') or ''
diff=gr.get('modsum_diff','') or ''
if cmd:
    print('CMD=' + cmd)
adds=[]; rems=[]
for line in diff.splitlines():
    if not line or line[0] not in '+-':
        continue
    m=re.match(r'^[+-]\s*(?:require\s+)?([A-Za-z0-9_.:/@-]+)\s+(v?\d[^\s]*)', line)
    if not m:
        continue
    (adds if line[0]=='+' else rems).append(m.group(1)+' '+m.group(2))
changed=[]
rem_by={x.split()[0]:x.split()[1] for x in rems}
add_by={x.split()[0]:x.split()[1] for x in adds}
for name in sorted(set(rem_by)&set(add_by)):
    if rem_by[name] != add_by[name]:
        changed.append(f'{name} {rem_by[name]}→{add_by[name]}')
new=[x for x in adds if x.split()[0] not in rem_by]
removed=[x for x in rems if x.split()[0] not in add_by]
if changed or new or removed:
    print('SUMMARY=' + f'{len(changed)} changed, {len(new)} new, {len(removed)} removed' + (': ' + '; '.join((changed+new+removed)[:6]) if (changed+new+removed) else ''))
print('---OUT---')
print('\n'.join([l for l in out.splitlines() if l.strip()][-20:]))
print('---DIFF---')
# Drop file-sections for internal breakability tooling (e.g. .github/tools/reachability)
# that build-check mutates during analysis — they are not part of the analyzed module
# and only add confusing noise to the go.mod/go.sum diff shown to developers.
def _filter_internal(diff_text):
    out, skip = [], False
    for ln in diff_text.splitlines():
        if ln.startswith('diff --git '):
            skip = '.github/tools/' in ln
        if skip:
            continue
        out.append(ln)
    return '\n'.join(out)
print('\n'.join(_filter_internal(diff).splitlines()[:160]))
" 2>/dev/null || true)
    _GO_RES_CMD=$(echo "$_GO_RESOLUTION_PARSE" | grep '^CMD=' | head -1 | cut -d= -f2-)
    _GO_RES_SUMMARY=$(echo "$_GO_RESOLUTION_PARSE" | grep '^SUMMARY=' | head -1 | cut -d= -f2-)
    _GO_RES_OUT=$(echo "$_GO_RESOLUTION_PARSE" | sed -n '/^---OUT---$/,/^---DIFF---$/p' | sed '1d;$d')
    _GO_RES_DIFF=$(echo "$_GO_RESOLUTION_PARSE" | sed -n '/^---DIFF---$/,$p' | tail -n +2)
    if [[ -n "$_GO_RES_CMD" ]]; then
      _DEP_RESOLUTION_LINE="- ✅ Dependency resolved — \`$_GO_RES_CMD\`"
      [[ -n "$_GO_RES_SUMMARY" ]] && _DEP_RESOLUTION_LINE="${_DEP_RESOLUTION_LINE} — go.mod/go.sum: ${_GO_RES_SUMMARY}"
    fi
    if [[ -n "$_GO_RES_OUT" ]]; then
      _GO_RESOLUTION_BLOCK="
<details><summary>📦 Go dependency-resolution output</summary>

\`\`\`
${_GO_RES_OUT}
\`\`\`
</details>"
    fi
    if [[ -n "$_GO_RES_DIFF" ]]; then
      _GO_RESOLUTION_BLOCK="${_GO_RESOLUTION_BLOCK}
<details><summary>🧾 go.mod / go.sum diff</summary>

\`\`\`diff
${_GO_RES_DIFF}
\`\`\`
</details>"
    fi
  fi

  _NO_TEST_CONFIDENCE_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
d=json.loads(os.environ["_BC_PRF"])
nt=d.get('no_test_confidence') or {}
if not nt.get('applies'):
    sys.exit(0)
b=nt.get('basis') or {}
print('### Confidence without tests')
print(f'Derived confidence: **{nt.get("confidence","unknown")}** (no Go test files were present).')
print(f'- API diff changes: `{b.get("api_changes", 0)}`')
print(f'- BREAK-reachability signals (changed API symbols your code calls/accesses): `{b.get("usage_signals", 0)}`')
print(f'- Semver bump: `{b.get("semver_bump", "?")}` · dep type: `{b.get("dep_type", "?")}`')
print(f'- **Residual risk:** {nt.get("residual_risk","Runtime behavior is not covered by tests.")}')
PYEOF
)

  _API_DIFF_TOOL_BLOCK=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
d=json.loads(os.environ["_BC_PRF"])
tool=((d.get('deterministic') or {}).get('api_diff_tool') or {})
if not tool:
    sys.exit(0)
status=tool.get('status')
print('\n\n### API diff signal')
if status == 'semantic':
    print(f'- ✅ Go apidiff ran in **{tool.get("mode","module")} mode** using `{tool.get("package","golang.org/x/exp/cmd/apidiff")}@{tool.get("version","unknown")}`')
    if tool.get('command'):
        print(f'- Command: `{tool.get("command")}`')
elif status == 'structural_fallback':
    print(f'- ⚠️ Go apidiff was unavailable; structural fallback ran instead.')
    if tool.get('warning'):
        print(f'- Reason: {tool.get("warning")}')
    print('- Coverage note: fallback is evidence, but may miss subpackage/type-compatibility breaks that module-mode apidiff would catch.')
PYEOF
)

  # Build build-stdout evidence block
  _BUILD_STDOUT_BLOCK=""
  _BUILD_STDOUT_SNIPPET=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tail = d.get('output_tail', '')
# Show last 8 non-empty lines of build output as evidence
lines = [l for l in tail.splitlines() if l.strip()][-8:]
if lines:
    print('\n'.join(lines))
" 2>/dev/null || true)
  if [[ -n "$_BUILD_STDOUT_SNIPPET" ]]; then
    _BUILD_STDOUT_BLOCK="
<details><summary>🖥️ Build output (last lines)</summary>

\`\`\`
${_BUILD_STDOUT_SNIPPET}
\`\`\`
</details>"
  fi
  # Build TEST-stdout evidence block + parse a trustworthy test summary.
  # Reviewer P1: "Tests pass (exit=0)" with no test names/count is NOT evidence.
  # We surface the actual `go test` / pytest stdout AND derive a count line.
  _TEST_STDOUT_BLOCK=""
  _EV_TEST=""        # inline summary appended to the "Tests pass" checklist line
  _TEST_PARSE=$(echo "$PR_FIELDS" | python3 -c "
import json, sys, re
d = json.load(sys.stdin)
tail = d.get('test_output_tail', '') or ''
lines = [l for l in tail.splitlines() if l.strip()]
# Go:  'ok   pkg/path   0.123s'  /  '--- PASS: TestFoo'  /  'FAIL'
# Py:  '5 passed, 1 warning in 0.42s'  / '=== 3 passed ==='
ok_pkgs   = [l for l in lines if re.match(r'^ok\s+\S', l)]
no_test   = [l for l in lines if 'no test files' in l.lower()]
pass_cnt  = sum(1 for l in lines if l.startswith('--- PASS'))
fail_cnt  = sum(1 for l in lines if l.startswith('--- FAIL') or l.strip()=='FAIL' or l.startswith('FAIL'))
# pytest-style summary line
py_sum    = next((l.strip() for l in reversed(lines) if re.search(r'\d+\s+(passed|failed|error)', l)), '')
parts = []
if ok_pkgs:  parts.append(f'{len(ok_pkgs)} package(s) ok')
if pass_cnt: parts.append(f'{pass_cnt} test(s) PASS')
if fail_cnt: parts.append(f'{fail_cnt} FAIL')
if py_sum:   parts.append(py_sum)
summary = '; '.join(parts)
# If every package reports 'no test files', say so honestly.
only_no_tests = no_test and not ok_pkgs and not pass_cnt
print('SUMMARY=' + summary)
print('ONLY_NO_TESTS=' + ('1' if only_no_tests else '0'))
# emit last 12 non-empty lines for the details block
print('---TAIL---')
print('\n'.join(lines[-12:]))
" 2>/dev/null || true)
  _TEST_SUMMARY_LINE=$(echo "$_TEST_PARSE" | grep '^SUMMARY=' | head -1 | cut -d= -f2-)
  _TEST_ONLY_NO_TESTS=$(echo "$_TEST_PARSE" | grep '^ONLY_NO_TESTS=' | head -1 | cut -d= -f2-)
  _TEST_TAIL=$(echo "$_TEST_PARSE" | sed -n '/^---TAIL---$/,$p' | tail -n +2)
  if [[ -n "$_TEST_SUMMARY_LINE" ]]; then
    _EV_TEST=" — $_TEST_SUMMARY_LINE"
  elif [[ "$_TEST_ONLY_NO_TESTS" == "1" ]]; then
    _EV_TEST=" — ⚠️ no test files in affected packages (exit 0 ≠ tests ran)"
  fi
  if [[ -n "$_TEST_TAIL" ]]; then
    _TEST_STDOUT_BLOCK="
<details><summary>🧪 Test output (last lines)</summary>

\`\`\`
${_TEST_TAIL}
\`\`\`
</details>"
  fi
  # Build inline evidence strings for checklist items
  _EV_BUILD=""
  [[ -n "$BUILD_EVIDENCE" ]] && _EV_BUILD=" — \`$BUILD_EVIDENCE\`"
  [[ -n "$BUILD_DIRS" && -z "$_EV_BUILD" ]] && _EV_BUILD=" — \`$BUILD_DIRS\`"

  HOW_CHECKED=""
  case "$VER_LABEL" in
    L4*)
      HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ✅ Tests pass (exit=$TEST_EXIT_CODE)${_EV_TEST} — no regressions vs main
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_TEST_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
      ;;
    L3*)
      HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ⬜ Tests not configured or not run
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
      ;;
    L2*)
      # V9.3 FIX (P1-2): BUILD_FAILS PRs must NOT use the "builds clean" checklist.
      # Split on verdict: fail gets a failure-specific checklist, pass/pre_existing gets the original.
      if [[ "$VERDICT" == "fail" || "$VERDICT" == "pre_existing_plus_new" ]]; then
        # Build failed — show failure-specific checklist
        if [[ "$OOM_OVERRIDE" == "True" ]]; then
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ⚙️ Build hit OOM (\`signal: killed\`) on unrelated sub-packages — not caused by this upgrade
- ✅ PR's targeted packages are not affected
- ✅ No new type errors introduced vs. main
</details>"
        else
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ❌ Project build fails on PR branch
- ✅ Build passes on main — errors are introduced by this upgrade
- ⬜ Tests not run (build must pass first)
</details>"
        fi
      else
        # Build passed — original L2 checklist
        TEST_EXIT_RAW=$(echo "$PR_FIELDS" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('test_exit',-1))" 2>/dev/null || echo "-1")
        TEST_RAN_RAW=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('test_ran',False))" 2>/dev/null || echo "False")
        if [[ "$TEST_RAN_RAW" == "True" && "$TEST_EXIT_RAW" != "0" && "$TEST_EXIT_RAW" != "-1" ]]; then
          _TEST_DETAIL_NOTE=""
          if [[ -n "$TEST_FAIL_DETAIL" ]]; then
            _TEST_DETAIL_NOTE=" ($TEST_FAIL_DETAIL)"
          fi
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ${_TEST_HOWCHECKED:-⚙️ Automated tests fail${_TEST_DETAIL_NOTE} — see test output}
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
        else
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ✅ Build passes${_EV_BUILD} — exit 0, $NEW_ERR_COUNT new error(s)
- ⬜ Tests not configured or not run
- ✅ Diffed error output: PR introduces 0 new diagnostics${_TRANSITIVE_NOTE}${_VULN_NOTE}
</details>${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}${_FILES_DETAIL_BLOCK}${_GO_RESOLUTION_BLOCK}${_BUILD_STDOUT_BLOCK}${_NO_TEST_CONFIDENCE_BLOCK}${CHANGELOG_LINK}"
        fi
      fi
      ;;
    L1*)
      # V8 FIX (C3): L1 comments must include WHAT failed and WHERE, not just
      # "Build verification limited". Extract module and error excerpt from build output.
      _L1_MAIN_EXIT=$(echo "$PR_FIELDS" | python3 -c "import json,sys; print(json.load(sys.stdin).get('main_exit',-1))" 2>/dev/null || echo "-1")
      _L1_MAIN_CLASS=""
      case "$_L1_MAIN_EXIT" in
        124) _L1_MAIN_CLASS=" (timeout)" ;;
        137) _L1_MAIN_CLASS=" (OOM/killed)" ;;
      esac
      # V9.3: Enhanced excerpt + module attribution for OOM errors.
      # Identifies which sub-packages had errors and whether they're related to the PR's package.
      _L1_EXCERPT_AND_ATTR=$(echo "$PR_FIELDS" | _BC_PKG="$PKG" python3 -c "
import json, sys, os, re
d = json.load(sys.stdin)
tail = d.get('output_tail', '')
pkg = os.environ.get('_BC_PKG', '')
error_class = d.get('error_class', '')
# Extract error/fail lines for context
lines = [l.strip() for l in tail.splitlines()
         if any(k in l.lower() for k in ('error', 'fail', 'cannot', 'undefined', 'fatal', 'signal: kill', 'killed'))][:6]
if not lines:
    non_empty = [l.strip() for l in tail.splitlines() if l.strip()][-4:]
    lines = non_empty
# Extract OOM-killed package names for attribution
killed_pkgs = []
for line in tail.splitlines():
    if 'signal: killed' in line.lower() or 'signal: kill' in line.lower():
        m = re.match(r'^(\S+?):\s', line)
        if m:
            killed_pkgs.append(m.group(1))
# Build attribution note
attr = ''
if killed_pkgs and error_class == 'resource_exhaustion':
    short_names = [p.split('/')[-1] if '/' in p else p for p in killed_pkgs[:3]]
    attr = 'OOM_PKGS=' + ','.join(killed_pkgs[:3])
    # Check if any killed package relates to PR's package
    related = any(pkg.lower() in kp.lower() for kp in killed_pkgs) if pkg else False
    if not related:
        attr += '|UNRELATED'
    else:
        attr += '|RELATED'
# Output: first line is attribution metadata, rest is excerpt
print(attr)
print('---EXCERPT---')
print('\n'.join(lines) if lines else '')
" 2>/dev/null || echo "")
      _L1_ATTR=$(echo "$_L1_EXCERPT_AND_ATTR" | head -1)
      _L1_EXCERPT=$(echo "$_L1_EXCERPT_AND_ATTR" | sed '1,/^---EXCERPT---$/d')
      _L1_EXCERPT_BLOCK=""
      if [[ -n "$_L1_EXCERPT" ]]; then
        _L1_EXCERPT_BLOCK="
\`\`\`
${_L1_EXCERPT}
\`\`\`"
      fi
      _L1_MODULE_NOTE=""
      if [[ -n "$PKG_DIR" && "$PKG_DIR" != "/" ]]; then
        _L1_MODULE_NOTE="
- PR targets module: \`$PKG_DIR\`"
      fi
      # V9.3: Add OOM attribution note if errors are in unrelated sub-packages
      _L1_OOM_NOTE=""
      if echo "$_L1_ATTR" | grep -q "OOM_PKGS="; then
        _OOM_PKG_NAMES=$(echo "$_L1_ATTR" | sed 's/OOM_PKGS=//;s/|.*//')
        _OOM_PKG_SHORT=$(echo "$_OOM_PKG_NAMES" | tr ',' '\n' | while read -r p; do echo "$p" | rev | cut -d/ -f1 | rev; done | tr '\n' ',' | sed 's/,$//')
        if echo "$_L1_ATTR" | grep -q "UNRELATED"; then
          _L1_OOM_NOTE="
- ⚙️ Pre-existing failure is in \`${_OOM_PKG_SHORT}\` — **unrelated** to this PR's package (\`$PKG\`)"
        fi
      fi
      HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

${_DEP_RESOLUTION_LINE}
- ⚠️ Build fails on both \`main\` (exit=${_L1_MAIN_EXIT}${_L1_MAIN_CLASS}) and PR branch — same errors${_L1_MODULE_NOTE}${_L1_OOM_NOTE}
- ✅ No NEW errors introduced by this upgrade

**Pre-existing build errors:**${_L1_EXCERPT_BLOCK}

Fix these on \`main\` to unlock full L2+ verification.
</details>"
      ;;
    *)
      if [[ -n "$VER_LABEL" ]]; then
        # End-user feedback: "Limited verification performed" is misleading when the
        # tool DID compare builds and found zero new errors. Show what we actually did.
        if [[ "$VERDICT" == "pre_existing" && "$NEW_ERR_COUNT" -eq 0 ]]; then
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

- ⚙️ Built both \`main\` and PR branch
- ✅ Compared build errors — **zero new errors** from this upgrade
- ⚠️ Baseline build has pre-existing failures (not caused by upgrade)
</details>"
        else
          HOW_CHECKED="
<details><summary>🔍 How we checked (verification: $VER_LABEL)</summary>

- ⬜ Build verification limited by infrastructure issues
</details>"
        fi
      fi
      ;;
  esac

  if [[ "$ECOSYSTEM" == "gomod" && -n "$HOW_CHECKED" ]]; then
    case "$HOW_CHECKED" in
      *"Go dependency-resolution output"*|*"go.mod / go.sum diff"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_GO_RESOLUTION_BLOCK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"Confidence without tests"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_NO_TEST_CONFIDENCE_BLOCK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"API diff signal"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_API_DIFF_TOOL_BLOCK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"Changelog signals"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${CHANGELOG_LINK}" ;;
    esac
    case "$HOW_CHECKED" in
      *"BREAK-reachability context"*) ;;
      *) HOW_CHECKED="${HOW_CHECKED}${_USAGE_CONTEXT_BLOCK}${_DECLARED_BREAK_REACH_BLOCK}" ;;
    esac
  fi

  # Avoid double-rendering the changelog: the gomod HOW_CHECKED enrichment (above) may already
  # embed the "### Changelog signals" block inside the "How we checked" details. Templates that
  # ALSO insert ${CHANGELOG_LINK} inline must use ${CHANGELOG_INLINE} so the inline copy is
  # suppressed when HOW_CHECKED already carries it (non-gomod keeps the inline copy).
  CHANGELOG_INLINE="$CHANGELOG_LINK"
  case "$HOW_CHECKED" in
    *"Changelog signals"*) CHANGELOG_INLINE="" ;;
  esac

  # Prepend govulncheck header badge (if status is failure/vulns_found) so it sits
  # right above the HOW_CHECKED collapsible — visible without expanding details.
  if [[ -n "$_VULN_HEADER_BADGE" && -n "$HOW_CHECKED" ]]; then
    HOW_CHECKED="
${_VULN_HEADER_BADGE}${HOW_CHECKED}"
  fi

  # Excerpt of build output (first 10 lines of errors for context)
  BUILD_EXCERPT=$(echo "$PR_FIELDS" | python3 -c "
import json, sys
d = json.load(sys.stdin)
tail = d.get('output_tail', '')
lines = [l for l in tail.splitlines() if 'error' in l.lower() or 'Error' in l][:8]
if lines:
    print('\n'.join(lines))
else:
    print(tail[:300] if tail else '')
" 2>/dev/null || echo "")

  # Use the plan issue reserved at the start of this run, so the link always points
  # at THIS run's plan (never a stale previous-run number).
  PLAN_LINE=""
  if [[ -n "${MERGE_PLAN_NUM:-}" ]]; then
    PLAN_LINE="
📋 Merge plan: #$MERGE_PLAN_NUM"
  fi

  # ── Classify and generate comment ─────────────────────────────────────────
  COMMENT=""

  # PRs with breakability:skip label were intentionally excluded from analysis.
  # Don't post any comment — the developer already opted out of analysis.
  if [[ "$VERDICT" == "skipped" ]]; then
    echo "  PR #$PR_NUM has breakability:skip label — skipping fallback comment"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # V8 FIX (C2): Cancelled PRs already have a comment posted above
  if [[ "$VERDICT" == "cancelled" ]]; then
    echo "  PR #$PR_NUM was cancelled — comment already posted"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # V9.8 iter6 A (security verdict gate): a PR that INTRODUCES new vulnerabilities
  # (vuln_status=vulns_found with non-empty vuln_new_findings) must NEVER be SAFE,
  # regardless of build status. Demote pass → vulns_introduced so the dispatch
  # chain renders the security-risk comment instead of SAFE.
  # Pre-existing-only vulns (ok_preexisting) do NOT demote — PR is not at fault.
  if [[ "$VULN_STATUS" == "vulns_found" && "$VULN_NEW_COUNT" -gt 0 ]]; then
    if [[ "$VERDICT" != "vulns_introduced" ]]; then
      echo "  PR #$PR_NUM: demoting VERDICT=$VERDICT → vulns_introduced ($VULN_NEW_COUNT new CVE(s))"
      VERDICT="vulns_introduced"
    fi
  fi

  if [[ "$VERDICT" == "pre_existing" ]]; then
    _GUARD_BUILD_BADGE="⚠️ pre-existing failures (not introduced by this PR)"
  else
    _GUARD_BUILD_BADGE="✅ passes"
  fi
  if [[ ( "$MERGE_RISK_TAG" == "High" || ( "$_DECL_BEHAVIORAL_REVIEW" == "1" && ( "$VERDICT" == "pass" || "$VERDICT" == "pre_existing" ) ) ) && "$VERDICT" != "fail" && "$VERDICT" != "vulns_introduced" && "$VERDICT" != "security_review" && "$VERDICT" != "pre_existing_plus_new" ]]; then
    # FALSE-SAFE GUARD (global): a High merge-risk signal (e.g. a maintainer-declared breaking
    # change) is structurally unverifiable by build/test/apidiff. Pre-empt EVERY green
    # headline below (actions/docker SAFE, pass, pre_existing) — a green build must NOT clear
    # this PR. Hard-fail/security verdicts keep their own stronger BLOCKED messaging.
    # Two grades share this headline: High → "⛔ REVIEW REQUIRED" (confirmed/unverifiable break);
    # Medium import-reachable behavioral declaration → "⚠️ REVIEW SUGGESTED" (not a confirmed break,
    # but a green build must not silently say "merge when ready").
    if [[ "$MERGE_RISK_TAG" == "High" ]]; then
      _REVIEW_TITLE="⛔ REVIEW REQUIRED"
      _REVIEW_WHY="Build and tests pass on the PR branch, but that does **not** clear this upgrade: ${MERGE_RISK_REASON}. Behavioral changes (changed defaults, error/ordering semantics) and breaks in sibling or transitive modules are invisible to compilation and to existing tests.${_REVIEW_WHY_TAIL}"
    else
      _REVIEW_TITLE="⚠️ REVIEW SUGGESTED"
      _REVIEW_WHY="Build and tests pass on the PR branch — but the maintainer **declares a behavioral breaking change** and your code imports the affected package. Behavioral changes (changed defaults, error/ordering semantics) are invisible to compilation, tests, and API-diff, so this is a **review signal, not a confirmed break**. The behavioral verdict below grades your actual exposure, and the exact import site is in the reachability block."
    fi
    COMMENT="<!-- breakability-check -->
## ${_REVIEW_TITLE} — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ${_GUARD_BUILD_BADGE} · Verification: **${VER_LABEL:-L2}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

${MERGE_RISK_LINE}

### Why a passing build is not enough here
${_REVIEW_WHY}${CHANGELOG_INLINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ ( "$ECOSYSTEM" == "actions" || "$ECOSYSTEM" == "docker" ) && -n "$_CI_TIER" ]]; then
    # CI dependency that is NOT auto-safe: security-sensitive (secsens) or a non-sensitive major.
    if [[ "$_CI_TIER" == "secsens" ]]; then
      _CI_REVIEW_TITLE="🔐 REVIEW — supply-chain sensitive"
      _CI_REVIEW_WHY="This CI dependency handles **tokens, credentials, registry/cloud auth, code signing, or deployment/publishing**. A breaking change or a compromised release here is a **supply-chain risk** — the class of dependency an attacker most wants merged unread. \"CI-only\" does **not** mean \"safe\" here."
      _CI_REVIEW_CHECK="- **Pin to a full commit SHA** (not a moving tag) so a re-tagged release can't silently change what runs.
- Review the **release notes / changelog** for changed **permissions**, token scopes, or inputs.
- Confirm the publisher and that the new version is the official release."
      _CI_FOOT="CI dependency flagged supply-chain sensitive; not auto-cleared"
    else
      _CI_REVIEW_TITLE="🟡 REVIEW — major CI action bump"
      _CI_REVIEW_WHY="This is a **major** version bump of a CI action. Major bumps routinely change inputs, runtime defaults, or output names and can **break your workflow** — even though no application code is affected. This is a **breakability glance, not a security flag**."
      _CI_REVIEW_CHECK="- Skim the **release notes / changelog** for breaking input/output or runtime changes.
- Optionally pin to a full commit SHA."
      _CI_FOOT="major CI action bump; quick changelog review suggested"
    fi
    COMMENT="<!-- breakability-check -->
## ${_CI_REVIEW_TITLE} — \`$PKG\` $FROM → $TO · dev (CI) · $BUMP_DISPLAY

${_CI_REVIEW_WHY}

### What to check before merging
${_CI_REVIEW_CHECK}${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — ${_CI_FOOT}*"

  elif [[ "$ECOSYSTEM" == "actions" ]]; then
    # GitHub Actions — always safe, no app code affected.
    # No L0/fallback labels — CI-only changes need no build verification (end-user feedback 2.4).
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · dev (CI) · $BUMP_DISPLAY

GitHub Actions workflow dependency. No application code affected. No build verification needed.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — CI-only change, no build impact*"

  elif [[ "$ECOSYSTEM" == "docker" && "$BUMP" != "major" ]]; then
    # Docker non-major — typically safe
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · production · $BUMP_DISPLAY

Docker base image $BUMP_DISPLAY bump. No application source changes.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" && "$OOM_OVERRIDE" == "True" ]]; then
    # V9.3: OOM override — build was killed by OOM on unrelated sub-packages.
    # The PR's own targeted packages were not affected.
    _OOM_PKG_NOTE=""
    if [[ -n "$OOM_PACKAGES" ]]; then
      _OOM_PKG_LIST=$(echo "$OOM_PACKAGES" | tr ',' '\n' | sed 's/^/  - /' | head -5)
      _OOM_PKG_NOTE="

OOM-killed sub-packages (unrelated to this upgrade):
\`\`\`
${_OOM_PKG_LIST}
\`\`\`"
    fi
    _DEV_DEP_NOTE=""
    if [[ "$FILES_COUNT" -eq 0 ]]; then
      _DEV_DEP_NOTE=" · ⚙️ 0 direct imports (dev/indirect dependency)"
    fi
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ infra OOM on unrelated sub-packages — not caused by this upgrade · Verification: **${VER_LABEL:-L2}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}${_DEV_DEP_NOTE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
The CI runner ran out of memory (\`signal: killed\`) building sub-packages unrelated to \`$PKG\`. This PR's targeted packages are not affected. The same OOM occurs on \`main\` — it is an infrastructure limitation, not a code regression.${_OOM_PKG_NOTE}

**Recommendation:** Safe to merge. The OOM is a CI runner memory issue, not caused by this $BUMP_DISPLAY bump.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" && "$BUMP" == "patch" && "$FILES_COUNT" -lt 5 ]]; then
    # Patch bump, build passes, low usage surface — simple safe
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · patch

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}

$BUMP_DISPLAY bump with passing build. No new type errors introduced.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" && "$DEP_REL" == "transitive" ]]; then
    # Transitive dep, build passes
    COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · transitive · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}**

Transitive dependency — your code does not import it directly. Build passes.${CVE_LINE}${FIXES_CVE_LINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pass" ]]; then
    # Build passes — general case
    NEW_ERR_NOTE=""
    if [[ "$NEW_ERR_COUNT" -gt 0 ]]; then
      NEW_ERR_NOTE=" · ⚠️ $NEW_ERR_COUNT new error(s) found"
    fi
    COMMENT="<!-- breakability-check -->
## 🔍 BUILD ANALYSIS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}$NEW_ERR_NOTE${CVE_LINE}${FIXES_CVE_LINE}

### Summary (deterministic analysis)
- Package: \`$PKG\` $FROM → $TO ($BUMP_DISPLAY bump)
- Type: $DEP_TYPE / $DEP_REL
- Build passes on PR branch
- New type errors: $NEW_ERR_COUNT

**Recommendation:** Review changelog for $BUMP_DISPLAY bump breaking changes. Build passes — merge when ready.${CHANGELOG_INLINE}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "fail" ]]; then
    # Build fails — most important fallback to get right
    EXCERPT_BLOCK=""
    if [[ -n "$BUILD_EXCERPT" ]]; then
      EXCERPT_BLOCK="
\`\`\`
${BUILD_EXCERPT}
\`\`\`"
    fi
    # P0 (reviewer): a BUILD_FAILS verdict on a PR where NO source file imports the
    # upgraded dependency (Usage: 0 file(s)) is suspicious. Generic toolchain errors
    # like "no import data available" are NOT attributable to this upgrade. Flag the
    # likely false-positive instead of confidently telling the dev "do not merge".
    _FALSE_POSITIVE_NOTE=""
    _GENERIC_TOOLCHAIN_ERR=0
    if echo "$BUILD_EXCERPT" | grep -qiE "no import data available|no required module provides|missing go\.sum entry|cannot find module"; then
      _GENERIC_TOOLCHAIN_ERR=1
    fi
    if [[ "$FILES_COUNT" == "0" ]]; then
      if [[ "$_GENERIC_TOOLCHAIN_ERR" == "1" ]]; then
        _FALSE_POSITIVE_NOTE="

> ⚠️ **Likely false positive.** No source file in this repo imports \`$PKG\`, and the failure above is a generic Go toolchain message (not a type/compile error attributable to this upgrade). This is most likely a build-cache/module-resolution artifact, **not** a real break caused by the bump. Verify locally with \`go build ./...\` on a clean checkout before treating this as blocking."
      else
        _FALSE_POSITIVE_NOTE="

> ⚠️ **Note:** No source file directly imports \`$PKG\` (Usage: 0 file(s)). If the error below is in an unrelated package, this failure may not be caused by this upgrade — confirm the failing package is actually affected before blocking the merge."
      fi
    fi
    COMMENT="<!-- breakability-check -->
## ❌ BUILD_FAILS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ❌ fails on PR branch, ✅ passes on main · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${CVE_LINE}${FIXES_CVE_LINE}${_FALSE_POSITIVE_NOTE}

### Build errors (excerpt)$EXCERPT_BLOCK

### What to do
1. Check the full build output in the Actions run for this PR
2. Review the \`$PKG\` $FROM → $TO changelog for breaking changes${CHANGELOG_INLINE}
3. Fix type errors or update your code to match the new API
4. Re-run the breakability analysis after your fix

**Do not merge — build is broken.** ($BUMP_DISPLAY bump)${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "pre_existing" ]]; then
    # Pre-existing failures — split on verification level (Finding-3.3, A2-8).
    # L2+ means tsc/go-build actually passed (identical errors = no new problems) → SAFE.
    # L1 means deps resolved but build inconclusive → LIKELY SAFE.
    # L0 means deps didn't even resolve → UNVERIFIED (do NOT say "LIKELY SAFE").
    if [[ "$VER_LABEL" == L2* || "$VER_LABEL" == L3* || "$VER_LABEL" == L4* || "$VER_LABEL" == L5* ]]; then
      COMMENT="<!-- breakability-check -->
## ✅ SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ verified — same result as main baseline, not caused by this change · Verification: **${VER_LABEL}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
The build produces the same errors on both \`main\` and this PR branch. This upgrade does **not** introduce new failures. Verified at **${VER_LABEL}**.

**Recommendation:** Safe to merge. Pre-existing build issues are unrelated to this upgrade.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    elif [[ "$VER_LABEL" == L1* ]]; then
      # L1: dependency resolution passed but build/type-check inconclusive
      COMMENT="<!-- breakability-check -->
## ⚙️ LIKELY SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚙️ same errors on main and PR branch — pre-existing failure, **not caused by this upgrade** · Verification: **${VER_LABEL}**${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
Dependencies resolved successfully. The build fails on both \`main\` and this PR with the same errors. This upgrade does **not** introduce new failures. Full build verification was limited by pre-existing issues on \`main\`.

**Recommendation:** Likely safe to merge — no new errors detected. Fix pre-existing build failures on \`main\` for full verification coverage.${_TIMEOUT_CAVEAT}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    else
      # L0: dependency resolution failed or build inconclusive.
      # End-user feedback (P1): "UNVERIFIED" is misleading when the tool DID compare
      # both branches and found zero new errors. That IS a safety signal.
      # Use "LIKELY SAFE" when zero new errors detected, "UNVERIFIED" only when
      # we truly have no signal (e.g., install_ok=false with no comparison done).
      if [[ "$NEW_ERR_COUNT" -eq 0 ]]; then
        COMMENT="<!-- breakability-check -->
## ⚙️ LIKELY SAFE — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚙️ same errors on \`main\` and PR branch — **not caused by this upgrade** · Verification: **${VER_LABEL:-L0}**${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What this means
Both \`main\` and this PR branch produce the same build errors. This upgrade does **not** introduce new failures. Build verification was limited by pre-existing infrastructure issues.

**Recommendation:** Likely safe to merge — zero new errors detected. Fix baseline build on \`main\` for full verification.${_TIMEOUT_CAVEAT}${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
      else
        COMMENT="<!-- breakability-check -->
## ⚠️ UNVERIFIED — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚠️ build verification could not complete — infrastructure/configuration errors · Verification: **${VER_LABEL:-L0}**${MODULE_LINE}${CVE_LINE}${FIXES_CVE_LINE}

### What to do
1. Fix the baseline build on \`main\` (see merge plan for error details)
2. Re-run analysis: \`gh workflow run breakability-agent.yml\`

**Recommendation:** Cannot confirm safety. Fix build environment first, then re-analyze.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
      fi
    fi

  elif [[ "$VERDICT" == "pre_existing_plus_new" ]]; then
    # Pre-existing + new errors
    COMMENT="<!-- breakability-check -->
## ❌ BUILD_FAILS — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ❌ new errors introduced by this PR (on top of pre-existing failures)${CVE_LINE}${FIXES_CVE_LINE}

This upgrade introduces **$NEW_ERR_COUNT new error(s)** not present on \`main\`. Fix required before merging.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "security_review" ]]; then
    # Build passes but npm audit found CRITICAL/HIGH vulnerabilities
    COMMENT="<!-- breakability-check -->
## ⚠️ SECURITY REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${CVE_LINE}${FIXES_CVE_LINE}

### Security concern
Build passes, but \`npm audit\` found **critical or high** vulnerabilities in this upgrade. Manual security review recommended before merging.

**Recommendation:** Review the npm audit output and CVE details. If vulnerabilities are in transitive deps not used by your code, merge may still be safe.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "vulns_introduced" ]]; then
    # V9.8 iter6 (A): PR build passes but govulncheck found NEW CVE(s) not on main.
    # This overrides SAFE because a PR that introduces vulnerabilities is never SAFE.
    _VULN_IDS_LIST="${VULN_NEW_LIST:-unknown}"
    # G4: Extract per-finding reachability info from govulncheck output.
    _VULN_REACHABILITY=$(_BC_PRF="$PR_FIELDS" python3 - <<'PYEOF' 2>/dev/null || echo "❓ CVE reachability unknown (hint only) — run \`govulncheck ./...\` locally to check"
import json, os, re
d = json.loads(os.environ["_BC_PRF"])
output = d.get('vuln_output', '') or ''
new_ids = d.get('vuln_new_findings', []) or []
details = d.get('cve_details', []) or []
sev_by_id = {}
for item in details:
    if not isinstance(item, dict):
        continue
    ids = [item.get('cve_id'), item.get('ghsa_id'), item.get('id')]
    sev = (item.get('severity') or item.get('cvss_severity') or '').upper()
    for k in ids:
        if k and sev:
            sev_by_id[k] = sev

def section_of(pos):
    sym = output.rfind('=== Symbol Results ===', 0, pos)
    pkg = output.rfind('=== Package Results ===', 0, pos)
    mod = output.rfind('=== Module Results ===', 0, pos)
    best = max(sym, pkg, mod)
    if best < 0 or best == sym:
        return 'symbol'
    return 'imported'

def block_for(vid):
    idx = output.find(vid)
    if idx < 0:
        return '', 'unknown'
    nexts = [p for p in [output.find('Vulnerability #', idx + len(vid)), output.find('GO-', idx + len(vid))] if p > idx]
    end = min(nexts) if nexts else len(output)
    return output[idx:end], section_of(idx)

def chain(block):
    vals = []
    for pat in [r'#\d+:\s+[^:]+:\d+:\d+:\s*(.+)', r'\b([\w./-]+\.[\w*]+)\s+calls\s+([\w./-]+\.[\w*]+)']:
        for m in re.findall(pat, block):
            if isinstance(m, tuple):
                vals.append(' → '.join(x for x in m if x))
            else:
                vals.append(m.strip())
    return vals[:4]

lines = []
for vid in new_ids:
    block, sect = block_for(vid)
    aliases = sorted(set(re.findall(r'CVE-\d{4}-\d+|GHSA-[0-9a-z-]+', block, re.I)))
    sev = next((sev_by_id.get(x) for x in [vid]+aliases if sev_by_id.get(x)), 'UNKNOWN')
    cve_label = ', '.join(aliases) if aliases else vid
    lines.append(f'- **{cve_label}** (`{vid}`) · Severity: **{sev}**')
    if sect == 'symbol' and block:
        ch = chain(block)
        if ch:
            lines.append('  - CVE exploitability reachability (hint only): reachable from your code — ' + ' → '.join(f'`{x}`' for x in ch))
        else:
            lines.append('  - CVE exploitability reachability (hint only): reachable from your code (govulncheck Symbol Results; call-chain text not emitted)')
    elif sect == 'imported':
        lines.append('  - CVE exploitability reachability (hint only): no reachable path found in govulncheck Symbol Results; this is not safe-to-ignore evidence')
    else:
        lines.append('  - CVE exploitability reachability (hint only): not determined in govulncheck output; run `govulncheck ./...` locally to confirm')
    lines.append('  - Does merging this PR fix it? **No** — absent on `main`, present on this PR branch (introduced by the upgrade).')
if not lines:
    lines.append('❓ CVE reachability unknown (hint only) — govulncheck produced no per-finding call-graph data; run `govulncheck ./...` locally to check')
print('\n'.join(lines))
PYEOF
)
    # EU-6: Get max severity for the new vulns
    _VULN_SEVERITY_NOTE=""
    if [[ -n "$CVE_MAX_SEVERITY" ]]; then
      _VULN_SEVERITY_NOTE=" · Severity: **${CVE_MAX_SEVERITY}**"
    fi
    COMMENT="<!-- breakability-check -->
## 🚨 SECURITY RISK — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ✅ passes · Verification: **${VER_LABEL:-L1}** · Usage: $FILES_COUNT file(s)${_USAGE_CONTEXT_INLINE}${_VULN_SEVERITY_NOTE}${CVE_LINE}${FIXES_CVE_LINE}

### 🚨 This PR introduces **$VULN_NEW_COUNT NEW vulnerability(ies)** not present on \`main\`

**New CVEs:** $_VULN_IDS_LIST
### Heads-up: CVE reachability (hint only)
> Snyk-style caveat: **no reachable path found** means “not observed in this scan”, not “safe to ignore”.
${_VULN_REACHABILITY}

Pre-existing on main: $VULN_PREEXISTING_COUNT (unaffected by this PR).

**Recommendation:** Do **NOT** merge until these vulnerabilities are addressed. Options:
1. Bump to a later fixed version that patches these CVEs, or
2. Close this PR and wait for an upstream fix, or
3. Treat any "no reachable path found" result as a prioritization hint only, not as permission to ignore the introduced CVE.
${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — govulncheck diffed against \`main\` baseline*"

  elif [[ "$INSTALL_METHOD" == "infra_error" ]]; then
    # Infrastructure blocked analysis
    COMMENT="<!-- breakability-check -->
## 🔍 REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build: ⚠️ blocked by infrastructure error — build verification could not run${CVE_LINE}${FIXES_CVE_LINE}

### What happened
The build check was blocked by an infrastructure issue (private registry, network timeout, or missing dependency not caused by this upgrade). **This is not a build failure from the upgrade.**

**Recommendation:** Verify infrastructure health, then re-run. If infrastructure is healthy, review manually.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  elif [[ "$VERDICT" == "conflict" ]]; then
    # Conflicted PR — cannot merge or analyze until rebased (Finding-3.6)
    COMMENT="<!-- breakability-check -->
## ⚠️ CONFLICTED — \`$PKG\` $FROM → $TO — rebase required

This PR has merge conflicts and cannot be merged or analyzed until rebased.
Run \`@dependabot recreate\` or rebase manually.${PLAN_LINE}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"

  else
    # Catch-all: skip/unknown verdict
    COMMENT="<!-- breakability-check -->
## 🔍 REVIEW — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

Build analysis status: \`$VERDICT\` (verification: ${VER_LABEL:-unknown})${CVE_LINE}${FIXES_CVE_LINE}

Automated build analysis was not conclusive for this PR. Manual review recommended.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
  fi

  # ── CVE version-gating for the SECURITY FIX body ─────────────────────────────
  # The PR-body `cves` field (CVE_LIST/CVE_COUNT) is an UNVERIFIED claim: it does NOT
  # prove the resulting (incl. transitive) version actually reaches the advisory's
  # fixed-in version. Dependabot-matched fixes (fixes_cves -> _FIXES_CVE_DATA) ARE
  # version-gated (build-check.sh bumped_modules + first_patched_version gate in
  # merge-results.sh). Credit only the version-verified set as "resolved"; render the
  # rest as "claimed (not version-verified)" so the per-PR body can never over-credit a
  # CVE the bump does not actually deliver — keeping it consistent with the merge-plan
  # orphan table (e.g. PR#23 CVE-2026-39883 fixed-in 1.43 while the PR reaches only 1.42;
  # PR#10 CVE-2025-30204 with no Dependabot match).
  # Reconcile the SECURITY-FIX recommendation with the committed behavioral grade so the body
  # cannot say "MERGE IMMEDIATELY" while the headline/merge-plan say "REVIEW THEN MERGE" (PR#23).
  # BG_* are available here (eval'd at get_behavioral_grade above); V2_VERDICT is not yet set.
  _BEHAV_BREAK=0
  if [[ "${BG_OK:-0}" == "1" ]]; then
    _bg_src_lc_cve="$(printf '%s' "${BG_SOURCE:-}" | tr '[:upper:]' '[:lower:]')"
    _bg_grade_lc_cve="$(printf '%s' "${BG_GRADE:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ ( "$_bg_src_lc_cve" == "reasoning" || "$_bg_src_lc_cve" == "probe" ) \
          && ( -n "${BG_RATIONALE:-}" || -n "${BG_GUIDANCE:-}" || -n "${BG_EVIDENCE:-}" ) \
          && ( "$_bg_grade_lc_cve" == "high" || "$_bg_grade_lc_cve" == "medium" ) ]]; then
      _BEHAV_BREAK=1
    fi
  fi
  if [[ -n "${_FIXES_CVE_DATA:-}" ]]; then
    _CVE_VERIFIED=1
    _CVE_HEADING="$CVE_COUNT CVE(s) resolved (version-verified): $_FIXES_CVE_DATA"
    if [[ "$_BEHAV_BREAK" == "1" ]]; then
      _CVE_RECOMMEND="**REVIEW THEN MERGE.** It resolves ${CVE_COUNT} version-verified known CVE(s) (the resulting version reaches each advisory's fixed-in version) with zero new build errors — but the behavioral oracle graded a **${_bg_grade_lc_cve}** breaking-change exposure (see the graded call sites below). Confirm those call sites, then merge to clear the CVE."
    elif [[ "$_TESTS_CLEAN" == "1" ]]; then
      _CVE_RECOMMEND="**MERGE NOW.** It resolves ${CVE_COUNT} version-verified known CVE(s) (the resulting version reaches each advisory's fixed-in version), introduces zero new build errors, and the test suite passes."
    elif [[ "$_TEST_FAILED" == "1" && "$_TEST_PREEXIST_VERIFIED" == "1" ]]; then
      _CVE_RECOMMEND="**REVIEW THEN MERGE.** It resolves ${CVE_COUNT} version-verified known CVE(s) with zero new build errors, but the test suite is **also failing on \`main\`** (pre-existing). Confirm the failures are unrelated to this upgrade, then merge to clear the CVE."
    elif [[ "$_TEST_FAILED" == "1" ]]; then
      _CVE_RECOMMEND="**REVIEW THEN MERGE.** It resolves ${CVE_COUNT} version-verified known CVE(s) with zero new build errors, but the test suite is **currently failing** and we could NOT confirm the failures pre-date this PR (\`main\` did not build/test clean). Verify the failures are unrelated before merging."
    else
      _CVE_RECOMMEND="**PRIORITIZE — review then merge.** It resolves ${CVE_COUNT} version-verified known CVE(s) with zero new build errors; the build and type-check pass, but **tests were not run**, so safety is not fully verified. Prioritize it, give it a quick review, then merge to clear the CVE."
    fi
  else
    _CVE_VERIFIED=0
    _CVE_HEADING="$CVE_COUNT CVE(s) claimed by the PR body — ⚠️ NOT version-verified against the resulting (incl. transitive) go.mod/lockfile version: $CVE_LIST"
    _CVE_RECOMMEND="**Merge to clear these advisories — but the fix is NOT version-verified.** Confirm the resulting (incl. transitive) version reaches each advisory's fixed-in version before relying on this as a security fix; merging is still the path to remediate. No new build errors were introduced."
  fi

  # CVE override: when a PR fixes CVEs, escalate the presentation regardless of verdict.
  # CR5-8: Fire for ALL verdicts, not just pass/pre_existing. A HIGH CVE must be visually
  # distinct and recommend immediate merge even when the build has issues. The developer
  # needs to know: "This PR fixes a HIGH CVE but may also need build fixes."
  # End-user feedback: PR #10 (HIGH CVE) was indistinguishable from 27 other PRs.
  if [[ "$CVE_COUNT" -gt 0 && "$CVE_COUNT" != "0" ]]; then
    _SEV_BADGE=""
    if [[ -n "$CVE_MAX_SEVERITY" ]]; then
      _SEV_BADGE=" ($CVE_MAX_SEVERITY)"
    fi
    if [[ "$NEW_ERR_COUNT" -eq 0 ]]; then
      # No new errors — recommend immediate merge regardless of baseline state
      COMMENT="<!-- breakability-check -->
## 🔴 SECURITY FIX${_SEV_BADGE} — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

### ⚠️ $_CVE_HEADING
$(if [[ -n "$CVE_MAX_SEVERITY" ]]; then echo "**Severity: ${CVE_MAX_SEVERITY}** — This PR fixes a known security vulnerability."; fi)

**Build Impact:** No new errors introduced by this upgrade.${MODULE_LINE}
$(if [[ "$VERDICT" == "pre_existing" ]]; then echo "Baseline build has pre-existing failures (not related to this package)."; elif [[ "$VERDICT" == "pass" ]]; then echo "Build passes on PR branch."; else echo "Build status: \`$VERDICT\` — no new errors detected."; fi)

### Heads-up: CVE reachability (hint only)
No reachable path found by a scanner is **not** safe-to-ignore evidence. Patch regardless: this PR resolves the advisory.

### Recommendation
$_CVE_RECOMMEND
Security fixes should be prioritized over routine dependency upgrades.
$(if [[ "$VERDICT" == "pre_existing" && "$VER_LABEL" == L0* ]]; then echo "> If baseline build failures concern you, verify locally before merging. The security fix is independent of the baseline issue."; fi)

Verification: **${VER_LABEL:-L0}**${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    else
      # Has new errors BUT also fixes CVEs — show both facts prominently
      COMMENT="<!-- breakability-check -->
## 🔴 SECURITY FIX (BUILD ISSUES)${_SEV_BADGE} — \`$PKG\` $FROM → $TO · $DEP_TYPE · $BUMP_DISPLAY

### ⚠️ $_CVE_HEADING
$(if [[ -n "$CVE_MAX_SEVERITY" ]]; then echo "**Severity: ${CVE_MAX_SEVERITY}** — This PR fixes a known security vulnerability."; fi)

**Build Impact:** ❌ $NEW_ERR_COUNT new error(s) introduced by this upgrade.${MODULE_LINE}

### Heads-up: CVE reachability (hint only)
No reachable path found by a scanner is **not** safe-to-ignore evidence. Patch regardless once the build break is fixed.

### Recommendation
$(if [[ "$_CVE_VERIFIED" == "1" ]]; then echo "**This PR fixes a version-verified $CVE_MAX_SEVERITY CVE but also introduces build errors.** Fix the build errors, then merge immediately."; else echo "**This PR claims to fix a $CVE_MAX_SEVERITY CVE (not version-verified) and also introduces build errors.** Fix the build errors and confirm the resulting version reaches the advisory's fixed-in version, then merge."; fi)
Do not delay — the security fix is critical.${PLAN_LINE}${HOW_CHECKED}${ADVISORY_FOOTER}
${RUN_LINK}
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*"
    fi
  fi

  if [[ -n "$COMMENT" ]]; then
    COMMENT=$(COMMENT_BODY="$COMMENT" MERGE_RISK_LINE="$MERGE_RISK_LINE" python3 -c '
import os
body = os.environ.get("COMMENT_BODY", "")
risk = os.environ.get("MERGE_RISK_LINE", "").strip()
if risk and "Merge Risk:" not in body:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("## "):
            lines.insert(i + 1, "")
            lines.insert(i + 2, risk)
            body = "\n".join(lines)
            break
print(body)
' 2>/dev/null || printf '%s' "$COMMENT")
    eval "$(get_verdict_v2 "$PR_NUM")"
    eval "$(get_behavioral_grade "$PR_NUM")"
    # Reset per-PR so a prior PR's residual evidence can never leak onto this one.
    _V2_RESIDUAL_BLOCK=""
    # ── Behavioral/oracle confidence, DISTINCT from build-verification tier ──
    # V2_CONF is an L0-L5 build/test verification tier from the verdict mapper. Do not show it
    # as behavioral confidence. Behavioral/oracle confidence comes from behavioral_grade.confidence.
    # BLOCKED -> High; SAFE -> None; REVIEW -> the committed behavioral grade (the
    # differential probe / break-class router) if present, else Medium. This replaces
    # the "review the release notes yourself" punt with a graded answer.
    # Match the Python _bg_cited_grade() condition exactly so the headline grade and the
    # merge-plan effective_risk_tag() can never diverge: a committed grade only counts when
    # it is CITED (source reasoning/probe AND has rationale/guidance/evidence). BG_OK alone
    # is set even for uncited default grades.
    _BG_CITED=0
    if [[ "${BG_OK:-0}" == "1" ]]; then
      _bg_src_lc="$(printf '%s' "${BG_SOURCE:-}" | tr '[:upper:]' '[:lower:]')"
      if [[ ( "$_bg_src_lc" == "reasoning" || "$_bg_src_lc" == "probe" ) \
            && ( -n "${BG_RATIONALE:-}" || -n "${BG_GUIDANCE:-}" || -n "${BG_EVIDENCE:-}" ) ]]; then
        _BG_CITED=1
      fi
    fi
    _BG_CONF_LC="$(printf '%s' "${BG_CONFIDENCE:-}" | tr '[:upper:]' '[:lower:]')"
    if [[ "$_BG_CITED" == "1" ]]; then
      case "$_BG_CONF_LC" in
        low|medium|high) _BEHAVIORAL_CONF_LABEL="$_BG_CONF_LC" ;;
        *) _BEHAVIORAL_CONF_LABEL="cited" ;;
      esac
    else
      _BEHAVIORAL_CONF_LABEL="not available"
    fi
    # Normalise the mapper's deterministic severity (none/low/medium/high) as the fallback grade
    # when no CITED behavioral oracle grade exists — this is the SAME severity the merge-plan tiers
    # use, so the per-PR headline and the merge-plan bucket can never diverge. A cited probe/
    # reasoning grade still wins (it did real work and may legitimately raise/lower the tier).
    _V2_SEV="$(printf '%s' "${V2_SEVERITY:-medium}" | tr '[:upper:]' '[:lower:]')"
    case "$_V2_SEV" in high|medium|low|none) ;; *) _V2_SEV="medium" ;; esac
    case "${V2_VERDICT:-REVIEW}" in
      BLOCKED) _GRADE="high" ;;
      SAFE)    if [[ "$_BG_CITED" == "1" ]]; then _GRADE="${BG_GRADE:-$_V2_SEV}"; else _GRADE="$_V2_SEV"; fi ;;
      *)       # REVIEW: prefer the CITED behavioral grade (the probe/reasoning oracle did real
               # work and may lower/raise the tier); otherwise fall back to the mapper severity so
               # stable major/0.x bumps read as Low (optional glance), not a blanket Medium.
               if [[ "$_BG_CITED" == "1" ]]; then
                 _GRADE="${BG_GRADE:-medium}"
               else
                 _GRADE="$_V2_SEV"
               fi
               case "$_GRADE" in high|medium|low|none) ;; *) _GRADE="medium" ;; esac
               ;;
    esac
    # CI review-tier floor: a security-sensitive CI action (auth/token/registry/deploy) must not
    # headline "Low · optional glance" while its body asks for a supply-chain review. Floor it to
    # Medium so headline and body agree. Non-sensitive majors stay Low (= "optional glance",
    # matching the changelog-glance body). A cited behavioral grade, if any, still wins.
    if [[ ( "$ECOSYSTEM" == "actions" || "$ECOSYSTEM" == "docker" ) && "$_CI_TIER" == "secsens" && "$_BG_CITED" != "1" ]]; then
      case "$_GRADE" in high|medium) ;; *) _GRADE="medium" ;; esac
    fi
    # ── Breakability grade headline (decisive verdicts) ────────────────────────
    # Map V2_BREAK_GRADE (from verdict_contract.py) to decisive emoji+title
    # SAFE → ✅ SAFE
    # LOW_BREAKING → 🟡 BREAKING - LOW breakability
    # MEDIUM_BREAKING → 🟠 BREAKING - MEDIUM breakability
    # HIGH_BREAKING → 🔴 BREAKING - HIGH breakability
    case "${V2_BREAK_GRADE:-MEDIUM_BREAKING}" in
      SAFE) 
        _BRK_EMOJI="✅"
        _BRK_TITLE="SAFE"
        _BRK_DESC="safe to merge"
        ;;
      LOW_BREAKING)
        _BRK_EMOJI="🟡"
        _BRK_TITLE="BREAKING - LOW breakability"
        _BRK_DESC="quick review recommended"
        ;;
      MEDIUM_BREAKING)
        _BRK_EMOJI="🟠"
        _BRK_TITLE="BREAKING - MEDIUM breakability"
        _BRK_DESC="careful review required"
        ;;
      HIGH_BREAKING)
        _BRK_EMOJI="🔴"
        _BRK_TITLE="BREAKING - HIGH breakability"
        _BRK_DESC="fix required before merge"
        ;;
      *)
        _BRK_EMOJI="🟠"
        _BRK_TITLE="BREAKING - MEDIUM breakability"
        _BRK_DESC="review required"
        ;;
    esac
    
    # Legacy grade-based headline (keep for backward compatibility, but prefer decisive breakability_grade)
    case "$_GRADE" in
      high)   _V2_HEADLINE="🔴 Breakability: High · review required · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
      medium) _V2_HEADLINE="🟠 Breakability: Medium · review recommended · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
      low)    _V2_HEADLINE="🟡 Breakability: Low · optional glance · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
      *)      _GRADE="none"; _V2_HEADLINE="🟢 Breakability: None · safe to merge · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}" ;;
    esac
    
    # Use decisive breakability_grade headline as primary (user-requested format)
    _V2_HEADLINE_DECISIVE="${_BRK_EMOJI} ${_BRK_TITLE} · Oracle: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: ${V2_PRIO:-P2}"
    # ── CVE-aware headline floor ───────────────────────────────────────────────
    # A PR that fixes a known CVE must headline the SECURITY action, not a
    # breakability/changelog punt ("re-run", "skim the release notes"). Otherwise
    # the body says "MERGE THIS PR IMMEDIATELY" while the headline buries it, and
    # the merge plan says "SAFE — merge now" — a self-contradiction. Derive this
    # from the SAME committed signals the body uses: PR-body CVEs (CVE_COUNT) OR a
    # Dependabot/govulncheck-matched fix (_FIXES_CVE_DATA), plus NEW_ERR_COUNT and
    # V2_VERDICT. Security fixes are P0.
    _HAS_CVE_FIX=0
    _GATED_CVE_FIX=0
    if [[ -n "${_FIXES_CVE_DATA:-}" ]]; then
      # Version-gated Dependabot match (merge-results.sh confirmed resulting version >= fixed-in).
      _HAS_CVE_FIX=1; _GATED_CVE_FIX=1
    elif [[ "${CVE_COUNT:-0}" =~ ^[0-9]+$ && "${CVE_COUNT:-0}" -gt 0 ]]; then
      # PR-body CVE claim only — NOT confirmed against the resulting version.
      _HAS_CVE_FIX=1
    fi
    if [[ "$_HAS_CVE_FIX" == "1" ]]; then
      _SEC_SEV="${CVE_MAX_SEVERITY:+${CVE_MAX_SEVERITY} }"
      _SEC_CVE_N="${CVE_COUNT:-0}"
      [[ "$_SEC_CVE_N" =~ ^[0-9]+$ ]] || _SEC_CVE_N=0
      [[ "$_SEC_CVE_N" -gt 0 ]] && _SEC_CVE_DESC="${_SEC_CVE_N} ${_SEC_SEV}CVE(s)" || _SEC_CVE_DESC="known ${_SEC_SEV}vulnerabilit(ies)"
      # Only a version-gated fix earns the confident "resolves"/"MERGE NOW". A raw PR-body claim
      # is shown as "claims to fix (not version-verified)" and never forced to MERGE NOW, so the
      # headline can't over-credit a CVE the resulting version doesn't actually reach.
      if [[ "$_GATED_CVE_FIX" == "1" ]]; then _SEC_VERB="resolves"; else _SEC_VERB="claims to fix (not version-verified)"; fi
      if [[ "${NEW_ERR_COUNT:-0}" =~ ^[0-9]+$ && "${NEW_ERR_COUNT:-0}" -gt 0 ]]; then
        _V2_HEADLINE="🔴 Security fix · BLOCKED — ${_SEC_VERB} ${_SEC_CVE_DESC} but introduces build errors · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0 (fix build, then merge)"
      elif [[ "$_GRADE" == "high" ]]; then
        # The PR's OWN deterministic/behavioral break grade is High. An incidental (often
        # transitive) CVE must NOT bury that — keep the breaking-change identity as the lead
        # so the dev sees the dominant risk first, with the CVE noted as a secondary benefit.
        _V2_HEADLINE="🔴 Breakability: High · REVIEW THEN MERGE — also ${_SEC_VERB} ${_SEC_CVE_DESC}; the breaking change is the dominant risk (verify the call sites below), merging still clears the CVE · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_GATED_CVE_FIX" != "1" || "${V2_VERDICT:-REVIEW}" == "BLOCKED" || "${V2_VERDICT:-REVIEW}" == "REVIEW" || "$_GRADE" == "medium" ]]; then
        # A CVE-fixing PR that the body routes to REVIEW (breaking change flagged, or a
        # committed behavioral grade of high/medium), OR an unverified PR-body claim, must NOT
        # headline "MERGE NOW" — that contradicts the body/plan. Say REVIEW THEN MERGE so the
        # security urgency is preserved without over-promising safety.
        _V2_HEADLINE="🔴 Security fix · REVIEW THEN MERGE — ${_SEC_VERB} ${_SEC_CVE_DESC}; verify the version/breaking-change note below, but merging is the path to clear the CVE · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_TESTS_CLEAN" == "1" ]]; then
        # Build clean AND the test suite actually ran green — the only state that earns the
        # confident "MERGE NOW". Urgency never bypasses verification.
        _V2_HEADLINE="🔴 Security fix · MERGE NOW — ${_SEC_VERB} ${_SEC_CVE_DESC}; build clean and tests pass · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_TEST_FAILED" == "1" && "$_TEST_PREEXIST_VERIFIED" == "1" ]]; then
        # Tests fail, but they ALSO fail on main (proven pre-existing). Prioritize, but the dev
        # must confirm the failures are unrelated before merging — never "merge now" over red.
        _V2_HEADLINE="🔴 Security fix · REVIEW THEN MERGE — ${_SEC_VERB} ${_SEC_CVE_DESC}; the test suite also fails on \`main\` (pre-existing) — confirm it is unrelated, then merge to clear the CVE · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      elif [[ "$_TEST_FAILED" == "1" ]]; then
        # Tests fail and we could NOT prove they pre-date this PR. Highest-caution security verb.
        _V2_HEADLINE="🔴 Security fix · REVIEW THEN MERGE — ${_SEC_VERB} ${_SEC_CVE_DESC}, but the test suite is currently failing and not confirmed pre-existing — verify before merging · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      else
        # Build clean, no behavioral break, but tests were not run — prioritize, glance, merge.
        _V2_HEADLINE="🔴 Security fix · PRIORITIZE — review then merge — ${_SEC_VERB} ${_SEC_CVE_DESC}; build clean but tests were not run (safety not fully verified) · Oracle confidence: ${_BEHAVIORAL_CONF_LABEL:-not available} · Priority: P0"
      fi
    fi
    case "${V2_VERDICT:-REVIEW}" in
      SAFE)
        # None/Low with positive evidence => no residual block needed.
        [[ "$_GRADE" == "none" ]] && _V2_RESIDUAL_BLOCK=""
        ;;
      BLOCKED)
        ;;
      *)
        V2_VERDICT="REVIEW"
        ;;
    esac
    # When the committed behavioral grade carries a reasoned rationale, surface THAT
    # (concrete, committed) instead of the generic "what to check" residual.
    if [[ "${BG_OK:-0}" == "1" && -n "${BG_RATIONALE:-}" ]]; then
      _BG_BODY="Why: ${BG_RATIONALE}"
      [[ -n "${BG_GUIDANCE:-}" ]] && _BG_BODY="${_BG_BODY}
→ ${BG_GUIDANCE}"
      [[ -n "${BG_EVIDENCE:-}" ]] && _BG_BODY="${_BG_BODY}
Evidence: ${BG_EVIDENCE}"
      [[ -n "${BG_CALLSITE:-}" ]] && _BG_BODY="${_BG_BODY}
Reachable at: ${BG_CALLSITE}"
      _V2_RESIDUAL_BLOCK="$_BG_BODY"
    fi
    if [[ "${BG_OK:-0}" != "1" && ( "${V2_VERDICT:-REVIEW}" == "REVIEW" || "${V2_VERDICT:-REVIEW}" == "BLOCKED" ) ]]; then
      _V2_RESIDUAL_SUMMARY_RAW="${V2_RESIDUAL_SUMMARY:-${V2_REASON:-manual review required}}"
      _V2_RESIDUAL_CHECK_RAW="${V2_RESIDUAL_CHECK:-Review the deterministic evidence below before merging.}"
      _V2_RESIDUAL_CHANGELOG_RAW="${V2_RESIDUAL_CHANGELOG:-}"
      _V2_RESIDUAL_REACH_RAW="${V2_RESIDUAL_REACH:-}"
      _V2_RESIDUAL_BLOCK=$(V2_RESIDUAL_SUMMARY="$_V2_RESIDUAL_SUMMARY_RAW" V2_RESIDUAL_CHECK="$_V2_RESIDUAL_CHECK_RAW" V2_RESIDUAL_CHANGELOG="$_V2_RESIDUAL_CHANGELOG_RAW" V2_RESIDUAL_REACH="$_V2_RESIDUAL_REACH_RAW" R_DEP_TYPE="${DEP_TYPE:-?}" R_BUMP="${BUMP:-?}" R_FROM="${FROM:-?}" R_TO="${TO:-?}" R_USAGE_SIG="${V2_SIG_usage:-UNAVAILABLE}" R_CHANGELOG_SIG="${V2_SIG_changelog:-UNAVAILABLE}" python3 -c '
import os

def one_line(name):
    return " ".join(os.environ.get(name, "").replace("{sym}", "").replace("{loc}", "").replace("{path}", "").split())

summary = one_line("V2_RESIDUAL_SUMMARY")
check = one_line("V2_RESIDUAL_CHECK")
lines = [
    f"What to check: {summary}",
    f"→ {check}",
]
changelog = one_line("V2_RESIDUAL_CHANGELOG")
reach = one_line("V2_RESIDUAL_REACH")
if changelog:
    lines.append(f"Declared change: {changelog}")
if reach:
    lines.append(f"Reachable at: {reach}")

# ── Deterministic RESIDUAL-RISK synthesis (no committed behavioral grade) ──────────
# When there is no test/oracle proof, a dev without a good suite still needs a defensible
# call. Synthesize the residual risk from signals we ALREADY have: dependency type
# (prod vs dev/transitive = blast radius), semver bump (breaking-change likelihood), and
# whether the changed surface is even reachable from our code (usage signal). This answers
# the buyer question "if no/weak tests, what confidence remains and what risk is left".
dep_type = os.environ.get("R_DEP_TYPE", "?").strip().lower()
bump = os.environ.get("R_BUMP", "?").strip().lower()
usage_sig = os.environ.get("R_USAGE_SIG", "UNAVAILABLE").strip().upper()
changelog_sig = os.environ.get("R_CHANGELOG_SIG", "UNAVAILABLE").strip().upper()
fr, to = os.environ.get("R_FROM", "?"), os.environ.get("R_TO", "?")

factors = []
# Blast radius
if dep_type in ("development", "dev", "test", "indirect", "transitive"):
    factors.append(f"{dep_type} dependency (limited blast radius — not shipped in the prod call path)")
elif dep_type in ("production", "direct", "prod", "runtime"):
    factors.append("production/direct dependency (changes can reach shipped code paths)")
# Semver risk
if bump == "patch":
    factors.append("patch bump (intended bug-fix only; lowest semver risk)")
elif bump == "minor":
    factors.append("minor bump (additive by semver; behavioral drift possible)")
elif bump == "major":
    factors.append(f"major bump {fr}->{to} (semver signals breaking changes — highest risk)")
# Reachability of the changed surface
if usage_sig == "NEGATIVE":
    factors.append("changed API surface appears UNREACHABLE from your code (probe found no call site)")
elif usage_sig == "POSITIVE":
    factors.append("changed API surface IS reached by your code (review those call sites)")

if factors:
    lines.append("Residual risk: " + "; ".join(factors) + ".")
    if usage_sig == "NEGATIVE" and bump in ("patch", "minor") and dep_type in ("development", "dev", "test", "indirect", "transitive"):
        lines.append("→ Net: LOW residual risk — unreachable changed surface on a non-prod " + bump + " bump. Safe to merge if the build is green.")
    elif bump == "major" or usage_sig == "POSITIVE":
        lines.append("→ Net: elevated residual risk — review the reached call sites / breaking-change notes before merging.")

# TODO(AI-LAYER): the deterministic synthesis above cannot READ the changelog/release notes
# to confirm WHICH breaking changes apply, nor rank which reachable changes actually matter.
# When changelog evidence is unavailable (R_CHANGELOG_SIG=UNAVAILABLE), an LLM layer fed the
# from->to release notes + the reached call sites would add: plain-English "safe because X",
# breaking-change triage, and probabilistic risk. build-results.json already carries usages,
# dep_type, bump, from/to; it LACKS extracted changelog text — that is the AI layer to add.
if changelog_sig == "UNAVAILABLE":
    lines.append("Note: changelog/release-note text was not available to the tool, so breaking-change confirmation is deferred (AI-layer opportunity).")

print("\n".join(lines))
' 2>/dev/null || printf 'What to check: %s\n→ %s' "$_V2_RESIDUAL_SUMMARY_RAW" "$_V2_RESIDUAL_CHECK_RAW")
    fi
    # CI review-tier residual — surface the "why not auto-safe" in the VISIBLE per-PR comment
    # (the detailed body is collapsed into <details>). Only when no cited behavioral grade exists.
    if [[ ( "$ECOSYSTEM" == "actions" || "$ECOSYSTEM" == "docker" ) && -n "$_CI_TIER" && "$_BG_CITED" != "1" ]]; then
      if [[ "$_CI_TIER" == "secsens" ]]; then
        _V2_RESIDUAL_BLOCK="What to check: this CI dependency handles tokens, credentials, registry/cloud auth, signing, or deployment — \"CI-only\" does not make it auto-safe.
→ Pin to a full commit SHA, and review the changelog for changed permissions / token scopes / inputs before merging."
      else
        _V2_RESIDUAL_BLOCK="What to check: major version bump of a CI action — inputs, runtime defaults, or outputs may have changed and could break your workflow (no application code is affected).
→ Skim the release notes for breaking changes before merging."
      fi
    fi
    _COMPANION_BANNER=$(PR_NUM="$PR_NUM" RESULTS_FILE="$RESULTS_FILE" python3 -c '
import json, os
pr_num = str(os.environ.get("PR_NUM", ""))
try:
    with open(os.environ["RESULTS_FILE"]) as fh:
        data = json.load(fh)
except Exception:
    raise SystemExit(0)
prs = data.get("prs", {})
cross = data.get("cross_pr_deps", []) or []
_BLOCKING = {"fail", "pre_existing_plus_new", "vulns_introduced"}
def _is_blocked(n):
    p = prs.get(str(n)) or {}
    v = (p.get("build", {}) or {}).get("verdict", "")
    if v in _BLOCKING:
        return True
    if p.get("vuln_status") == "vulns_found" and (p.get("vuln_new_findings") or []):
        return True
    # Match the merge-plan blocked bucket: a committed verdict_v2 == BLOCKED also blocks, even
    # when the build is green (so the banner agrees with the plan companion_blocked routing).
    v2 = p.get("verdict_v2")
    if isinstance(v2, dict) and v2.get("verdict") == "BLOCKED":
        return True
    return False
blockers = []
# Skip the banner if THIS PR is itself blocked — its own headline already says so, and the
# "even though this PR verifies clean" wording would be wrong.
if not _is_blocked(pr_num):
    for g in cross:
        a, b = str(g.get("pr_a", "")), str(g.get("pr_b", ""))
        other = None
        if pr_num == a:
            other = b
        elif pr_num == b:
            other = a
        if other and _is_blocked(other) and other not in blockers:
            blockers.append(other)
if blockers:
    nums = ", ".join(f"#{n}" for n in blockers)
    print(f"> ⛔ **DO NOT MERGE YET — blocked by companion PR(s) {nums}.** This is a coordinated upgrade: its companion currently fails build or introduces new CVEs. Even though this PR verifies clean, merging it alone can break the shared dependency set. Fix {nums} first, then merge them together. (See the merge plan for ordering.)")
' 2>/dev/null || true)
    _V2_SIGNALS_TABLE="
### Signals checked
| Signal | Result |
|---|---|
| Resolve | $(v2_signal_label "${V2_SIG_resolve:-UNAVAILABLE}") |
| Build | $(v2_signal_label "${V2_SIG_build:-UNAVAILABLE}") |
| Test | $(if [[ "$_TEST_FAILED" == "1" ]]; then printf '%s' "$_TEST_SIGNAL_CELL"; elif [[ "$VERDICT" == "pre_existing" || -n "${TEST_FAIL_DETAIL:-}" ]]; then printf '⚠️ pre-existing failures — tests did not re-verify clean'; elif [[ "${TEST_RAN:-False}" != "True" ]]; then printf '· not run (no test suite or tests not executed)'; else v2_signal_label "${V2_SIG_test:-UNAVAILABLE}"; fi) |
| API diff | $(v2_signal_label "${V2_SIG_api_diff:-UNAVAILABLE}") |
| Usage | $(v2_signal_label "${V2_SIG_usage:-UNAVAILABLE}") |
| Vulnerability | $(v2_signal_label "${V2_SIG_vuln:-UNAVAILABLE}") |
| Changelog | $(v2_signal_label "${V2_SIG_changelog:-UNAVAILABLE}") |"
    COMMENT=$(COMMENT_BODY="$COMMENT" V2_HEADLINE="$_V2_HEADLINE" V2_COMPANION_BANNER="${_COMPANION_BANNER:-}" V2_RESIDUAL_BLOCK="${_V2_RESIDUAL_BLOCK:-}" V2_SIGNALS_TABLE="$_V2_SIGNALS_TABLE" python3 -c '
import os

legacy = os.environ.get("COMMENT_BODY", "")
headline = os.environ.get("V2_HEADLINE", "").strip()
companion = os.environ.get("V2_COMPANION_BANNER", "").strip()
residual = os.environ.get("V2_RESIDUAL_BLOCK", "").strip()
signals = os.environ.get("V2_SIGNALS_TABLE", "").strip()

legacy = legacy.replace("{sym}", "").replace("{loc}", "").replace("{path}", "")
if legacy.startswith("<!-- breakability-check -->\n"):
    legacy = legacy.split("\n", 1)[1]
legacy = legacy.strip()

parts = [headline]
if companion:
    parts.append(companion)
if residual:
    parts.append(residual)
if signals:
    parts.append(signals)
parts.append("<!-- breakability-check -->")
if legacy:
    parts.append("<details><summary>Internal merge-risk detail</summary>\n\n" + legacy + "\n</details>")

_head = [headline] + ([companion] if companion else [])
_rest = parts[len(_head):]
body = "\n\n".join(_head) + "\n\n" + "\n\n".join(_rest) if _rest else "\n\n".join(_head)
for marker in ("</details>### ", "</details>\n### "):
    while marker in body:
        body = body.replace(marker, "</details>\n\n### ")
normalized = []
for line in body.splitlines():
    if line.startswith("### ") and normalized and normalized[-1] != "":
        normalized.append("")
    normalized.append(line)
body = "\n".join(normalized)
print(body)
' 2>/dev/null || printf '%s' "$COMMENT")
    if gh_pr_comment "$PR_NUM" "$COMMENT"; then
      echo "  Posted comment for PR #$PR_NUM ($PKG ${FROM}→${TO}, $VERDICT)"
      POSTED=$((POSTED + 1))
    else
      echo "  ⚠️  Failed to post comment for PR #$PR_NUM"
      FAILED=$((FAILED + 1))
    fi
  fi
done

echo ""
echo "Deterministic comments: posted $POSTED, skipped $SKIPPED (AI agent already commented), failed $FAILED"

# ── Regenerate merge plan issue ──────────────────────────────────────────────
# The merge plan must always reflect the latest build-results.json.
# If the AI agent generated a previous plan, it may be stale after a
# deterministic rerun. This section creates/updates the merge plan issue
# from the current data so PR comments and the plan never contradict.
echo ""
echo "════════════ MERGE PLAN ════════════"

MERGE_PLAN_BODY=$(python3 << 'PYEOF'
import json, sys, subprocess
from datetime import datetime, timezone

with open("/tmp/build-results.json") as f:
    data = json.load(f)

prs = data.get("prs", {})
meta = data.get("metadata", {})
cross = data.get("cross_pr_deps", [])
security = data.get("security_posture", {})

# Count total open PRs (not just Dependabot) for completeness note
try:
    result = subprocess.run(
        ["gh", "pr", "list", "--state", "open", "--json", "number", "-q", "length"],
        capture_output=True, text=True, timeout=30
    )
    total_open_prs = int(result.stdout.strip()) if result.returncode == 0 else 0
except (Exception,):
    total_open_prs = 0

non_dependabot_count = max(0, total_open_prs - len(prs))

# Display helper: 0.x semver versions may contain breaking changes
def fmt_bump(bump, from_ver=""):
    """Format bump type for display. Only flags 0.x major bumps, not real v1→v2."""
    if bump == "major":
        fv = from_ver.lstrip("v").split(".")[0] if from_ver else ""
        if fv == "0":
            return "major ⚠️ (0.x unstable)"
        return "major"
    return bump

def _bg_cited_grade(pr):
    # Returns the committed behavioral-oracle grade ('high'/'medium'/'low'/'none') ONLY when
    # it is cited (has rationale/guidance/evidence); else None. This is the single source of
    # truth used to reconcile routing, the merge-plan row tag, and the PR-comment headline.
    bg = pr.get("behavioral_grade") or {}
    src = str(bg.get("source", "")).strip().lower()
    cited = src in ("reasoning", "probe") and bool(
        str(bg.get("rationale", "")).strip() or str(bg.get("guidance", "")).strip()
        or str(bg.get("evidence", "")).strip())
    if not cited:
        return None
    g = str(bg.get("grade", "")).strip().lower()
    return g if g in ("high", "medium", "low", "none") else None

import re as _v2_re
def committed_v2_verdict(pr):
    # The authoritative per-PR verdict, validated EXACTLY like get_verdict_v2() in the comment
    # poster (verdict in {SAFE,REVIEW,BLOCKED}, confidence L0-L5, priority P0-P3). Fail-closed to
    # REVIEW when missing/invalid, so the merge-plan bucket matches the PR comment's own
    # fail-closed verdict and the two can never contradict.
    v2 = pr.get("verdict_v2")
    if not isinstance(v2, dict):
        return "REVIEW"
    verdict = v2.get("verdict")
    conf = v2.get("confidence")
    prio = v2.get("priority")
    if verdict not in ("SAFE", "REVIEW", "BLOCKED"):
        return "REVIEW"
    if not isinstance(conf, str) or not _v2_re.fullmatch(r"L[0-5]", conf):
        return "REVIEW"
    if not isinstance(prio, str) or not _v2_re.fullmatch(r"P[0-3]", prio):
        return "REVIEW"
    return verdict

def _det_risk_tag(pr):
    # The raw deterministic merge_risk tag, normalized to a bare High/Medium/Low word.
    raw = ((pr.get("merge_risk") or (pr.get("deterministic") or {}).get("merge_risk") or {}).get("tag")) or "Medium"
    first = str(raw).replace("—", " ").replace("(", " ").split()[0].strip().capitalize() if str(raw).strip() else "Medium"
    return first if first in ("High", "Medium", "Low") else "Medium"

def effective_risk_tag(pr):
    # ONE verdict across every surface (routing / plan row / headline). The deterministic
    # merge_risk is a FLOOR: a cited behavioral grade may RAISE the risk above it, but a
    # behavioral none/low may NOT erase a deterministic High/Medium signal (floor invariant).
    _RANK = {"Low": 0, "Medium": 1, "High": 2}
    det_tag = _det_risk_tag(pr)
    g = _bg_cited_grade(pr)
    if g is None:
        return det_tag
    beh_tag = {"high": "High", "medium": "Medium", "low": "Low", "none": "Low"}[g]
    return beh_tag if _RANK[beh_tag] >= _RANK[det_tag] else det_tag

def headline_severity(pr):
    # Reproduce the per-PR comment headline grade EXACTLY (post-fallback get_verdict_v2 + the
    # _GRADE block) so the merge-plan severity can never disagree with the PR comment:
    #   - verdict_v2 missing OR verdict/confidence/priority invalid -> fail-closed "medium"
    #     (NEVER read a stale `severity` off a record that failed validation).
    #   - BLOCKED -> high (wins over a cited grade, matching the bash case order).
    #   - cited behavioral-oracle grade -> that grade (SAFE/REVIEW only).
    #   - else verdict_v2.severity if EXACTLY lowercase-valid, else derive
    #     {SAFE:low, REVIEW:medium} (matches get_verdict_v2's fail-safe derivation).
    v2 = pr.get("verdict_v2")
    if not isinstance(v2, dict):
        return "medium"
    verdict = v2.get("verdict")
    conf = v2.get("confidence")
    prio = v2.get("priority")
    if verdict not in ("SAFE", "REVIEW", "BLOCKED"):
        return "medium"
    if not isinstance(conf, str) or not _v2_re.fullmatch(r"L[0-5]", conf):
        return "medium"
    if not isinstance(prio, str) or not _v2_re.fullmatch(r"P[0-3]", prio):
        return "medium"
    if verdict == "BLOCKED":
        return "high"
    g = _bg_cited_grade(pr)
    if g is not None:
        return g
    sev = v2.get("severity")
    if sev in ("none", "low", "medium", "high"):
        base_sev = sev
    else:
        base_sev = {"SAFE": "low", "REVIEW": "medium"}.get(verdict, "medium")
    # CI review-tier floor — mirror the bash _GRADE CI floor exactly: a security-sensitive CI
    # action (auth/token/registry/deploy) must read at least Medium (its body asks for a
    # supply-chain review), never Low/None. Reached only when there is no cited grade (the
    # cited grade returned above), matching the bash `_BG_CITED != 1` guard.
    if pr.get("ecosystem", "") in ("actions", "docker") and ci_review_tier(pr.get("package", ""), pr.get("bump", "")) == "secsens":
        if base_sev in ("none", "low"):
            base_sev = "medium"
    return base_sev

_ci_secsens_re = _v2_re.compile(r'token|credential|secret|password|login|oauth|oidc|/auth|-auth|ssh-agent|import-gpg|gpg|cosign|sigstore|vault|kms|aws-actions|azure/login|google-github-actions/auth|configure-aws-credentials|registry|ghcr|ecr|gcr|deploy|release|publish|pages', _v2_re.IGNORECASE)

def ci_review_tier(pkg, bump):
    # MUST stay in sync with the bash _CI_TIER classifier and ci_classifier.py.
    #   "secsens" -> auth/token/registry/cloud-cred/signing/deploy CI dep -> security review
    #   ""        -> benign CI dep -> auto-safe changelog glance (majorness alone is NOT review)
    if _ci_secsens_re.search(str(pkg or "")):
        return "secsens"
    return ""

def fmt_merge_risk(pr):
    risk = pr.get("merge_risk") or (pr.get("deterministic") or {}).get("merge_risk") or {}
    tag = risk.get("tag") or "Medium"
    reason = risk.get("reason") or "change evidence is limited; default caution"
    evidence = risk.get("evidenceAxis") or "limited evidence"
    build_verification = risk.get("buildVerificationAxis") or risk.get("confidenceAxis") or pr.get("verification_label") or "unverified"
    # The deterministic reason is built before the behavioral oracle runs, so it ends in a
    # "verify against the release notes" punt. When the oracle later committed a CITED grade,
    # that punt is stale here too (same fix as the per-PR comment) — strip it.
    bg = pr.get("behavioral_grade") or {}
    bg_src = str(bg.get("source", "")).strip().lower()
    bg_cited = bg_src in ("reasoning", "probe") and bool(
        str(bg.get("rationale", "")).strip() or str(bg.get("guidance", "")).strip()
        or str(bg.get("evidence", "")).strip())
    if bg_cited and "verify against the release notes" in reason:
        for sep in (" — verify against the release notes", "; verify against the release notes",
                    ", but verify against the release notes", " verify against the release notes"):
            reason = reason.replace(sep, "")
        glabel = str(bg.get("grade", "medium")).strip().capitalize() or "Medium"
        reason = reason + f" — behavioral oracle graded exposure {glabel} (see PR comment)"
    # Reconcile the merge-plan risk TAG with the committed behavioral grade so this row and
    # the PR-comment headline can never disagree (PR#38 headline Medium vs plan High).
    tag = effective_risk_tag(pr)
    oracle_conf = str(bg.get("confidence", "")).strip().lower() if bg_cited else "not available"
    if oracle_conf not in ("low", "medium", "high"):
        oracle_conf = "cited" if bg_cited else "not available"
    return f"{tag} (Evidence: {evidence} × Build verification: {build_verification} × Oracle confidence: {oracle_conf}) — {reason}"

# Categorize PRs
safe = []        # pass verdicts + pre_existing with L2+ verification
blocked = []     # fail / pre_existing_plus_new
review = []      # pre_existing (unverified) / error / infra_error
skipped = []     # skip (breakability:skip label)
ci_only = []     # V8 FIX (H3): Actions/Docker PRs — no build verification needed
not_analyzed = []  # PRs from cancelled/incomplete batches
cancelled = []   # V8 FIX (C2): discovered but not in results

for num, pr in sorted(prs.items(), key=lambda x: int(x[0])):
    v = pr.get("build", {}).get("verdict", "?")
    pkg = pr.get("package", "?")
    fr = pr.get("from", "?")
    to = pr.get("to", "?")
    bump = pr.get("bump", "?")
    dep_type = pr.get("dep_type", "?")
    ver = pr.get("verification_label", "?")
    cves = pr.get("cves", [])
    eco = pr.get("ecosystem", "?")
    install_ok = pr.get("install_ok", False)
    pkg_dir = pr.get("pkg_dir", "/")
    error_class = pr.get("build", {}).get("error_class", "")
    new_errors = pr.get("build", {}).get("new_errors", [])
    main_exit = pr.get("build", {}).get("main_exit", -1)
    v2 = pr.get("verdict_v2") if isinstance(pr.get("verdict_v2"), dict) else {}
    entry = {"num": num, "pkg": pkg, "from": fr, "to": to, "bump": bump, "dep_type": dep_type, "ver": ver, "cves": cves, "eco": eco, "verdict": v, "install_ok": install_ok, "pkg_dir": pkg_dir, "error_class": error_class, "new_error_count": len(new_errors), "main_exit": main_exit, "merge_risk": fmt_merge_risk(pr), "behavioral_grade": pr.get("behavioral_grade") or {}, "severity": headline_severity(pr), "ci_tier": (ci_review_tier(pkg, bump) if eco in ("actions", "docker") else ""), "v2_reason": v2.get("reason") or (v2.get("residual") or {}).get("summary") or "", "v2_check": (v2.get("residual") or {}).get("check") or ""}

    # V9.8 iter6 (A): security verdict gate — a PR that INTRODUCES new CVEs must never be "safe"
    vuln_status = pr.get("vuln_status", "")
    vuln_new = pr.get("vuln_new_findings", [])
    if vuln_status == "vulns_found" and vuln_new:
        entry["vuln_new_findings"] = vuln_new
        entry["vuln_new_count"] = len(vuln_new)
        if v != "vulns_introduced":
            entry["verdict"] = "vulns_introduced"
            entry["original_verdict"] = v
            v = "vulns_introduced"

    # V9.9 iter9: govulncheck OOM/timeout → review (not safe) — user must verify manually
    if vuln_status in ("failed_oom", "failed_timeout") and v == "pass":
        entry["vuln_incomplete"] = True
        review.append(entry)
    elif v == "skipped":
        skipped.append(entry)
    elif v == "skip":
        skipped.append(entry)
    elif v == "cancelled":
        cancelled.append(entry)
    elif eco in ("actions",) or ver == "CI_ONLY":
        # V8 FIX (H3): Separate CI-only PRs — don't inflate "verified" count
        ci_only.append(entry)
    elif committed_v2_verdict(pr) == "BLOCKED" and v in ("pass", "pre_existing"):
        # ONE-COMMITTED-VERDICT: committed_v2_verdict() is the authoritative per-PR verdict the
        # comment headline/body are built from (fail-closed to REVIEW exactly like the poster). If
        # it says BLOCKED, the merge plan MUST agree — a green build does not override a committed
        # BLOCKED (PR#10/#23 clash).
        entry["v2_blocked"] = True
        blocked.append(entry)
    elif committed_v2_verdict(pr) == "REVIEW" and v in ("pass", "pre_existing"):
        # A committed REVIEW verdict routes to Manual Review here, so the plan can never say
        # "SAFE — merge now" while the PR comment says "review required".
        #
        # SOFT-REVIEW REFINEMENT (restores the reference plan's "Build Passes — Review
        # Recommended (L2/L3)" tier): a build-clean PR whose only uncertainty is a missing
        # changelog / unverifiable (racy) tests — NOT a high-severity break, NOT security-
        # sensitive, NOT a reachable declared break — is "review recommended", not the
        # manual-review wall. It routes to the `safe` list, where ver<L4 renders it under
        # "Build Passes — Review Recommended". Hard guards below keep anything risky out, and
        # the `soft_review` flag + the ver-not-L4/L5 guard ensure it can never be read as
        # "safe to merge now" (the L4 "tests pass" section excludes it).
        _vc = entry.get("v2_check", "")
        _sev = entry.get("severity", "medium")
        _is_break_reachable = (
            _vc == "review:break-reachable-api"
            or bool((pr.get("declared_break_reachability") or {}).get("prod_reachable"))
        )
        _is_sec = (
            bool(entry.get("cves"))
            or _vc == "review:security-sensitive"
            or entry.get("ci_tier") == "secsens"
            or bool(pr.get("vuln_new_findings"))
        )
        _ver = entry.get("ver", "") or ""
        _soft_review = (
            _vc in ("review:uncertain-critical-signal", "review:residual-or-uncertain")
            and _sev in ("low", "medium", "none")
            and not _is_break_reachable
            and not _is_sec
            and not entry.get("vuln_incomplete")
            and not (_ver.startswith("L4") or _ver.startswith("L5"))
            and effective_risk_tag(pr) != "High"
        )
        if _soft_review:
            entry["soft_review"] = True
            safe.append(entry)
        else:
            entry["v2_review"] = True
            review.append(entry)
    elif committed_v2_verdict(pr) == "SAFE" and v in ("pass", "pre_existing"):
        # Committed SAFE normally wins over the raw deterministic heuristic. EXCEPTION (floor
        # invariant): a behavioral none/low must not erase a deterministic High signal. If the
        # effective risk is still High here, the deterministic floor stands and it routes to
        # review — a behavioral oracle cannot lower the final merge action below a det High.
        if effective_risk_tag(pr) == "High":
            entry["high_merge_risk"] = True
            review.append(entry)
        else:
            entry["v2_safe"] = True
            safe.append(entry)
    elif effective_risk_tag(pr) == "High" and v in ("pass", "pre_existing"):
        # FALSE-SAFE GUARD (fallback only when verdict_v2 is entirely absent/invalid): a High
        # effective risk must go to REVIEW, not safe.
        entry["high_merge_risk"] = True
        review.append(entry)
    elif (((pr.get("declared_break_reachability") or {}).get("reachability_kind") == "import")
          and (pr.get("declared_break_reachability") or {}).get("prod_reachable")
          and v in ("pass", "pre_existing")):
        # FALSE-SAFE GUARD (merge plan): a Medium import-reachable BEHAVIORAL declaration is
        # unverifiable by build/test — route to REVIEW (not safe), matching the per-PR
        # "REVIEW SUGGESTED" comment.
        entry["declared_behavioral_review"] = True
        review.append(entry)
    elif v in ("pass",):
        safe.append(entry)
    elif v in ("fail", "pre_existing_plus_new"):
        blocked.append(entry)
    elif v == "pre_existing":
        # pre_existing with L2+ verification = safe (same errors, no new problems)
        # pre_existing with L0/L1 and zero new errors = likely safe
        # pre_existing with L0 and new errors or no comparison = needs review
        if ver.startswith("L2") or ver.startswith("L3") or ver.startswith("L4") or ver.startswith("L5"):
            safe.append(entry)
        else:
            review.append(entry)
    elif v in ("error", "security_review"):
        review.append(entry)
    elif v == "vulns_introduced":
        # V9.8 iter6 (A): PR introduces NEW CVEs → blocked, not safe
        blocked.append(entry)
    elif v == "conflict":
        blocked.append(entry)
    else:
        not_analyzed.append(entry)

# ── V9.6 FIX: Coordinated upgrade companion blocking ─────────────────────────
# If a PR is "safe" but its coordinated-upgrade companion is "blocked",
# move it from safe to a separate companion_blocked list with explanation.
# This prevents showing "#30 Safe" when "#21 Fix Required — must merge together".
blocked_nums = {e["num"] for e in blocked}
blocked_map = {e["num"]: e for e in blocked}
companion_blocked = []
safe_after_coord = []
for entry in safe:
    num = entry["num"]
    companion_blocked_by = []
    companion_has_vulns = False
    for group in cross:
        pr_a = str(group.get("pr_a", ""))
        pr_b = str(group.get("pr_b", ""))
        if num == pr_a and pr_b in blocked_map:
            companion_blocked_by.append(pr_b)
            if blocked_map[pr_b].get("verdict") == "vulns_introduced":
                companion_has_vulns = True
        elif num == pr_b and pr_a in blocked_map:
            companion_blocked_by.append(pr_a)
            if blocked_map[pr_a].get("verdict") == "vulns_introduced":
                companion_has_vulns = True
    if companion_blocked_by:
        entry = dict(entry)
        entry["companion_blocked_by"] = companion_blocked_by
        if companion_has_vulns:
            entry["verdict"] = "vulns_introduced"
            entry["vuln_note"] = "same target version as companion PR which introduces CVEs"
        companion_blocked.append(entry)
    else:
        safe_after_coord.append(entry)
safe = safe_after_coord

# Build markdown
lines = []
lines.append("<!-- breakability-merge-plan -->")
lines.append(f"# 📋 Breakability Merge Plan")
lines.append(f"")
_gen_ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
lines.append(f"**Generated:** {_gen_ts} (deterministic)")
lines.append(f"**PRs analyzed:** {len(prs)} Dependabot PRs")
if non_dependabot_count > 0:
    lines.append(f"**Not analyzed:** {non_dependabot_count} non-Dependabot PR(s) (out of scope — this tool only analyzes Dependabot dependency upgrades)")
lines.append(f"")

# V8 FIX (M3): Staleness banner — critical for developer trust (Blind Spot 4A)
lines.append(f"> ⏱️ **Snapshot** generated at `{_gen_ts}`. PR states may have changed since analysis.")
lines.append(f"> To refresh: `gh workflow run breakability-agent.yml`")

# V9.5 FIX: Warn if batch was cancelled/incomplete
if meta.get('incomplete'):
    _missing = meta.get('missing_pr_count', '?')
    _ibs = meta.get('incomplete_batches', [])
    lines.append(f"> ")
    lines.append(f"> ⚠️ **INCOMPLETE RUN:** {_missing} PRs were NOT analyzed (batch{'es' if len(_ibs) != 1 else ''} {', '.join(_ibs) if _ibs else '?'} cancelled/failed).")
    lines.append(f"> PRs missing from this plan should be re-analyzed before merging.")
lines.append("")

# QUICK ACTION section — high-level guidance without jargon (replaces severity-firsted complexity)
# This pre-computes what the developer needs to do RIGHT NOW, hiding verification labels in collapsibles below.
lines.append("## ⚡ What to Do Next")
lines.append("")
lines.append("> **TLDR:** Jump to [Developer Action Summary](#developer-action-summary) for numbered merge steps. Or:")
lines.append("")
_act_high_risk = len([e for e in (safe + blocked + review + ci_only + companion_blocked) if e.get("severity") == "high"])
_act_med_risk = len([e for e in (safe + blocked + review + ci_only + companion_blocked) if e.get("severity") == "medium"])
_act_low_risk = len([e for e in (safe + blocked + review + ci_only + companion_blocked) if e.get("severity") == "low"])
_act_blocked = len(blocked)
_quick_cve_fix_prs = set(str(f["pr"]) for f in security.get("cve_fixes", []))
_quick_sec_safe = [e for e in safe + ci_only if e.get("cves") or e["num"] in _quick_cve_fix_prs]
_quick_sec_blocked = [e for e in blocked if e.get("cves") or e["num"] in _quick_cve_fix_prs]
_act_security = len(_quick_sec_safe) + len(_quick_sec_blocked)
_act_msg = []
if _act_blocked > 0:
    _act_msg.append(f"🛑 **Fix first:** {_act_blocked} PR(s) have blocking verification issues — see 'Fix Required' below.")
if _act_security > 0:
    _act_msg.append(f"🔐 **Priority merge:** {_act_security} PR(s) fix known CVEs — merge them first.")
if _act_high_risk > 0:
    _act_msg.append(f"🔴 **Review required:** {_act_high_risk} PR(s) need careful review before merge.")
if _act_med_risk > 0:
    _act_msg.append(f"📋 **Follow the numbered plan:** {_act_med_risk} PR(s) need review/glance handling — see exact actions below.")
if _act_low_risk > 0 and not _act_msg:
    _act_msg.append(f"✅ **Most are safe:** {_act_low_risk} routine upgrades ready to merge.")
if not _act_msg:
    _act_msg.append("✅ **All clear:** All PRs are ready to merge.")
for msg in _act_msg:
    lines.append(f"- {msg}")
lines.append("")

# V9.7: govulncheck status aggregation — top-level banner shows scan health + vuln findings.
# V9.7b: distinguish NEW findings (introduced by PR) from pre-existing-on-main.
_vuln_not_installed = 0
_vuln_failed_oom   = 0
_vuln_failed_timeout = 0
_vuln_failed_error = 0
_vuln_found = []   # list of (pr_num, [new_findings])
_vuln_ok_preexisting = 0
_vuln_ok = 0
_main_baseline = data.get("govulncheck", {}).get("main_baseline", {})
_main_baseline_status = _main_baseline.get("status", "unknown")
_main_baseline_findings = _main_baseline.get("findings", [])
for _pn, _pr in prs.items():
    _vs = _pr.get("vuln_status", "")
    _new = _pr.get("vuln_new_findings", [])
    if _vs == "not_installed": _vuln_not_installed += 1
    elif _vs == "failed_oom":  _vuln_failed_oom += 1
    elif _vs == "failed_timeout": _vuln_failed_timeout += 1
    elif _vs == "failed_error":   _vuln_failed_error += 1
    elif _vs == "vulns_found" and _new:
        _vuln_found.append((_pn, _new))
    elif _vs == "ok_preexisting":
        _vuln_ok_preexisting += 1
    elif _vs == "ok": _vuln_ok += 1

# Main baseline context — this is key for developer trust
if _main_baseline_findings:
    lines.append("> ")
    lines.append(f"> 🛡️ **Pre-existing vulnerabilities on main:** {len(_main_baseline_findings)} known CVE(s) detected by govulncheck on the main branch (independent of any PR). Example: {', '.join(_main_baseline_findings[:3])}{'…' if len(_main_baseline_findings) > 3 else ''}. These PRs do not introduce or fix them unless explicitly noted.")
    lines.append("")

# Top banner — prioritize NEW vulns introduced by PRs > failures > ok summary
if _vuln_found:
    lines.append("> ")
    _fb_list = ", ".join(f"#{n} ({','.join(findings[:2])})" for n, findings in _vuln_found[:10])
    lines.append(f"> 🚨 **New vulnerabilities INTRODUCED by {len(_vuln_found)} PR(s)** (not present on main): {_fb_list} — review each PR comment before merging.")
    lines.append("")
if _vuln_failed_oom or _vuln_failed_timeout or _vuln_failed_error:
    _fails = []
    if _vuln_failed_oom:     _fails.append(f"{_vuln_failed_oom} OOM")
    if _vuln_failed_timeout: _fails.append(f"{_vuln_failed_timeout} timed out")
    if _vuln_failed_error:   _fails.append(f"{_vuln_failed_error} error")
    lines.append("> ")
    lines.append(f"> ⚠️ **govulncheck incomplete on {sum([_vuln_failed_oom, _vuln_failed_timeout, _vuln_failed_error])} PR(s)** ({', '.join(_fails)}) — absence of findings in those PRs is NOT proof of safety. Run `govulncheck ./...` locally with GOMEMLIMIT=2GiB before merging.")
    lines.append("")
if _vuln_not_installed:
    lines.append("> ")
    lines.append(f"> ⚠️ **govulncheck not installed on {_vuln_not_installed} PR runner(s)** — vulnerability scan was skipped. Install via `go install golang.org/x/vuln/cmd/govulncheck@latest`.")
    lines.append("")
# Clean summary if scans succeeded with no new findings
if (_vuln_ok + _vuln_ok_preexisting) and not (_vuln_found or _vuln_failed_oom or _vuln_failed_timeout or _vuln_failed_error):
    _clean_parts = []
    if _vuln_ok: _clean_parts.append(f"{_vuln_ok} with no vulns")
    if _vuln_ok_preexisting: _clean_parts.append(f"{_vuln_ok_preexisting} only touching pre-existing vulns on main (no NEW vulns introduced)")
    lines.append(f"> ✅ govulncheck: {' / '.join(_clean_parts)} across {_vuln_ok + _vuln_ok_preexisting} scanned PR(s). No PR introduces new vulnerabilities.")
    lines.append("")

# Summary table
lines.append("<details><summary><strong>📊 Technical Details & Risk Classification</strong> (L-levels, severity, counts)</summary>")
lines.append("")
lines.append("## Summary by Verification Level")
lines.append("")
lines.append(f"| Category | Count |")
lines.append(f"|----------|-------|")
likely_safe_count = sum(1 for e in review if e.get("verdict") == "pre_existing" and e.get("new_error_count", 0) == 0)
unverified_count = sum(1 for e in review if e.get("verdict") == "pre_existing" and e.get("new_error_count", 0) > 0)
needs_review_count = len(review) - likely_safe_count - unverified_count
lines.append(f"| ✅ Safe to merge — tests pass (L4) | {sum(1 for e in safe if e['ver'].startswith('L4') or e['ver'].startswith('L5'))} |")
lines.append(f"| ✅ Build passes — review recommended (L2/L3) | {sum(1 for e in safe if not (e['ver'].startswith('L4') or e['ver'].startswith('L5')))} |")
if companion_blocked:
    lines.append(f"| 🔗 Blocked (safe but companion PR needs fix) | {len(companion_blocked)} |")
if ci_only:
    _ci_sec = [e for e in ci_only if e.get("ci_tier") == "secsens"]
    _ci_maj = [e for e in ci_only if e.get("ci_tier") == "major"]
    _ci_auto = [e for e in ci_only if not e.get("ci_tier")]
    if _ci_auto:
        lines.append(f"| 🔧 CI-only (Actions/Docker — no app impact) | {len(_ci_auto)} |")
    if _ci_maj:
        lines.append(f"| 🟡 CI major action bump — changelog glance | {len(_ci_maj)} |")
    if _ci_sec:
        lines.append(f"| 🔐 CI supply-chain (auth/token/registry/deploy) — security review | {len(_ci_sec)} |")
if likely_safe_count > 0:
    lines.append(f"| ⚙️ Likely safe (deps resolved, no new errors) | {likely_safe_count} |")
if unverified_count > 0:
    lines.append(f"| ⚠️ Unverified (deps failed — infra issue) | {unverified_count} |")
lines.append(f"| ❌ Fix required | {len(blocked)} |")
if needs_review_count > 0:
    # Tier the review wall by the SAME severity shown on each PR headline so a dev sees the
    # true burden: high/medium = genuinely needs a look; low = optional glance. (Addresses
    # "80% review-required defeats the purpose" — most of the wall is usually low/optional.)
    _review_entries = [e for e in review
                       if not (e.get("verdict") == "pre_existing"
                               and e.get("new_error_count", 0) == 0)
                       and not (e.get("verdict") == "pre_existing"
                                and e.get("new_error_count", 0) > 0)]
    _rev_high = sum(1 for e in _review_entries if e.get("severity") == "high")
    _rev_med = sum(1 for e in _review_entries if e.get("severity") == "medium")
    _rev_low = sum(1 for e in _review_entries if e.get("severity") in ("low", "none"))
    if _rev_high:
        lines.append(f"| 🔴 Review required (High) | {_rev_high} |")
    if _rev_med:
        lines.append(f"| 🟠 Review recommended (Medium) | {_rev_med} |")
    if _rev_low:
        lines.append(f"| 🟡 Optional glance (Low) | {_rev_low} |")
    # Fallback: if severity tiering didn't account for every review entry, show the remainder.
    _rev_other = needs_review_count - (_rev_high + _rev_med + _rev_low)
    if _rev_other > 0:
        lines.append(f"| 🔍 Manual review | {_rev_other} |")
if skipped:
    lines.append(f"| ⏭️ Skipped (opted out) | {len(skipped)} |")
if cancelled:
    lines.append(f"| 🚫 Cancelled / Incomplete | {len(cancelled)} |")
if not_analyzed:
    lines.append(f"| ❓ Not analyzed | {len(not_analyzed)} |")
lines.append("")

# Severity summary — the SAME none/low/medium/high grade shown on every PR comment headline
# (single source of truth = headline_severity), so this roll-up and the per-PR headlines can
# never disagree. Replaces the old roll-up that parsed the decoupled legacy merge_risk string.
_all_entries = safe + blocked + review + skipped + ci_only + companion_blocked + not_analyzed + cancelled
if _all_entries:
    from collections import Counter as _Counter
    _sev_counts = _Counter((e.get("severity") or "medium") for e in _all_entries)
    lines.append("## Breakability Summary")
    lines.append("")
    lines.append(
        f"🔴 **High:** {_sev_counts.get('high', 0)} · "
        f"🟠 **Medium:** {_sev_counts.get('medium', 0)} · "
        f"🟡 **Low:** {_sev_counts.get('low', 0)} · "
        f"🟢 **None:** {_sev_counts.get('none', 0)}")
    lines.append("")
    lines.append(
        "> High/Medium = worth a review · Low = optional glance · None = safe to merge. "
        "Severity matches each PR's breakability headline (security-fix PRs show a "
        "merge-priority headline instead).")
    lines.append("")

# Close the Technical Details collapsible section
lines.append("</details>")
lines.append("")

# V8 FIX (M4): Developer Action Summary — prioritized numbered steps (regression from ref plan #39)
lines.append("## Developer Action Summary")
lines.append("")
lines.append("**Plain-English merge guidance — see Technical Details above for verification levels.**")
lines.append("")
_step = 1
# Security fixes first — use BOTH pr-body CVEs AND Dependabot alert matches (cve_fixes)
_cve_fix_prs = set(str(f["pr"]) for f in security.get("cve_fixes", []))
_sec_safe_l4 = [e for e in safe + ci_only if (e.get("cves") or e["num"] in _cve_fix_prs) and (e.get("ver", "").startswith("L4") or e.get("ver", "").startswith("L5")) and not e.get("ci_tier")]
_sec_safe_l2 = [e for e in safe + ci_only if (e.get("cves") or e["num"] in _cve_fix_prs) and (not (e.get("ver", "").startswith("L4") or e.get("ver", "").startswith("L5")) or e.get("ci_tier"))]
_sec_safe = _sec_safe_l4 + _sec_safe_l2  # combined for later reference
_sec_blocked = [e for e in blocked if e.get("cves") or e["num"] in _cve_fix_prs]
def is_optional_glance_entry(e):
    check = str(e.get("v2_check") or "")
    return (
        e.get("severity") in ("low", "none")
        and e.get("verdict") != "pre_existing"
        and check.startswith("glance:")
        and not e.get("cves")
        and e["num"] not in _cve_fix_prs
        and not e.get("ci_tier")
    )
_review_optional_glance = [
    e for e in review
    if is_optional_glance_entry(e)
]
if _sec_safe_l4:
    _sec_nums = ", ".join(f"#{e['num']}" for e in _sec_safe_l4)
    lines.append(f"{_step}. **MERGE NOW — CVE fixes (tests pass):** {_sec_nums} — fix known vulnerabilities right away")
    _step += 1
if _sec_safe_l2:
    _sec_nums = ", ".join(f"#{e['num']}" for e in _sec_safe_l2)
    lines.append(f"{_step}. **REVIEW then MERGE — CVE fixes (build passes, tests not run):** {_sec_nums} — check build details, then merge")
    _step += 1
if _sec_blocked:
    _sec_nums = ", ".join(f"#{e['num']}" for e in _sec_blocked)
    lines.append(f"{_step}. **FIX FIRST — security PRs with blocking issues:** {_sec_nums} — resolve the listed blocker before merging")
    _step += 1
# L4 safe PRs
_l4_safe = [e for e in safe if e.get("ver", "").startswith("L4") and not e.get("cves")]
if _l4_safe:
    lines.append(f"{_step}. **MERGE — tests pass:** {len(_l4_safe)} PR(s) — safest batch, merge together")
    _step += 1
# L2 safe PRs (build passes, tests fail or not run)
_l2_safe = [e for e in safe if not e.get("ver", "").startswith("L4") and not e.get("cves")]
if _l2_safe:
    lines.append(f"{_step}. **GLANCE then MERGE — build passes, tests not run:** {len(_l2_safe)} PR(s) — skim changelog for breaking changes")
    _step += 1
if _review_optional_glance:
    _glance_nums = ", ".join(f"#{e['num']}" for e in _review_optional_glance)
    lines.append(f"{_step}. **GLANCE then MERGE — low breakability:** {_glance_nums} — optional changelog/API skim, not deep review")
    _step += 1
# Companion blocked
if companion_blocked:
    _cb_nums = ", ".join(f"#{e['num']}" for e in companion_blocked)
    lines.append(f"{_step}. **WAIT — paired PRs blocked:** {_cb_nums} — merge these only after fixing their companion PR")
    _step += 1
# CI-only PRs
if ci_only:
    _ci_sec = [e for e in ci_only if e.get("ci_tier") == "secsens"]
    _ci_maj = [e for e in ci_only if e.get("ci_tier") == "major"]
    _ci_auto = [e for e in ci_only if not e.get("ci_tier")]
    if _ci_auto:
        lines.append(f"{_step}. **MERGE — CI/Actions PRs:** {len(_ci_auto)} PR(s) — no app impact")
        _step += 1
    if _ci_maj:
        _ci_maj_nums = ", ".join(f"#{e['num']}" for e in _ci_maj)
        lines.append(f"{_step}. **GLANCE then MERGE — major CI actions:** {_ci_maj_nums} — review for breaking input changes")
        _step += 1
    if _ci_sec:
        _ci_sec_nums = ", ".join(f"#{e['num']}" for e in _ci_sec)
        lines.append(f"{_step}. **REVIEW — supply-chain sensitive CI:** {_ci_sec_nums} — pin to commit SHA, verify permissions")
        _step += 1
# Likely safe
if likely_safe_count > 0:
    lines.append(f"{_step}. **INVESTIGATE — likely safe (unclear baseline):** {likely_safe_count} PR(s) — no new errors detected, but baseline build may be broken")
    _step += 1
# Fix required
_non_sec_blocked = [e for e in blocked if not e.get("cves")]
if _non_sec_blocked:
    lines.append(f"{_step}. **FIX NEEDED:** {len(_non_sec_blocked)} PR(s) have blocking verification issues")
    _step += 1
lines.append("")

# Infrastructure banner — when many PRs are in review with the same root cause
if len(review) > 0:
    infra_count = sum(1 for e in review if e.get("verdict") == "pre_existing")
    if infra_count > len(prs) * 0.5:
        # More than half of PRs are pre_existing — likely a systemic issue
        lines.append("> ⚠️ **Infrastructure Issue:** %d of %d PRs have pre-existing build failures (not caused by upgrades)." % (infra_count, len(prs)))
        lines.append("> Fix the baseline build on `main` and re-run analysis to unlock full verification for these PRs.")
        lines.append("> PRs marked \"Likely Safe\" below have no new errors — they are probably safe to merge despite incomplete verification.")
        lines.append("")

        # Show baseline error details so developers know WHAT to fix (end-user feedback 1.2)
        main_build = data.get("main_build", {})
        baseline_errors = []
        # Go baseline errors
        go_data = main_build.get("go", {})
        go_exit = go_data.get("exit", -1)
        go_output = go_data.get("output_tail", "")
        if go_exit is not None and go_exit not in (-1, 0) and go_output:
            error_lines = [l.strip() for l in go_output.split('\n')
                          if 'error' in l.lower() or 'Error' in l or 'FAIL' in l
                          or 'cannot' in l.lower() or 'undefined' in l.lower()][:10]
            if error_lines:
                baseline_errors.extend(error_lines)
        # npm baseline errors
        npm_data = main_build.get("npm", {})
        npm_exit = npm_data.get("exit", -1)
        npm_output = npm_data.get("output_tail", "")
        if npm_exit is not None and npm_exit not in (-1, 0) and npm_output:
            npm_errs = [l.strip() for l in npm_output.split('\n')
                       if 'error' in l.lower() or 'TS' in l][:5]
            if npm_errs:
                baseline_errors.extend(npm_errs)
        # Check per-module baselines from any PR's output (if available)
        error_classes = set()
        for num, pr in prs.items():
            ec = pr.get("build", {}).get("error_class", "")
            if ec and ec != "build_fail":
                error_classes.add(ec)
        if baseline_errors or error_classes:
            lines.append("### Baseline Build Errors on `main`")
            lines.append("")
            if error_classes:
                class_descriptions = {
                    "infra_error": "Infrastructure/network error (GOSUMDB, proxy, or registry issue)",
                    "private_module": "Private module access denied (GOPRIVATE not configured)",
                    "resource_exhaustion": "Out of memory / compiler killed",
                    "timeout": "Build timed out",
                    "cache_corruption": "Go build cache corruption",
                }
                for ec in sorted(error_classes):
                    desc = class_descriptions.get(ec, ec)
                    lines.append(f"- **{ec}:** {desc}")
                lines.append("")
            if baseline_errors:
                lines.append("```")
                for err in baseline_errors[:8]:
                    lines.append(err[:200])
                lines.append("```")
                lines.append("")
            lines.append("Fix these issues on `main`, then re-run: `gh workflow run breakability-agent.yml`")
            lines.append("")

# CVE highlight — union of PR-body CVEs and Dependabot alert-matched fixes
all_cves = []
_cve_fix_prs_set = set(str(f["pr"]) for f in security.get("cve_fixes", []))
# Map PR -> the CVE ids it actually resolves (version-gated, incl. transitive go.mod
# bumps), so Dependabot-only matches don't render blank CVE cells.
_cve_ids_by_pr = {}
for f in security.get("cve_fixes", []):
    _cve_ids_by_pr.setdefault(str(f["pr"]), []).append(f.get("cve_id") or "")
# Track the committed routing bucket for each PR so the verdict shown here can never
# contradict the Manual-Review / Fix-required sections below (one committed verdict).
_cve_bucket = {}
for _catname, cat in [("safe", safe), ("blocked", blocked), ("review", review), ("skipped", skipped), ("ci_only", ci_only), ("companion_blocked", companion_blocked), ("not_analyzed", not_analyzed), ("cancelled", cancelled)]:
    for e in cat:
        if e.get("cves") or e["num"] in _cve_fix_prs_set:
            all_cves.append(e)
            _cve_bucket.setdefault(e["num"], _catname)
if all_cves:
    lines.append("## 🔴 Security — CVEs Fixed by These Upgrades")
    lines.append("")
    lines.append("> **ACTION REQUIRED:** Merge security fix PRs as soon as possible to resolve known vulnerabilities.")
    lines.append("")
    for e in all_cves:
        # Prefer the version-gated Dependabot fix list (merge-results.sh already gated
        # these on first_patched_version vs the resulting incl-transitive go.mod version).
        # Only fall back to raw PR-body CVE claims when there is NO gated match, and mark
        # them unverified — otherwise a PR-body CVE the resulting version does NOT actually
        # reach its fixed-in version (e.g. otel/sdk →1.42 vs a CVE fixed in 1.43) gets
        # wrongly credited as "fixed".
        _gated = [c for c in _cve_ids_by_pr.get(str(e["num"]), []) if c]
        if _gated:
            _cve_list = _gated
            cve_str = ", ".join(_cve_list)
        else:
            _cve_list = [c for c in (e.get("cves") or []) if c]
            cve_str = (", ".join(_cve_list) + " (claimed in PR body — not version-verified vs fixed-in)") if _cve_list else "see Dependabot alerts"
        # Verdict note derived from the COMMITTED bucket (not the raw build verdict):
        # a CVE-fixing PR routed to Manual Review or Fix-required must NOT also read
        # "SAFE — merge now" (the PR#10/#23 self-contradiction).
        _b = _cve_bucket.get(e["num"], "")
        _is_l4 = e.get("ver", "").startswith("L4") or e.get("ver", "").startswith("L5")
        if _b == "blocked":
            verdict_note = " ❌ Fix required before merge"
        elif _b == "review":
            verdict_note = " ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)"
        elif _b == "companion_blocked":
            verdict_note = " 🔗 **Blocked by a companion PR** — see Blocked section below (fix companion first)"
        elif _b == "skipped":
            verdict_note = " ⏭️ Opted out (`breakability:skip`) — merge manually to resolve the CVE"
        elif _b in ("not_analyzed", "cancelled"):
            verdict_note = " ❓ Not analyzed this run — re-run the tool before merging"
        elif _b == "ci_only" and e.get("ci_tier") == "secsens":
            verdict_note = " 🔐 **Review — supply-chain sensitive** (pin SHA, review permissions); merge to resolve the CVE after review"
        elif _b == "ci_only" and e.get("ci_tier") == "major":
            verdict_note = " 🟡 **Major CI action bump** — glance at the changelog, then merge to resolve the CVE"
        elif _b in ("safe", "ci_only"):
            verdict_note = " ✅ **SAFE — merge now** (tests pass, L4)" if _is_l4 else " ⚙️ **Build verified (L2/L3) — tests not verified clean; review then merge**"
        else:
            verdict_note = ""
        lines.append(f"- **PR #{e['num']}** `{e['pkg']}` {e['from']}→{e['to']} — {cve_str}{verdict_note}")
    lines.append("")

# V9.8 iter6 (A): Dedicated security-risk section for PRs that INTRODUCE new CVEs
vulns_introduced = [e for e in blocked if e.get("verdict") == "vulns_introduced"]
if vulns_introduced:
    lines.append("## 🚨 Security Risk — PRs That Introduce NEW Vulnerabilities")
    lines.append("")
    lines.append("> **DO NOT MERGE** these PRs. They add CVEs not present on `main`. Pin to an earlier version, wait for an upstream fix, or close the PR.")
    lines.append("")
    lines.append("| PR | Package | Version | NEW CVEs | Pre-existing |")
    lines.append("|---|---|---|---|---|")
    for e in vulns_introduced:
        cves_new = e.get("vuln_new_findings", [])
        cves_show = ", ".join(cves_new[:5]) + (f" +{len(cves_new)-5} more" if len(cves_new) > 5 else "")
        pre = len([c for c in (e.get("cves") or [])])  # fallback
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {e.get('vuln_new_count', len(cves_new))}: {cves_show} | see PR |")
    lines.append("")

# Safe to merge — split L4 (tests pass) vs L2/L3 (build only)
safe_l4 = [e for e in safe if e["ver"].startswith("L4") or e["ver"].startswith("L5")]
safe_l2 = [e for e in safe if not (e["ver"].startswith("L4") or e["ver"].startswith("L5"))]

if safe_l4:
    lines.append("## ✅ Safe to Merge — Tests Pass (L4 verified, lowest risk)")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
    lines.append("|----|---------|---------|----|------------|-------------|")
    for e in safe_l4:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e['cves'] else ""
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {e['ver']}{cve_badge} |")
    lines.append("")

if safe_l2:
    lines.append("## ✅ Build Passes — Review Recommended (L2/L3 verified)")
    lines.append("")
    lines.append("> Build and type-check pass. Tests were not run or had pre-existing failures. Review changelog for major bumps.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
    lines.append("|----|---------|---------|----|------------|-------------|")
    for e in safe_l2:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e['cves'] else ""
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {e['ver']}{cve_badge} |")
    lines.append("")

# Companion-blocked: safe PRs that can't be merged yet because their coordinated partner is broken
if companion_blocked:
    lines.append("## 🔗 Blocked — Safe but Companion PR Needs Fix First")
    lines.append("")
    lines.append("These PRs pass build verification but are **blocked** because a companion PR (coordinated upgrade) currently has build failures or security issues.")
    lines.append("Fix the companion PR first, then merge both together.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Verification | Blocked By |")
    lines.append("|----|---------|---------|------|------------|-------------|------------|")
    for e in companion_blocked:
        companions = ", ".join(f"#{n}" for n in e.get("companion_blocked_by", []))
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {e['ver']} ✅ | Fix {companions} first |")
    lines.append("")

# Cross-PR deps
if cross:
    lines.append("## 🔗 Coordinated Upgrades (merge together)")
    lines.append("")
    # Group pairwise entries into multi-PR groups by shared package name
    from collections import defaultdict
    _pkg_groups = defaultdict(set)
    _pkg_reason = {}
    _single_groups = []
    for group in cross:
        pr_a = str(group.get("pr_a", "?"))
        pr_b = str(group.get("pr_b", "?"))
        reason = group.get("reason", "related")
        # Extract package name from reason for grouping
        import re as _re
        _m = _re.search(r'`([^`]+)`|Same package \(([^)]+)\)', reason)
        _key = (_m.group(1) or _m.group(2)) if _m else reason[:40]
        _pkg_groups[_key].add(pr_a)
        _pkg_groups[_key].add(pr_b)
        _pkg_reason[_key] = reason
    for pkg_key, pr_set in sorted(_pkg_groups.items()):
        pr_list = sorted(pr_set, key=lambda x: int(x) if x.isdigit() else 99)
        pr_str = " + ".join(f"#{p}" for p in pr_list)
        reason = _pkg_reason.get(pkg_key, pkg_key)
        # P0 FIX: never instruct "merge all together" if any member is blocked
        # (build_fails or introduces NEW CVEs). A coordinated group is only as
        # safe as its weakest member — surfacing this prevents a dangerous merge.
        group_blocked = sorted((p for p in pr_list if p in blocked_nums),
                               key=lambda x: int(x) if x.isdigit() else 99)
        if group_blocked:
            _bb = ", ".join(f"#{n}" for n in group_blocked)
            _reasons = []
            for n in group_blocked:
                bv = blocked_map.get(n, {}).get("verdict", "")
                if bv == "vulns_introduced":
                    _reasons.append(f"#{n} introduces {blocked_map[n].get('vuln_new_count', 0)} NEW CVE(s)")
                elif bv == "fail":
                    _reasons.append(f"#{n} build fails")
                elif bv == "conflict":
                    _reasons.append(f"#{n} has merge conflicts")
                else:
                    _reasons.append(f"#{n} blocked")
            lines.append(f"- ⛔ **{reason}:** {pr_str} — **DO NOT MERGE as a group.** "
                         f"{'; '.join(_reasons)}. Resolve {_bb} first (see sections below); "
                         f"merging the group now would pull in the blocking PR.")
            continue
        # Simplify reason for groups with 3+ PRs
        if len(pr_list) >= 3:
            lines.append(f"- **{reason}:** {pr_str} — merge all {len(pr_list)} together")
        else:
            order = ""
            for group in cross:
                if str(group.get("pr_a")) in pr_set and str(group.get("pr_b")) in pr_set:
                    order = group.get("merge_order", "")
                    break
            order_text = f" ({order})" if order else ""
            lines.append(f"- **{reason}:** {pr_str}{order_text}")
    lines.append("")

# Blocked
if blocked:
    lines.append("## ❌ Fix Required — Do Not Merge")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Issue |")
    lines.append("|----|---------|---------|----|------------|-------|")
    for e in blocked:
        if e["verdict"] == "fail":
            issue = "Build fails"
        elif e["verdict"] == "conflict":
            issue = "Merge conflicts — rebase required"
        elif e["verdict"] == "vulns_introduced":
            issue = f"🚨 {e.get('vuln_new_count', 0)} NEW CVE(s) introduced — see Security Risk section"
        else:
            issue = "New errors on top of pre-existing"
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {issue} |")
    lines.append("")

# Review — split into "Likely Safe" and "Needs Review".
# End-user feedback: L0 pre_existing with zero new errors IS a safety signal.
# The tool compared both branches and found no new errors — that's useful info.
# Only truly "unverified" PRs (where comparison couldn't happen) go into unverified.
likely_safe = [e for e in review if e["verdict"] == "pre_existing" and e.get("new_error_count", 0) == 0]
unverified = [e for e in review if e["verdict"] == "pre_existing" and e.get("new_error_count", 0) > 0]
optional_glance = [
    e for e in review
    if is_optional_glance_entry(e)
]
needs_review = [
    e for e in review
    if e["verdict"] != "pre_existing"
    and not is_optional_glance_entry(e)
]

if likely_safe:
    lines.append("## ⚙️ Likely Safe — No New Errors (pre-existing build failure)")
    lines.append("")
    lines.append("These PRs do **not** introduce new failures. Both `main` and the PR branch")
    lines.append("produce the same build errors. The upgrades are likely safe to merge.")
    lines.append("Fix baseline build on `main` and re-run for full L2+ verification.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Module | Status |")
    lines.append("|----|---------|---------|----|------------|--------|--------|")
    for e in likely_safe:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
        pkg_dir = e.get('pkg_dir', '/')
        mod_col = pkg_dir if pkg_dir != '/' else 'root'
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {mod_col} | {e['ver']} — no new errors{cve_badge} |")
    lines.append("")

if unverified:
    lines.append("## ⚠️ Needs Investigation (new errors detected or comparison failed)")
    lines.append("")
    lines.append("These PRs have new errors or could not be compared against the baseline.")
    lines.append("Manual review is recommended before merging.")
    lines.append("")
    lines.append("| PR | Package | Version | Bump | Merge Risk | Module | Issue |")
    lines.append("|----|---------|---------|----|------------|--------|-------|")
    for e in unverified:
        cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
        pkg_dir = e.get('pkg_dir', '/')
        mod_col = pkg_dir if pkg_dir != '/' else 'root'
        lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | {mod_col} | Deps failed — infra issue{cve_badge} |")
    lines.append("")

if optional_glance:
    lines.append("## 🟡 Optional Glance — Low Breakability")
    lines.append("")
    lines.append("These PR comments are already downgraded to **Low / optional glance** by the committed verdict. Skim the noted evidence, then merge if no project-specific concern appears.")
    lines.append("")
    for e in optional_glance:
        reason = e.get("v2_reason") or "low breakability evidence"
        check = f" (`{e.get('v2_check')}`)" if e.get("v2_check") else ""
        lines.append(f"- **PR #{e['num']}** `{e['pkg']}` {e['from']}→{e['to']} — **Low / optional glance**: {reason}{check}")
    lines.append("")

if needs_review:
    lines.append("## ⚠️ Manual Review Needed")
    lines.append("")
    for e in needs_review:
        bg = e.get("behavioral_grade") or {}
        bg_src = str(bg.get("source", "")).strip().lower()
        bg_cited = bg_src in ("reasoning", "probe") and bool(
            str(bg.get("rationale", "")).strip() or str(bg.get("guidance", "")).strip()
            or str(bg.get("evidence", "")).strip())
        if e["verdict"] == "security_review":
            reason = "Build passes but npm audit found critical/high vulnerabilities"
        elif e.get("declared_behavioral_review") or e.get("high_merge_risk"):
            # Build PASSED — the review signal is a declared BEHAVIORAL break, not a build error.
            if bg_cited:
                glabel = str(bg.get("grade", "medium")).strip().capitalize() or "Medium"
                guid = str(bg.get("guidance", "")).strip()
                reason = (f"Declared behavioral breaking change in a used package — behavioral oracle "
                          f"graded exposure **{glabel}** (build/test/api-diff cannot confirm runtime exposure)")
                if guid:
                    reason += f"; {guid}"
            else:
                reason = ("Declared behavioral breaking change in a used package — build/test/api-diff "
                          "cannot confirm runtime exposure; see the PR comment for the graded verdict")
        else:
            _v = e.get("verdict", "")
            _ver = str(e.get("ver", "") or "")
            _verified_clean = (
                _v in ("pass", "pre_existing") or _ver.startswith(("L2", "L3", "L4"))
            ) and e.get("new_error_count", 0) == 0
            if e.get("vuln_incomplete"):
                reason = ("Build passed but the vulnerability scan was incomplete (timeout/OOM) — "
                          "re-run govulncheck to confirm no new CVEs before merging")
            elif _verified_clean:
                # The build VERIFIED clean (L2+/no new errors); it is only here because a
                # committed REVIEW verdict routed it. Do not mislabel it a build/infra failure.
                reason = (f"Verified clean ({_ver or 'build passed'}); routed to review — "
                          f"see the PR comment for the committed verdict")
            else:
                reason = "Build error / infrastructure issue"
        lines.append(f"- **PR #{e['num']}** `{e['pkg']}` {e['from']}→{e['to']} — Merge Risk: {e.get('merge_risk', 'Medium — default caution')} — {reason}")
    lines.append("")

# V8 FIX (H3/L3): CI-only PRs in their own section, not mixed with verified Go/npm PRs
if ci_only:
    _ci_sec = [e for e in ci_only if e.get("ci_tier") == "secsens"]
    _ci_maj = [e for e in ci_only if e.get("ci_tier") == "major"]
    _ci_auto = [e for e in ci_only if not e.get("ci_tier")]
    if _ci_auto:
        lines.append("## 🔧 CI-Only (Actions / Docker — no application impact)")
        lines.append("")
        lines.append("These PRs only affect CI/CD workflows. No build verification needed — zero app code impact.")
        lines.append("")
        lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
        lines.append("|----|---------|---------|----|------------|-------------|")
        for e in _ci_auto:
            cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
            lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | CI_ONLY — auto-safe{cve_badge} |")
        lines.append("")
    if _ci_maj:
        lines.append("## 🟡 Major CI Action Bumps — Changelog Glance")
        lines.append("")
        lines.append("Major version bumps of CI actions. No application code is affected, but a major bump can change inputs, runtime defaults, or output names and **break the workflow**. Skim the changelog for breaking changes before merging.")
        lines.append("")
        lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
        lines.append("|----|---------|---------|----|------------|-------------|")
        for e in _ci_maj:
            cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
            lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | 🟡 major bump — glance changelog{cve_badge} |")
        lines.append("")
    if _ci_sec:
        lines.append("## 🔐 CI Supply-Chain — Review Required (not auto-safe)")
        lines.append("")
        lines.append("These CI actions handle tokens, credentials, registry/cloud auth, code signing, or deployment/publishing. A breaking or compromised release here is a supply-chain risk, so they are **not** auto-cleared. Before merging: **pin to a full commit SHA**, and review the changelog for changed **permissions / token scopes / inputs**.")
        lines.append("")
        lines.append("| PR | Package | Version | Bump | Merge Risk | Verification |")
        lines.append("|----|---------|---------|----|------------|-------------|")
        for e in _ci_sec:
            cve_badge = f" 🔴 {','.join(e['cves'])}" if e.get('cves') else ""
            lines.append(f"| #{e['num']} | `{e['pkg']}` | {e['from']}→{e['to']} | {fmt_bump(e['bump'], e.get('from', ''))} | {e.get('merge_risk', 'Medium — default caution')} | ⚠️ REVIEW — supply-chain sensitive{cve_badge} |")
        lines.append("")

# Skipped (breakability:skip label)
if skipped:
    lines.append("## ⏭️ Skipped (opted out)")
    lines.append("")
    for e in skipped:
        lines.append(f"- PR #{e['num']} `{e['pkg']}` — skipped ({e.get('eco', '?')})")
    lines.append("")

# Cancelled / Incomplete
if cancelled:
    lines.append("## 🚫 Cancelled / Incomplete")
    lines.append("")
    lines.append("These PRs were discovered but not analyzed (batch timeout or cancellation).")
    lines.append("")
    for e in cancelled:
        lines.append(f"- PR #{e['num']} `{e['pkg']}` — analysis incomplete")
    lines.append("")

# Security posture
if security:
    lines.append("## 🛡️ Repository Security Posture")
    lines.append("")
    _alerts_unavail = security.get("alerts_unavailable", False)
    open_alerts = security.get("total_open_alerts", 0)
    fixable = security.get("alerts_fixable_by_merging", 0)
    if _alerts_unavail:
        lines.append("- Open Dependabot alerts: **⚠️ Unavailable** (token missing `security_events` permission — set `BREAKABILITY_PAT` repo secret)")
    else:
        lines.append(f"- Open Dependabot alerts: **{open_alerts}**")
        if fixable:
            lines.append(f"- Alerts fixable by merging these PRs: **{fixable}**")
        by_sev = security.get("severity_counts", {})
        if by_sev:
            sev_str = ", ".join(f"{s}: {c}" for s, c in sorted(by_sev.items()))
            lines.append(f"- By severity: {sev_str}")
    lines.append("")

    # V9.8 iter6 (B): precise CVE fixes with severity + advisory links
    cve_fixes = security.get("cve_fixes", [])
    if cve_fixes:
        _SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "moderate": 2, "low": 3, "unknown": 4}
        # Group by PR so one PR fixing multiple CVEs appears once
        fixes_by_pr = {}
        for f in cve_fixes:
            pr = f["pr"]
            fixes_by_pr.setdefault(pr, []).append(f)
        def _pr_sort_key(pr_num):
            sev = min(_SEV_RANK.get((f["severity"] or "").lower(), 4) for f in fixes_by_pr[pr_num])
            return (sev, pr_num)
        # Which CVEs are delivered by more than one PR (so a dev knows "merge any one").
        _prs_per_cve = {}
        for f in cve_fixes:
            cid = f.get("cve_id") or ""
            if cid:
                _prs_per_cve.setdefault(cid, set()).add(str(f["pr"]))
        lines.append("### 🛡️ Security Fixes — Merge with Priority")
        lines.append("")
        lines.append("| PR | Package | Version | CVE(s) | Severity | Fixed in | Advisory |")
        lines.append("|---|---|---|---|---|---|---|")
        for pr_num in sorted(fixes_by_pr.keys(), key=_pr_sort_key):
            flist = fixes_by_pr[pr_num]
            pkg = flist[0]["package"]
            fr = flist[0].get("from_version", ""); to = flist[0].get("to_version", "")
            via = flist[0].get("via", "primary")
            primary_pkg = flist[0].get("primary_package", "")
            # A transitive fix bumps the vulnerable package indirectly: its from-version is
            # not the PR's own (primary) from-version, so render only "→{to}" and name the
            # primary package that carried the bump instead of fabricating a range.
            if via == "transitive":
                ver_cell = f"→{to} (transitive via `{primary_pkg}`)" if primary_pkg else f"→{to} (transitive)"
            else:
                ver_cell = f"{fr}→{to}"
            cve_cell = ", ".join(sorted(set(f["cve_id"] for f in flist if f["cve_id"])))
            sev_cell = ", ".join(sorted(set(f["severity"] for f in flist if f["severity"])))
            fixed_cell = ", ".join(sorted(set(f.get("first_patched_version") or "?" for f in flist)))
            adv_cell = " ".join(f"[{f['cve_id']}](https://nvd.nist.gov/vuln/detail/{f['cve_id']})" for f in flist if (f['cve_id'] or '').startswith('CVE-'))
            if not adv_cell:
                adv_cell = "_see Dependabot_"
            lines.append(f"| #{pr_num} | `{pkg}` | {ver_cell} | {cve_cell} | {sev_cell} | {fixed_cell} | {adv_cell} |")
        lines.append("")
        _multi = {c: sorted(p, key=lambda x: int(x) if x.isdigit() else 0) for c, p in _prs_per_cve.items() if len(p) > 1}
        if _multi:
            lines.append("> ℹ️ **Some CVEs are delivered by more than one PR — merge any one to clear them:**")
            for cid in sorted(_multi):
                lines.append(f">   - `{cid}`: " + ", ".join(f"#{n}" for n in _multi[cid]))
            lines.append("")

    # V9.8 iter6 (B): orphan alerts (no PR fixes them) — needs manual attention
    orphans = security.get("orphan_alerts", [])
    if orphans:
        lines.append("### ⚠️ Orphan Alerts — No PR Fixes These")
        lines.append("")
        lines.append("_These open Dependabot alerts have **no corresponding PR** in this batch. Manual remediation required._")
        lines.append("")
        lines.append("| Package | CVE | Severity | Fixed in (upstream) |")
        lines.append("|---|---|---|---|")
        for o in orphans:
            cve_cell = f"[{o['cve_id']}](https://nvd.nist.gov/vuln/detail/{o['cve_id']})" if (o['cve_id'] or '').startswith('CVE-') else (o['cve_id'] or '-')
            lines.append(f"| `{o['package']}` | {cve_cell} | **{o['severity']}** | {o['first_patched_version']} |")
        lines.append("")

lines.append("---")
lines.append("> 🔬 *Deterministic merge plan — generated from build-results.json. Refer to individual PR comments for full details.*")

print("\n".join(lines))
PYEOF
)

if [[ -n "$MERGE_PLAN_BODY" && "$MERGE_PLAN_BODY" != *"Traceback"* ]]; then
  _MP_LABEL="breakability-merge-plan"

  # Count analyzed PRs for the title
  _MP_PR_COUNT=$(python3 -c "
import json
with open('$RESULTS_FILE') as f:
    data = json.load(f)
print(len(data.get('prs', {})))
" 2>/dev/null || echo "?")
  _MP_TITLE="📋 Breakability Merge Plan $(date -u '+%Y-%m-%d %H:%M UTC') (${_MP_PR_COUNT} PRs)"

  # GitHub caps issue/comment bodies at 65536 chars. On large repos the rendered plan
  # (per-PR sections + long CVE lists) can exceed that, which previously made
  # `gh issue create` fail silently ("Failed to create merge plan issue") and post
  # nothing. Build a truncated copy for the GitHub post (the full body is still written
  # to disk as the dry-run/CI artifact). The plan is front-loaded (Developer Action
  # Summary, security CVEs, per-PR review), so head-truncation preserves the
  # actionable content.
  MERGE_PLAN_BODY_POST="$MERGE_PLAN_BODY"
  if [[ "${#MERGE_PLAN_BODY}" -gt 65000 ]]; then
    MERGE_PLAN_BODY_POST=$(MP_FULL="$MERGE_PLAN_BODY" python3 << 'PYEOF'
import os
body = os.environ.get("MP_FULL", "")
LIMIT = 65536
NOTICE = ("\n\n---\n> ⚠️ **Plan truncated** — the full plan exceeded GitHub's "
          "65,536-character issue limit. The most actionable sections (summary, "
          "security fixes, per-PR review) are shown above; the complete plan is "
          "available as the `merge-plan.md` CI artifact / dry-run output.\n")
budget = LIMIT - len(NOTICE) - 16
if len(body) <= budget:
    print(body, end="")
else:
    head = body[:budget]
    nl = head.rfind("\n")
    if nl > 0:
        head = head[:nl]
    print(head + NOTICE, end="")
PYEOF
)
    echo "  Merge plan body exceeded 65536 chars — truncated to fit GitHub limit"
  fi

  # The plan issue was reserved (and stale plans closed) BEFORE the per-PR loop so
  # comments could link the live number. Update THAT issue in place with the final
  # title + body. Only if reservation failed do we create one now (fallback).
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '%s\n' "$MERGE_PLAN_BODY" > "$DRY_RUN_DIR/merge-plan.md"
    echo "  [dry-run] wrote merge plan -> $DRY_RUN_DIR/merge-plan.md"
  elif [[ -n "${MERGE_PLAN_NUM:-}" ]]; then
    if gh issue edit "$MERGE_PLAN_NUM" --title "$_MP_TITLE" --body "$MERGE_PLAN_BODY_POST" >/dev/null 2>&1; then
      echo "  Updated merge plan issue #$MERGE_PLAN_NUM"
    else
      echo "  ⚠️  Failed to update reserved merge plan issue #$MERGE_PLAN_NUM"
    fi
  else
    NEW_ISSUE=$(gh issue create \
      --title "$_MP_TITLE" \
      --body "$MERGE_PLAN_BODY_POST" \
      --label "$_MP_LABEL" 2>/dev/null || echo "")
    # Retry without the label if the labeled create failed (e.g. label missing).
    if [[ -z "$NEW_ISSUE" ]]; then
      NEW_ISSUE=$(gh issue create \
        --title "$_MP_TITLE" \
        --body "$MERGE_PLAN_BODY_POST" 2>/dev/null || echo "")
    fi
    if [[ -n "$NEW_ISSUE" ]]; then
      echo "  Created merge plan issue: $NEW_ISSUE"
    else
      echo "  ⚠️  Failed to create merge plan issue"
    fi
  fi
else
  echo "  ⚠️  Merge plan generation failed — skipping issue update"
fi
