#!/usr/bin/env bash
# Independent per-PR AI adjudication (PRD: the AI does its OWN analysis and AUDITS the
# deterministic layer; it is not a formatter over the deterministic verdict). Replaces the
# single 1122-line mega-call with one small, bounded, module-scoped call PER residue PR.
#
# For each PR the deterministic layer marked REVIEW for a break-reachable API change AND where
# the dependency is actually imported in the bumped module (Tier-0 could not auto-clear it):
#   1. render a tiny module-scoped 3-phase prompt (render_prompt.py),
#   2. run `agent -p --force` once for that PR (independent-first, then audits deterministic),
#   3. schema-validate the output (validate_adjudication.py) — invented citations / unshown
#      work are rejected,
#   4. append the validated, normalized verdict to the verdicts file.
#
# The verdicts file is consumed by reconcile_adjudication.py. If the agent is unavailable or a
# call fails/validation rejects, that PR simply stays REVIEW (fail-safe) and Tier-0 still
# protects the obvious not-imported cases.
#
# Usage: independent_adjudicate.sh <build-results.json> <out-verdicts.json> [model]
set -uo pipefail

RESULTS="${1:?build-results.json required}"
OUT="${2:?out verdicts path required}"
MODEL="${3:-claude-4-sonnet}"
HARNESS="$(cd "$(dirname "$0")/../breakability/harness" && pwd)"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPTS="$(cd "$(dirname "$0")" && pwd)"

# Backend mode: live (default) | replay | record. In replay mode the AI layer runs
# fully offline from cassettes (sub-second, no agent CLI, no keychain) so the local
# loop and unit tests are deterministic. Selected via BRK_AGENT_MODE.
MODE="${BRK_AGENT_MODE:-live}"
export BRK_AGENT_MODEL="${BRK_AGENT_MODEL:-$MODEL}"

# The agent CLI is only required for live/record. In replay we need nothing.
if [ "$MODE" != "replay" ]; then
  command -v agent >/dev/null 2>&1 || { echo "[independent] agent CLI not found; skipping (Tier-0 only)"; echo '{}' > "$OUT"; exit 0; }
fi
[ -f "$RESULTS" ] || { echo "[independent] no results file; skipping"; echo '{}' > "$OUT"; exit 0; }

# Which PRs need independent adjudication? REVIEW + break-reachable + dep imported in module.
mapfile -t PR_IDS < <(HARNESS="$HARNESS" python3 - "$RESULTS" <<'PY'
import json, os, sys
sys.path.insert(0, os.environ["HARNESS"])
import render_prompt as rp
prs = json.load(open(sys.argv[1])).get("prs", {})
def imported(pr, mod):
    fi = [ (f if isinstance(f, str) else f.get("file", "")) for f in (pr.get("files_importing") or []) ]
    if any(rp.in_module(f, mod) for f in fi):
        return True
    us = (pr.get("deterministic") or {}).get("usages") or []
    return any(rp.in_module(u.get("file", ""), mod) for u in us)
for pid, pr in prs.items():
    v2 = pr.get("verdict_v2") or {}
    pol = (pr.get("policy_lowering") or {}).get("decision") or {}
    verdict = (v2.get("verdict") or pol.get("verdict") or "").upper()
    reason = ((v2.get("residual") or {}).get("check") or v2.get("reason")
              or pol.get("reason_code") or "")
    es = v2.get("evidenceState") or {}
    det = pr.get("deterministic") or {}
    api_changes = len(det.get("api_changes_detail") or [])
    is_br = ("break-reachable" in reason) or (es.get("api_diff") == "POSITIVE")
    is_declared_breaking = "declared-breaking" in reason
    # Adjudicate any REVIEW whose hold is driven by a breakability signal the AI can resolve
    # by reading the consumer code: a reachable break, real apidiff changes, or a declared
    # breaking changelog. (Pure security/CVE holds are still surfaced to the AI for a
    # reachability read, but reconcile never CLEARS them — that floor lives there.)
    needs_ai = is_br or api_changes > 0 or is_declared_breaking
    if verdict == "REVIEW" and needs_ai and imported(pr, rp.module_dir(pr)):
        print(pid)
PY
)

echo "[independent] PRs needing AI adjudication: ${PR_IDS[*]:-none}"
echo "{" > "$OUT.tmp"
first=1
for pid in "${PR_IDS[@]}"; do
  [ -z "$pid" ] && continue
  prompt="$(HARNESS="$HARNESS" python3 "$HARNESS/render_prompt.py" "$RESULTS" "$pid" "$HARNESS/ai_adjudicator_prompt.md" 2>/dev/null)"
  [ -z "$prompt" ] && { echo "[independent] PR#$pid: render failed, skip"; continue; }
  echo "[independent] PR#$pid: invoking backend (mode=$MODE model=$BRK_AGENT_MODEL)..."
  # Route through the unified backend (live | replay | record) with a stable,
  # portable cassette key so replay works on any machine.
  raw="$(printf '%s' "$prompt" | python3 "$SCRIPTS/ai_backend.py" --namespace adjudication --key "pr-$pid" --cwd "$REPO_ROOT" 2>/dev/null)"
  if [ -z "$raw" ]; then echo "[independent] PR#$pid: empty agent output, skip"; continue; fi
  norm="$(printf '%s' "$raw" | python3 "$HARNESS/validate_adjudication.py" --repo "$REPO_ROOT" 2>/dev/null)"
  acc="$(printf '%s' "$norm" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("accepted"))' 2>/dev/null)"
  if [ "$acc" != "True" ]; then echo "[independent] PR#$pid: REJECTED ($norm)"; continue; fi
  # The validator already emitted a normalized, trusted object — use it directly (drop "accepted").
  entry="$(printf '%s' "$norm" | python3 -c 'import json,sys;d=json.load(sys.stdin);d.pop("accepted",None);print(json.dumps(d))' 2>/dev/null)"
  [ -z "$entry" ] && continue
  [ $first -eq 0 ] && echo "," >> "$OUT.tmp"
  printf '  "%s": %s' "$pid" "$entry" >> "$OUT.tmp"
  first=0
  echo "[independent] PR#$pid: ACCEPTED ($(printf '%s' "$norm" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("final_verdict"))'))"
done
echo "" >> "$OUT.tmp"
echo "}" >> "$OUT.tmp"
mv "$OUT.tmp" "$OUT"
echo "[independent] wrote verdicts -> $OUT"
cat "$OUT"
