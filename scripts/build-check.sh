#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# build-check.sh — Deterministic JSON producer for breakability analysis
#
# Runs TS pipeline CLI + ecosystem-specific builds for each Dependabot PR,
# produces /tmp/build-results.json with structured analysis data.
# ──────────────────────────────────────────────────────────────────────────────
set -u
export LC_ALL=en_US.UTF-8
unset GH_TOKEN

BRK_SCRIPTS="${BREAKABILITY_SCRIPTS_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

source "$BRK_SCRIPTS/lib/common.sh"
source "$BRK_SCRIPTS/lib/config.sh"
source "$BRK_SCRIPTS/lib/detection.sh"
source "$BRK_SCRIPTS/lib/ecosystem_npm.sh"
source "$BRK_SCRIPTS/lib/ecosystem_pip.sh"
source "$BRK_SCRIPTS/lib/ecosystem_go.sh"
source "$BRK_SCRIPTS/lib/post_loop.sh"
source "$BRK_SCRIPTS/lib/baseline_builds.sh"
source "$BRK_SCRIPTS/lib/pr_metadata.sh"
source "$BRK_SCRIPTS/lib/pr_build.sh"
source "$BRK_SCRIPTS/lib/pr_test.sh"

TIMEOUT=120
DIFF_MAX_LINES=500
BATCH_ID="${BATCH_ID:-}"
if [[ -n "$BATCH_ID" ]]; then
  RESULTS_FILE="/tmp/build-results-${BATCH_ID}.json"
else
  RESULTS_FILE="/tmp/build-results.json"
fi
CLI_PATH="${CLI_PATH:-$(dirname "$BRK_SCRIPTS")/.github/actions/breakability-check/index.js}"
REPO_ROOT="$(pwd)"
PR_FILTER="${PR_FILTER:-${BREAKABILITY_PR_NUMBERS:-}}"

# ── Per-batch Go BUILD-cache isolation (race-safety) ──────────────────────────
# All self-hosted batch runners share one $HOME on the same machine, so they
# share the default GOCACHE (~/Library/Caches/go-build). This script calls
# `go clean -cache` at several sites; one batch's clean deletes cache entries a
# parallel batch is mid-build against -> "no such file or directory" /
# "package ... is not in std" corruption -> degraded build output -> thin
# comments. Isolate the BUILD cache per batch HERE (in-script) so the race is
# closed even when the workflow does not (or cannot) set GOCACHE — e.g. the
# job-level `env: GOCACHE: ${{ runner.temp }}/...` is an INVALID expression
# (the `runner` context is unavailable at job-level env), which fails the whole
# workflow at 0s. With this in-script guard the orchestrator can simply DELETE
# that broken workflow line; cache isolation no longer depends on it.
# GOMODCACHE stays shared/warm: `go clean -cache` never touches the module cache.
if [[ -z "${GOCACHE:-}" || "${GOCACHE:-}" != *"go-build-cache-"* ]]; then
  _bc_cache_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  export GOCACHE="${_bc_cache_root%/}/go-build-cache-${BATCH_ID:-default}"
  mkdir -p "$GOCACHE" 2>/dev/null || true
fi


export BC_SCRATCH_DIR="${BC_SCRATCH_DIR:-$REPO_ROOT/.breakability-scratch}"
mkdir -p "$BC_SCRATCH_DIR"
WORKTREE_BASE="/tmp/worktree"

# ── Private Registry Configuration ───────────────────────────────────────────
# Reads .github/breakability-config.yml and sets up .npmrc for private registries.
# This lets npm ci resolve private scoped packages without falling back to file: links.
BC_CONFIG="${REPO_ROOT}/.github/breakability-config.yml"
PRIVATE_REGISTRY_CONFIGURED=false
BC_MODE="advisory"

GO_AVAILABLE=false
if command -v go &>/dev/null; then
  GO_AVAILABLE=true
  GO_VERSION=$(go version 2>/dev/null | head -1 || echo "unknown")
fi


echo "═══════════════════════════════════════════════════════════════════"
echo "  Breakability Deterministic Analysis"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"

cd "$REPO_ROOT"

# ── Discover repo info ────────────────────────────────────────────────────────
OWNER_REPO=$(gh repo view --json nameWithOwner -q '.nameWithOwner' 2>/dev/null || echo "unknown/unknown")
OWNER="${OWNER_REPO%%/*}"
REPO="${OWNER_REPO##*/}"
echo "Repo: $OWNER_REPO"

# Load analysis mode from breakability-config.yml (advisory | enforce; default: advisory)
BC_MODE=$(_parse_bc_config | python3 -c "import json,sys; print(json.load(sys.stdin).get('mode','advisory'))" 2>/dev/null || echo "advisory")
echo "Mode: $BC_MODE"

# ── Discover Dependabot PRs ──────────────────────────────────────────────────
echo ""
echo "Discovering Dependabot PRs..."
PR_JSON=$(gh pr list --label "dependencies" --state open \
  --json number,title,headRefName,body,labels --limit 500 2>&1) || {
  echo "  ERROR: gh pr list failed: $PR_JSON" >&2
  PR_JSON='[]'
}
if ! echo "$PR_JSON" | jq -e '.' >/dev/null 2>&1; then
  echo "  ERROR: Invalid JSON from gh pr list, treating as empty" >&2
  PR_JSON='[]'
fi

PR_COUNT=$(echo "$PR_JSON" | jq length)
echo "Found $PR_COUNT open Dependabot PRs"

# Apply PR_FILTER/BREAKABILITY_PR_NUMBERS if set (comma-separated PR numbers).
# CR5-11: Pass PR_FILTER via env var read by Python, not shell expansion into code,
# to eliminate injection risk from workflow_dispatch input.
if [[ -n "${PR_FILTER:-}" ]]; then
  echo "PR_FILTER set: $PR_FILTER"
  FILTERED_JSON=$(echo "$PR_JSON" | _BC_PR_FILTER="$PR_FILTER" python3 "$BRK_SCRIPTS/core/pr_utils.py" filter_prs)
  PR_JSON="$FILTERED_JSON"
  PR_COUNT=$(echo "$PR_JSON" | jq length)
  echo "After filter: $PR_COUNT PRs to analyze"
fi

# ── Initialize JSON result ────────────────────────────────────────────────────
cat > "$RESULTS_FILE" <<EOF
{
  "metadata": {
    "repo": "$OWNER_REPO",
    "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
    "pr_count": $PR_COUNT,
    "cli_path": "$CLI_PATH",
    "mode": "$BC_MODE"
  },
  "main_build": {},
  "prs": {},
  "cross_pr_deps": []
}
EOF

if [[ -n "${PR_FILTER:-}" ]]; then
  _BC_PR_FILTER="$PR_FILTER" python3 - "$RESULTS_FILE" <<'PY'
import json, os, re, sys

path = sys.argv[1]
requested = []
seen = set()
for token in os.environ.get("_BC_PR_FILTER", "").split(","):
    token = token.strip().lstrip("#")
    if not re.fullmatch(r"[0-9]+", token or ""):
        continue
    if token not in seen:
        seen.add(token)
        requested.append(int(token))

with open(path) as f:
    data = json.load(f)
meta = data.setdefault("metadata", {})
meta["subset_requested"] = True
meta["requested_pr_numbers"] = requested
with open(path, "w") as f:
    json.dump(data, f, indent=2)
PY
fi

run_baseline_builds

# ── Build workspace dependency graph ─────────────────────────────────
echo ""
echo "════════════ WORKSPACE DEPENDENCY GRAPH ════════════"
build_workspace_dep_graph "$REPO_ROOT"

echo ""
echo "════════════ DYNAMIC PEER DEPENDENCY DISCOVERY ════════════"
REPO_ROOT="$REPO_ROOT" python3 "$BRK_SCRIPTS/discover_peer_groups.py"


# ── Pre-fetch Dependabot alerts for per-PR CVE enrichment ────────────────────
# Dependabot PRs often do NOT mention CVE/GHSA IDs in the PR body.
# We fetch all open alerts once and cache them so each PR can look up its CVEs.
echo ""
echo "════════════ DEPENDABOT ALERTS CACHE ════════════"
_BC_ALERTS_CACHE="/tmp/_bc_dependabot_alerts.json"
_BC_ALERTS_RAW="/tmp/_bc_dependabot_alerts_raw.json"
_BC_ALERTS_ERR="/tmp/_bc_dependabot_alerts_err.txt"
# Dependabot alerts require a token with Dependabot-alerts:read. The default GITHUB_TOKEN
# usually cannot list them, so prefer BREAKABILITY_PAT when provided (same token the
# security-posture scan uses). Without this the per-PR CVE cache is silently empty.
_BC_ALERTS_TOKEN="${BREAKABILITY_PAT:-${GH_TOKEN:-${GITHUB_TOKEN:-}}}"
if GH_TOKEN="$_BC_ALERTS_TOKEN" GITHUB_TOKEN="$_BC_ALERTS_TOKEN" gh api "repos/$OWNER_REPO/dependabot/alerts?state=open&per_page=100" --paginate > "$_BC_ALERTS_RAW" 2>"$_BC_ALERTS_ERR"; then
  # gh --paginate outputs one JSON array per page; merge them into a single array
  python3 "$BRK_SCRIPTS/core/pr_utils.py" merge_alerts "$_BC_ALERTS_CACHE" < "$_BC_ALERTS_RAW"
  _ALERT_COUNT=$?
  _ALERT_COUNT=$(python3 -c "import json; print(len(json.load(open('$_BC_ALERTS_CACHE'))))" 2>/dev/null || echo 0)
  echo "  Cached $_ALERT_COUNT open Dependabot alerts"
else
  echo "[]" > "$_BC_ALERTS_CACHE"
  echo "  Could not fetch Dependabot alerts (permissions or no alerts)"
  if [[ -s "$_BC_ALERTS_ERR" ]]; then
    echo "  reason: $(head -c 200 "$_BC_ALERTS_ERR" | tr '\n' ' ')"
  fi
  if [[ -z "${BREAKABILITY_PAT:-}" ]]; then
    echo "  hint: BREAKABILITY_PAT is not set; GITHUB_TOKEN usually cannot read Dependabot alerts. Set a fine-grained PAT with Dependabot alerts:read."
  fi
fi

# ── Process each PR ──────────────────────────────────────────────────────────
echo ""
echo "════════════ PROCESSING PRs ════════════"

for i in $(seq 0 $(( PR_COUNT - 1 )) ); do
  PR_NUM=$(echo "$PR_JSON" | jq -r ".[$i].number")
  PR_TITLE=$(echo "$PR_JSON" | jq -r ".[$i].title")
  PR_BRANCH=$(echo "$PR_JSON" | jq -r ".[$i].headRefName")
  PR_BODY=$(echo "$PR_JSON" | jq -r ".[$i].body // \"\"")

  echo ""
  echo "──── PR #$PR_NUM: $PR_TITLE ────"

  # Respect breakability:skip label — opt-out for PRs that should bypass analysis
  PR_SKIP=$(echo "$PR_JSON" | jq -r ".[$i].labels[] | select(.name==\"breakability:skip\") | .name" 2>/dev/null | head -1)
  if [[ -n "$PR_SKIP" ]]; then
    echo "  ⏭️  SKIP — breakability:skip label found on PR #$PR_NUM"
    # Write a minimal skip entry so this PR appears in results (avoids pr_count mismatch
    # and lets the agent/fallback scripts acknowledge it was seen and intentionally skipped).
    _SKIP_BRANCH="$PR_BRANCH"
    _SKIP_TITLE="$PR_TITLE"
    # Write user-derived PR title to temp file to avoid shell injection in heredoc (Finding-4.1)
    printf '%s' "$_SKIP_TITLE" > "/tmp/_bc_skip_title_${PR_NUM}.txt"
    RESULTS_FILE="$RESULTS_FILE" PR_NUM="$PR_NUM" _SKIP_BRANCH="$_SKIP_BRANCH" python3 "$BRK_SCRIPTS/write_skip_entry.py"
    continue
  fi

  # Skip non-Dependabot PRs (safety guard — label filter should catch these)
  if [[ "$PR_BRANCH" != dependabot/* ]]; then
    echo "  ⏭️  SKIP — not a Dependabot branch: $PR_BRANCH"
    continue
  fi

  setup_pr_metadata

  run_pr_build

  run_pr_test

  # ── Write PR data to JSON ──────────────────────────────────────
  # Write build and test output to temp files for safe JSON encoding.
  # User-derived strings (PR titles, config patterns, package names) are written
  # to temp files and read from Python, avoiding shell-to-Python injection via
  # the unquoted heredoc. This prevents Python-hostile chars (quotes, backslashes)
  # in PR titles or config patterns from crashing the heredoc (Finding-3.2).
  echo "$BUILD_OUTPUT" | tail -n 80 > "/tmp/_bc_build_out_${PR_NUM}.txt"
  echo "$TEST_OUTPUT" | tail -n 80 > "/tmp/_bc_test_out_${PR_NUM}.txt"
  printf '%s' "$EVIDENCE_DEP_COMMAND" > "$BC_SCRATCH_DIR/_bc_evidence_dep_command_${PR_NUM}.txt"
  printf '%s' "$EVIDENCE_BUILD_COMMAND" > "$BC_SCRATCH_DIR/_bc_evidence_build_command_${PR_NUM}.txt"
  printf '%s' "$EVIDENCE_TEST_COMMAND" > "$BC_SCRATCH_DIR/_bc_evidence_test_command_${PR_NUM}.txt"
  printf '%s' "$EVIDENCE_SMOKE_COMMAND" > "$BC_SCRATCH_DIR/_bc_evidence_smoke_command_${PR_NUM}.txt"
  printf '%s' "$EVIDENCE_SMOKE_OUTPUT" > "$BC_SCRATCH_DIR/_bc_smoke_output_${PR_NUM}.txt"
  printf '%s' "$EVIDENCE_SMOKE_EXIT" > "$BC_SCRATCH_DIR/_bc_smoke_exit_${PR_NUM}.txt"
  printf '%s' "${AUDIT_JSON:-}" > "$BC_SCRATCH_DIR/_bc_npm_audit_output_${PR_NUM}.txt"
  echo "$NEW_ERRORS" > "/tmp/_bc_new_errors_${PR_NUM}.txt"
  printf '%s' "${GOSUM_NEW_COUNT:-0}" > "/tmp/_bc_gosum_new_${PR_NUM}.txt"
  printf '%s' "${GOSUM_NEW_NAMES:-}" > "/tmp/_bc_gosum_names_${PR_NUM}.txt"
  printf '%s' "${GOSUM_TOTAL_PR:-0}" > "/tmp/_bc_gosum_total_pr_${PR_NUM}.txt"
  printf '%s' "${GOSUM_TOTAL_MAIN:-0}" > "/tmp/_bc_gosum_total_main_${PR_NUM}.txt"
  echo "$DETERMINISTIC" > "/tmp/_bc_det_${PR_NUM}.json"
  echo "$FILES_IMPORTING" > "/tmp/_bc_files_${PR_NUM}.json"
  printf '%s' "$CASCADE_IMPACT" > "/tmp/_bc_cascade_${PR_NUM}.txt"
  printf '%s' "$NESTJS_PEER_WARNING" > "/tmp/_bc_peer_warn_${PR_NUM}.txt"
  printf '%s' "$ADDITIONAL_PACKAGES" > "/tmp/_bc_addl_pkgs_${PR_NUM}.txt"
  printf '%s' "$ADDITIONAL_IMPORTS" > "/tmp/_bc_addl_imports_${PR_NUM}.json"
  # Write PR metadata to temp files to avoid shell injection in heredoc (Finding-4.4)
  printf '%s' "$PKG" > "/tmp/_bc_pkg_${PR_NUM}.txt"
  printf '%s' "$FROM_VER" > "/tmp/_bc_from_ver_${PR_NUM}.txt"
  printf '%s' "$TO_VER" > "/tmp/_bc_to_ver_${PR_NUM}.txt"
  printf '%s' "$DEP_TYPE" > "/tmp/_bc_dep_type_${PR_NUM}.txt"
  printf '%s' "$DEP_RELATION" > "/tmp/_bc_dep_relation_${PR_NUM}.txt"
  printf '%s' "$CVES" > "/tmp/_bc_cves_${PR_NUM}.txt"
  printf '%s' "$CVE_DETAILS" > "/tmp/_bc_cve_details_${PR_NUM}.json"
  printf '%s' "$BUMP" > "/tmp/_bc_bump_${PR_NUM}.txt"
  printf '%s' "$ECOSYSTEM" > "/tmp/_bc_ecosystem_${PR_NUM}.txt"
  printf '%s' "$PKG_DIR" > "/tmp/_bc_pkg_dir_${PR_NUM}.txt"

  # Determine main exit for this ecosystem (use per-module for multi-module Go — A2-3)
  MAIN_EXIT_FOR_ECO=-1
  case "$ECOSYSTEM" in
    npm)   MAIN_EXIT_FOR_ECO=$main_npm_exit ;;
    gomod)
      if [[ "$_GO_MULTI_MODULE" == "true" && "$PKG_DIR" != "/" ]]; then
        _pkg_mod_key=$(echo "$PKG_DIR" | tr '/' '_')
        _pkg_mod_exit_file="/tmp/_bc_main_go_mod_exit_${_pkg_mod_key}.txt"
        if [[ -f "$_pkg_mod_exit_file" ]]; then
          MAIN_EXIT_FOR_ECO=$(cat "$_pkg_mod_exit_file" 2>/dev/null || echo "$main_go_exit")
        else
          MAIN_EXIT_FOR_ECO=$main_go_exit
        fi
      else
        MAIN_EXIT_FOR_ECO=$main_go_exit
      fi
      ;;
    pip)   MAIN_EXIT_FOR_ECO=$main_pip_exit ;;
    maven)  MAIN_EXIT_FOR_ECO=-1 ;;
    docker) MAIN_EXIT_FOR_ECO=-1 ;;

  esac

  # Load extra infra patterns from config (if any) for this heredoc
  EXTRA_INFRA_PATTERNS=""
  while IFS= read -r pattern; do
    [[ -n "$pattern" ]] && EXTRA_INFRA_PATTERNS="${EXTRA_INFRA_PATTERNS}${pattern}
"
  done < <(load_extra_infra_patterns 2>/dev/null)
  printf '%s' "$EXTRA_INFRA_PATTERNS" > "/tmp/_bc_extra_infra_${PR_NUM}.txt"

  RESULTS_FILE="$RESULTS_FILE" PR_NUM="$PR_NUM" TEST_RAN="$TEST_RAN" \
  TEST_EXIT="$TEST_EXIT" BUILD_VERDICT="$BUILD_VERDICT" \
  ERROR_CLASS="${ERROR_CLASS:-}" MAIN_EXIT_ECO="$MAIN_EXIT_FOR_ECO" \
  BUILD_EXIT_CODE="$BUILD_EXIT" INSTALL_METHOD="${INSTALL_METHOD:-ci}" \
  MAIN_GO_TEST_EXIT_PR="$MAIN_GO_TEST_EXIT_PR" \
  MAIN_NPM_TEST_EXIT_PR="$MAIN_NPM_TEST_EXIT_PR" \
  SMOKE_RAN="$SMOKE_RAN" SMOKE_EXIT="$SMOKE_EXIT" \
  DIFF_LINES="$DIFF_LINES" DIFF_TRUNCATED="$DIFF_TRUNCATED" \
  PKG_DIR="$PKG_DIR" INSTALL_OK="$INSTALL_OK" \
  MERGEABLE_STATUS="$MERGEABLE_STATUS" \
  AUDIT_CRITICAL="$AUDIT_CRITICAL" AUDIT_HIGH="$AUDIT_HIGH" \
  BC_SCRATCH_DIR="$BC_SCRATCH_DIR" PR_TSC_EXIT="$PR_TSC_EXIT" \
  PR_INSTALL_EXIT="$PR_INSTALL_EXIT" \
  python3 "$BRK_SCRIPTS/core/pr_data_assembler.py"

  cd "$REPO_ROOT"
done

post_loop_aggregation
