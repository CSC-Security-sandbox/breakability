#!/usr/bin/env bash
# run_ai_layer.sh — end-to-end, locally runnable demonstration of the breakability AI layer.
#
# WHERE THIS RUNS: locally, against this checkout. No GitHub Actions, no push required.
# It reproduces exactly what was demonstrated:
#   1. DETERMINISTIC residue narrowing  (scope_check.py — cheap module-scope filter, no callgraph)
#   2. AI ADJUDICATION of the residue    (real `agent` CLI per-PR if available, else replay
#                                         the captured ai_verdicts.json from a prior agent run)
#   3. GATE VALIDATION + scoring          (run_gate.py --ai — schema-checks every AI verdict,
#                                         scores vs verified corpus, reports AI-on vs AI-off)
#
# Usage:  bash .github/breakability/harness/run_ai_layer.sh [build-results.json]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
RESULTS="${1:-/tmp/build-results.json}"
CORPUS="$HERE/corpus.json"
PROMPT="$HERE/ai_adjudicator_prompt.md"
VERDICTS="$HERE/ai_verdicts.json"
RESIDUE_PRS=(38 23 10)   # dynamic cases: cross-module, behavioral, removed-symbol

if [[ ! -f "$RESULTS" ]]; then
  echo "build-results.json not found at $RESULTS"
  echo "fetch it with: git fetch origin breakability-results && git show FETCH_HEAD:build-results.json > /tmp/build-results.json"
  exit 2
fi

echo "==================================================================="
echo " STEP 1/3  DETERMINISTIC residue narrowing (scope_check.py, no callgraph)"
echo "==================================================================="
python3 "$HERE/scope_check.py" "$RESULTS" "${RESIDUE_PRS[@]}"

echo
echo "==================================================================="
echo " STEP 2/3  AI adjudication of the residue"
echo "==================================================================="
# Default is REPLAY (reproducible). Pass --live as 2nd arg to invoke the real agent CLI.
# CRITICAL: the AI layer must NEVER fail silently — if live yields zero verdicts we ABORT,
# because a silently-empty AI layer is exactly the production bug we are fixing.
if [[ "${2:-}" == "--live" ]]; then
  command -v agent >/dev/null 2>&1 || { echo "ERROR: --live requested but 'agent' CLI not on PATH"; exit 3; }
  echo "[live] running real per-PR AI adjudication via 'agent' CLI."
  TMP="$(mktemp -d)"; echo "{" > "$TMP/v.json"; first=1; got=0
  for pr in "${RESIDUE_PRS[@]}"; do
    rendered="$(python3 "$HERE/render_prompt.py" "$RESULTS" "$pr" "$PROMPT" 2>/dev/null || true)"
    [[ -z "$rendered" ]] && { echo "  PR#$pr: prompt render failed, skipping"; continue; }
    out="$(agent -p --model claude-4-sonnet "$rendered" 2>/dev/null || true)"
    json="$(printf '%s' "$out" | python3 -c 'import sys,re;m=re.search(r"\{.*\}",sys.stdin.read(),re.S);print(m.group(0) if m else "")' || true)"
    if [[ -n "$json" ]]; then
      [[ $first -eq 0 ]] && echo "," >> "$TMP/v.json"; first=0; got=$((got+1))
      echo "\"$pr\": $json" >> "$TMP/v.json"
      echo "  PR#$pr: got verdict"
    else
      echo "  PR#$pr: agent returned NO parseable JSON"
    fi
  done
  echo "}" >> "$TMP/v.json"
  if [[ $got -eq 0 ]]; then
    echo "ERROR: AI layer produced ZERO verdicts. Refusing to proceed with a silently-empty AI"
    echo "       layer (this is the dormant-AI failure mode). Fix the agent invocation first."
    exit 4
  fi
  VERDICTS="$TMP/v.json"
  echo "[live] wrote $got verdict(s) -> $VERDICTS"
else
  echo "[replay] replaying captured AI verdicts from a prior grounded-agent run:"
  echo "         $VERDICTS"
  echo "         (real outputs of 3 Sonnet adjudicator agents; pass --live to re-run the agent CLI.)"
fi
echo
echo "--- AI verdicts being fed to the gate ---"
python3 -c "import json;d=json.load(open('$VERDICTS'));[print(f'  PR#{k}: {v[\"recommendation\"]:6} reachable={v[\"reachable\"]!s:9} cite={v.get(\"citation\") or \"(none)\"}') for k,v in d.items() if k!='_note']"

echo
echo "==================================================================="
echo " STEP 3/3  GATE validation + scoring (run_gate.py --ai)"
echo "==================================================================="
python3 "$HERE/run_gate.py" "$RESULTS" "$CORPUS" \
  --repo "$REPO_ROOT" \
  --golden "$HERE/golden_predictions.json" \
  --ai "$VERDICTS" || true

echo
echo "Interpretation:"
echo "  AI_REJECTED  = AI verdict failed falsifiability (e.g. #38 'safe' with empty citation)."
echo "  AI_PROOF_ADDED = REVIEW kept but now backed by a real call site (e.g. #23 metric.go:72)."
echo "  FALSE_GREEN  = an AI downgrade that contradicts the verified corpus (e.g. #10) — caught,"
echo "                 never trusted over ground truth. This is the safety net working."
