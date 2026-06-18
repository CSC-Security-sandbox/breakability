#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# run-local.sh — Run the FULL breakability pipeline locally, fast, no GitHub Actions.
#
# Same scripts the CI workflow (breakability-agent.yml) runs, chained end-to-end on
# your machine so you get feedback in seconds instead of a ~30 min CI round-trip:
#
#   1. deterministic   build-check.sh           -> /tmp/build-results.json   (slow, cacheable)
#   2. policy          policy_lowering.py --enrich
#   3. ai              independent_adjudicate.sh -> /tmp/ai_verdicts.json     (per-PR agent calls)
#   4. reconcile       reconcile_adjudication.py --write
#   5. comments        post-fallback-comments.sh (DRY_RUN by default)
#   6. summary         final verdict table
#
# The slow part is (1). Once you have a build-results.json you can iterate on the AI
# layer (3-5) in seconds with --from ai. Comments are dry-run by default (rendered to
# files, never posted) so you can iterate without touching real PRs. Add --post to
# actually comment, once you're shippable.
#
# Usage:
#   .github/scripts/run-local.sh --prs 10,23,38                 # full run, dry-run comments
#   .github/scripts/run-local.sh --prs 10,23,38 --from ai       # reuse cached build-results, redo AI+
#   .github/scripts/run-local.sh --prs 10 --from reconcile      # reuse cached AI verdicts, redo reconcile+
#   .github/scripts/run-local.sh --prs 10,23,38 --post          # actually post comments
#   .github/scripts/run-local.sh --seed path/to/build-results.json --from ai
#
# Flags:
#   --prs LIST        comma-separated PR numbers (required unless --seed already has them)
#   --from STAGE      start stage: deterministic|probe|policy|ai|reconcile|comments  (default deterministic)
#   --to STAGE        stop after stage (default summary)
#   --model NAME      agent model for AI stage (default claude-4-sonnet)
#   --skip-ai         do not call the agent; rely on Tier-0 deterministic module-scope only
#   --post            actually post comments to GitHub (default: dry-run to files)
#   --seed FILE       copy FILE -> /tmp/build-results.json before starting (reuse a CI artifact)
#   --results FILE    results file path (default /tmp/build-results.json)
# ──────────────────────────────────────────────────────────────────────────────
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
HARNESS="$REPO_ROOT/.github/breakability/harness"

PRS=""
FROM="deterministic"
TO="summary"
MODEL="${ADJ_MODEL:-claude-4-sonnet}"
SKIP_AI=0
POST=0
SEED=""
RESULTS="/tmp/build-results.json"
VERDICTS="/tmp/ai_verdicts.json"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prs)      PRS="$2"; shift 2;;
    --from)     FROM="$2"; shift 2;;
    --to)       TO="$2"; shift 2;;
    --model)    MODEL="$2"; shift 2;;
    --skip-ai)  SKIP_AI=1; shift;;
    --post)     POST=1; shift;;
    --seed)     SEED="$2"; shift 2;;
    --results)  RESULTS="$2"; shift 2;;
    -h|--help)  sed -n '2,46p' "$0"; exit 0;;
    *) echo "unknown flag: $1" >&2; exit 2;;
  esac
done

# Stage ordering so --from/--to can gate.
stage_idx() { case "$1" in
  deterministic) echo 1;; probe) echo 2;; policy) echo 3;; ai) echo 4;; reconcile) echo 5;; comments) echo 6;; summary) echo 7;;
  *) echo 0;; esac; }
FROM_I=$(stage_idx "$FROM"); TO_I=$(stage_idx "$TO")
[[ "$FROM_I" == 0 ]] && { echo "bad --from $FROM" >&2; exit 2; }
[[ "$TO_I"   == 0 ]] && { echo "bad --to $TO" >&2; exit 2; }
run_stage() { local i; i=$(stage_idx "$1"); [[ "$i" -ge "$FROM_I" && "$i" -le "$TO_I" ]]; }

cd "$REPO_ROOT"
# Auto-load a local, gitignored env file so CURSOR_API_KEY (and any registry tokens) are
# picked up without re-exporting every shell. Create .github/breakability/.env.local with:
#   export CURSOR_API_KEY=...
ENV_LOCAL="$REPO_ROOT/.github/breakability/.env.local"
if [[ -f "$ENV_LOCAL" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_LOCAL"; set +a
  echo "loaded local env: $ENV_LOCAL"
fi
hr() { printf '─%.0s' {1..78}; echo; }
t0=$(date +%s)
say() { echo; hr; echo "▶ $*"; hr; }

# ── Preflight ─────────────────────────────────────────────────────────────────
# (a) The self-hosted CI runner lives on this same machine and writes the SAME
#     /tmp/build-results.json. Concurrent local + CI runs clobber each other.
if command -v gh >/dev/null 2>&1; then
  _ACTIVE_CI=$(gh run list --workflow=breakability-agent.yml --status in_progress \
                 --json databaseId -q '.[].databaseId' 2>/dev/null | head -1 || true)
  if [[ -n "${_ACTIVE_CI:-}" ]]; then
    echo "⚠️  A breakability CI run ($_ACTIVE_CI) is IN PROGRESS on the self-hosted runner."
    echo "    It shares /tmp/build-results.json with this machine and will corrupt local results."
    echo "    Cancel it first:  gh run cancel $_ACTIVE_CI"
    echo
  fi
fi
# (b) The AI stage uses a headless agent. Cursor's `agent -p` requires CURSOR_API_KEY
#     even when interactively logged in (the keychain session is ignored headlessly).
#     Other backends authenticate differently: GitHub Copilot CLI (`copilot`) uses your
#     `gh`/Copilot login and needs NO Cursor key. So only gate on the key when the
#     configured backend is actually Cursor's agent.
#     EXCEPTION: replay mode (BRK_AGENT_MODE=replay) reads recorded cassettes and needs
#     no key or network — never skip the AI stage in replay.
_AGENT_PROG="$(basename "$(echo "${BRK_AGENT_CMD:-agent}" | awk '{print $1}')")"
if [[ "$SKIP_AI" == 0 && "$FROM_I" -le 4 && "$TO_I" -ge 4 && "${BRK_AGENT_MODE:-live}" != "replay" \
      && ( "$_AGENT_PROG" == "agent" || "$_AGENT_PROG" == "cursor-agent" ) ]]; then
  if [[ -z "${CURSOR_API_KEY:-}" ]]; then
    echo "⚠️  CURSOR_API_KEY is not set — agent -p (headless) cannot authenticate."
    echo "    The AI stage will be SKIPPED (Tier-0 deterministic module-scope still runs)."
    echo "    To enable the AI layer locally:  export CURSOR_API_KEY=<your key>  (same as the CI secret)"
    echo "    Or use GitHub Copilot CLI:  BRK_AGENT_CMD='copilot --model {model}' BRK_AGENT_MODEL=claude-sonnet-4.5"
    echo "    Or run fully offline from cassettes:  BRK_AGENT_MODE=replay"
    echo
    SKIP_AI=1
  fi
fi

if [[ -n "$SEED" ]]; then
  cp "$SEED" "$RESULTS"
  echo "Seeded $RESULTS from $SEED"
fi

# ── 1. Deterministic ──────────────────────────────────────────────────────────
if run_stage deterministic; then
  say "1/7 deterministic build analysis (PRs: ${PRS:-<all open>})"
  [[ -z "$PRS" ]] && { echo "note: no --prs given; build-check will discover open PRs"; }
  st=$(date +%s)
  PR_FILTER="$PRS" BREAKABILITY_PR_NUMBERS="$PRS" \
    CLI_PATH=".github/actions/breakability-check/index.js" \
    REPO_ROOT="$REPO_ROOT" \
    bash .github/scripts/build-check.sh
  echo "deterministic done in $(( $(date +%s) - st ))s -> $RESULTS"
else
  echo "skip deterministic (--from $FROM); reusing $RESULTS"
fi
[[ -f "$RESULTS" ]] || { echo "no $RESULTS — run the deterministic stage first" >&2; exit 1; }
# the pipeline scripts hardcode /tmp/build-results.json; keep them in sync
[[ "$RESULTS" != "/tmp/build-results.json" ]] && cp "$RESULTS" /tmp/build-results.json

# ── 2. Behavioral differential probe (declared-break residuals only) ──────────
# For PRs that are build/test/api-diff clean but whose changelog declares a
# behavioral break that our prod code imports, build+RUN the dependency at the
# from/to versions and map the change to OUR call site. Emits behavioral_grade
# (none/low/medium/high) which policy lowering folds into the PROBE signal. The
# driver self-selects residuals, is cost-bounded (DP_MAX_PRS), fail-open (any
# failure -> Medium, never a false low), and sandboxed. No-op when no residuals.
if run_stage probe; then
  if [[ "$SKIP_AI" == 1 ]]; then
    # The npm runtime-shape probe is deterministic (installs old/new from the public
    # registry and diffs the runtime surface) and needs no agent — run it even with
    # --skip-ai. Residuals that require the AI agent are skipped in this mode.
    say "2/7 deterministic npm runtime-shape probe (no agent; --skip-ai)"
    st=$(date +%s)
    DP_DETERMINISTIC_ONLY=1 \
    DP_RESULTS=/tmp/build-results.json \
    DP_REPO_ROOT="$REPO_ROOT" \
      python3 .github/scripts/differential-probe.py \
      && echo "deterministic npm probe done in $(( $(date +%s) - st ))s" \
      || echo "[warn] deterministic npm probe failed; residuals stay at deterministic verdict"
  else
    say "2/7 behavioral differential probe (model: $MODEL)"
    st=$(date +%s)
    # Reuse the same agent backend as the AI stage; differential-probe.py
    # auto-completes copilot/cursor args and substitutes {model} from DP_AGENT_MODEL.
    DP_AGENT_CMD="${BRK_AGENT_CMD:-agent -p --force --model claude-4-sonnet}" \
    DP_AGENT_MODEL="$MODEL" \
    DP_RESULTS=/tmp/build-results.json \
    DP_REPO_ROOT="$REPO_ROOT" \
      python3 .github/scripts/differential-probe.py \
      && echo "behavioral probe done in $(( $(date +%s) - st ))s" \
      || echo "[warn] behavioral probe failed; residuals stay at deterministic verdict"
  fi
fi

# ── 3. Policy lowering ────────────────────────────────────────────────────────
if run_stage policy; then
  say "3/7 policy lowering (enrich verdicts)"
  # M8 changelog comprehension: enrich each PR with structured breaking_claims[]
  # (symbol/kind/old/new/severity) so reachability has named symbols to check, not
  # just a '### Breaking Changes' heading. Advisory + non-breaking: never changes a
  # verdict here. Set M8_USE_AI=1 (and BRK_AGENT_MODE) to enable AI enrichment.
  if [[ -f .github/scripts/changelog_comprehension.py ]]; then
    python3 .github/scripts/changelog_comprehension.py /tmp/build-results.json \
      ${M8_USE_AI:+--ai} --write \
      && echo "M8 changelog comprehension applied" \
      || echo "[warn] M8 changelog comprehension failed; continuing"
  fi
  if [[ -f .github/scripts/policy_lowering.py ]]; then
    python3 .github/scripts/policy_lowering.py /tmp/build-results.json --enrich -o /tmp/build-results.policy.json \
      && mv /tmp/build-results.policy.json /tmp/build-results.json \
      && echo "policy lowering applied" \
      || echo "[warn] policy lowering failed; continuing with raw verdicts"
  else
    echo "no policy_lowering.py; skipping"
  fi
fi

# ── 3. Independent AI adjudication (per-PR) ───────────────────────────────────
if run_stage ai; then
  say "4/7 independent AI adjudication (model: $MODEL, skip_ai=$SKIP_AI)"
  if [[ "$SKIP_AI" == 1 ]]; then
    echo '{}' > "$VERDICTS"
    echo "skip-ai: empty verdicts (Tier-0 deterministic module-scope still applies in reconcile)"
  else
    st=$(date +%s)
    bash .github/scripts/independent_adjudicate.sh /tmp/build-results.json "$VERDICTS" "$MODEL"
    echo "ai adjudication done in $(( $(date +%s) - st ))s -> $VERDICTS"
  fi
fi

# ── 4. Reconcile AI + deterministic ───────────────────────────────────────────
if run_stage reconcile; then
  say "5/7 reconcile AI + deterministic verdicts"
  V=""
  [[ -f "$VERDICTS" ]] && V="--verdicts $VERDICTS"
  python3 .github/scripts/reconcile_adjudication.py /tmp/build-results.json $V --repo "$REPO_ROOT" --write
fi

# ── 5. Comments (dry-run unless --post) ───────────────────────────────────────
if run_stage comments; then
  if [[ "$POST" == 1 ]]; then
    say "6/7 posting comments to GitHub (LIVE)"
    DRY_RUN=0 bash .github/scripts/post-fallback-comments.sh
  else
    say "6/7 rendering comments (DRY-RUN -> /tmp/breakability-local/comments)"
    DRY_RUN=1 DRY_RUN_DIR=/tmp/breakability-local/comments bash .github/scripts/post-fallback-comments.sh
    echo "rendered comments:"; ls -1 /tmp/breakability-local/comments/ 2>/dev/null | sed 's/^/  /' || true
  fi
fi

# ── 6. Summary table ──────────────────────────────────────────────────────────
if run_stage summary; then
  say "7/7 final verdicts"
  python3 - /tmp/build-results.json <<'PY'
import json, os, sys
sys.path.insert(0, os.path.join(os.getcwd(), ".github", "scripts"))
from verdict_contract import authoritative_verdict, prediction_for_pr, stage_report
d = json.load(open(sys.argv[1]))
prs = d.get("prs") or {}
rows = []
review_prs, ai_applied = 0, 0
for k, p in prs.items():
    av = authoritative_verdict(p)            # the SINGLE source of truth
    verdict = av.get("verdict", "?")
    conf = av.get("confidence", "-")
    src = av.get("source", "-")
    if verdict == "REVIEW":
        review_prs += 1
    if str((p.get("ai_adjudication") or {}).get("applied") or ""):
        ai_applied += 1
    pkg = p.get("package") or "?"
    rows.append((int(k) if str(k).isdigit() else k, verdict, conf, src, pkg))
rows.sort(key=lambda r: str(r[0]))
print(f"{'PR':>5}  {'VERDICT':<8} {'CONF':<5} {'SOURCE':<16} PACKAGE")
for r in rows:
    print(f"{r[0]:>5}  {r[1]:<8} {r[2]:<5} {r[3]:<16} {r[4]}")
# Loud AI-coverage visibility — surfaces the "dormant AI layer" failure immediately
# instead of weeks later. (Not a hard fail: a REVIEW PR may legitimately need no AI.)
print()
print(stage_report("ai-coverage", input_count=review_prs, processed_count=ai_applied))
if review_prs > 0 and ai_applied == 0:
    print("  [warn] AI layer applied 0 verdicts to %d REVIEW PR(s) — check the agent ran "
          "(auth/keychain/field-mismatch), not silently dormant." % review_prs)
PY
fi

echo
echo "total: $(( $(date +%s) - t0 ))s   results: /tmp/build-results.json"
