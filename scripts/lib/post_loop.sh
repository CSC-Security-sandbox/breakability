#!/usr/bin/env bash
# post_loop.sh — post-loop aggregation (worktree cleanup, cross-PR deps,
#                security posture, coverage gap, final banner)
# Sourced by build-check.sh; reads globals set there.

post_loop_aggregation() {
  # Clean up main worktree (kept alive for lazy baselines during PR processing)
  git worktree remove "$MAIN_DIR" --force 2>/dev/null || { chmod -R u+w "$MAIN_DIR" 2>/dev/null; rm -rf "$MAIN_DIR" 2>/dev/null; } || true

  # ── In batch mode, skip cross-PR / security / cleanup (merge script handles those) ──
  if [[ -n "$BATCH_ID" ]]; then
    # Embed main baseline vuln scan into batch JSON so merge-results.sh can aggregate it
    RESULTS_FILE="$RESULTS_FILE" python3 "$BRK_SCRIPTS/batch_vuln_summary.py"
    echo ""
    echo "═══════════════════════════════════════════════════════════════════"
    echo "  BATCH $BATCH_ID COMPLETE"
    echo "  Results: $RESULTS_FILE"
    echo "  PRs processed: $PR_COUNT"
    echo "═══════════════════════════════════════════════════════════════════"
    exit 0
  fi

  # ── Cross-PR dependency detection ────────────────────────────────────────────
  echo ""
  echo "════════════ CROSS-PR DEPENDENCIES ════════════"

  RESULTS_FILE="$RESULTS_FILE" python3 "$BRK_SCRIPTS/cross_pr_deps.py"

  # ── Security posture scan ────────────────────────────────────────────────────
  echo ""
  echo "════════════ SECURITY POSTURE ════════════"
  OWNER_REPO="$OWNER_REPO" RESULTS_FILE="$RESULTS_FILE" python3 "$BRK_SCRIPTS/security_posture_scan.py"


  # ── Comment cleanup ──────────────────────────────────────────────────────────
  # CR3-2: Removed duplicate cleanup code. Comment cleanup is now handled exclusively
  # by merge-results.sh (batch mode) or post-fallback-comments.sh (which does per-PR
  # atomic delete+post). Having cleanup in both build-check.sh and merge-results.sh
  # risked divergence and created a window where PRs had no comments.
  echo ""
  echo "════════════ COMMENT CLEANUP ════════════"
  echo "  Skipped — cleanup handled by merge-results.sh / post-fallback-comments.sh"

  # ── Summary ──────────────────────────────────────────────────────────────────
  echo ""
  echo "═══════════════════════════════════════════════════════════════════"
  echo "  COMPLETE"
  echo "  Results: $RESULTS_FILE"
  echo "  PRs processed: $PR_COUNT"
  echo "  Diffs saved: /tmp/pr-{N}.diff"

  # ── Coverage gap detection ───────────────────────────────────────────────────
  if [[ -n "${PR_FILTER:-}" ]]; then
    EXPECTED=$(echo "$PR_FILTER" | tr ',' '\n' | grep -c . || echo 0)
    ACTUAL=$(python3 -c "import json; print(len(json.load(open('$RESULTS_FILE')).get('prs', {})))" 2>/dev/null || echo 0)
    if [[ "$ACTUAL" -lt "$EXPECTED" ]]; then
      echo "  ::warning::Coverage gap: expected $EXPECTED PRs from filter, analyzed $ACTUAL"
      echo "  Missing PRs may have been closed, are not labeled 'dependencies', or exceeded the API limit."
    else
      echo "  Coverage: $ACTUAL / $EXPECTED PRs analyzed (100%)"
    fi
  fi

  echo "═══════════════════════════════════════════════════════════════════"
}
