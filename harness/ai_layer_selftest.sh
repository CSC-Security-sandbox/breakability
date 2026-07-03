#!/usr/bin/env bash
# End-to-end AI-layer selftest -- FULLY OFFLINE (replay cassettes, no agent, no network).
#
# Proves the whole AI adjudication path works deterministically in seconds:
#   1. independent_adjudicate.sh in replay mode -> AI verdicts from frozen cassettes,
#   2. run_gate.py with those verdicts -> asserts FALSE_GREEN=0 and GOLDEN_REGRESSIONS=0.
#
# This is the fast local loop the team was missing: edit logic -> run this -> know in
# seconds whether the AI layer still holds the safety floor, instead of a 1h CI round-trip.
#
# Requires bash >= 4 (mapfile). On macOS default bash 3.2, run with `/opt/homebrew/bin/bash`.
#
# Usage: ai_layer_selftest.sh
set -uo pipefail

HARNESS="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS="$(cd "$HARNESS/../../scripts" && pwd)"
REPO_ROOT="$(cd "$HARNESS/../../.." && pwd)"
FIX="$HARNESS/fixtures"
ARTIFACT="$FIX/build_results_3pr.json"
TMP_VERDICTS="$(mktemp)"
trap 'rm -f "$TMP_VERDICTS"' EXIT

export BRK_AGENT_MODE=replay
export BRK_CASSETTE_DIR="$HARNESS/cassettes"

echo "== [1/2] independent adjudication (replay, offline) =="
bash "$SCRIPTS/independent_adjudicate.sh" "$ARTIFACT" "$TMP_VERDICTS" || {
  echo "FAIL: adjudicator errored"; exit 1; }

echo "== [2/2] gate with replayed AI verdicts =="
OUT="$(python3 "$HARNESS/run_gate.py" "$ARTIFACT" "$HARNESS/corpus.json" --repo "$REPO_ROOT" --ai "$TMP_VERDICTS")"
echo "$OUT"

fg="$(printf '%s\n' "$OUT" | awk -F': ' '/^FALSE_GREEN:/{print $2}')"
gr="$(printf '%s\n' "$OUT" | awk -F': ' '/^GOLDEN_REGRESSIONS:/{print $2}')"
rej="$(printf '%s\n' "$OUT" | awk -F': ' '/^AI_REJECTED:/{print $2}')"

echo
if [ "$fg" = "0" ] && [ "$gr" = "0" ] && [ "$rej" = "0" ]; then
  echo "PASS: AI layer offline replay holds the safety floor (FALSE_GREEN=0, GOLDEN_REGRESSIONS=0, AI_REJECTED=0)"
  exit 0
fi
echo "FAIL: FALSE_GREEN=$fg GOLDEN_REGRESSIONS=$gr AI_REJECTED=$rej (expected 0/0/0)"
exit 1
