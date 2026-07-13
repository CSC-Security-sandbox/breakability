#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# baseline_builds.sh — Baseline build orchestration for main branch
#
# Provides run_baseline_builds() which fetches branches, creates a main
# worktree, runs npm/go/pip baseline builds, baseline vuln scan, and writes
# main_build results to JSON.
#
# Globals read:  WORKTREE_BASE, REPO_ROOT, MAIN_DIR, GO_AVAILABLE,
#                RESULTS_FILE, GO_TIMEOUT, BRK_SCRIPTS
# Globals set:   main_npm_exit, main_npm_install_exit, main_npm_tsc_exit,
#                main_npm_output, main_go_exit, main_go_output,
#                _GO_MULTI_MODULE, main_go_test_exit, main_go_test_output,
#                main_pip_exit, main_pip_output,
#                MAIN_DIR, MAIN_VULN_FINDINGS_FILE, MAIN_VULN_STATUS_FILE
# ──────────────────────────────────────────────────────────────────────────────

run_baseline_builds() {
# ── Fetch all branches ───────────────────────────────────────────────────────
echo ""
echo "Fetching remote branches..."
git fetch --all --prune --quiet 2>/dev/null || true

# ── Baseline builds on main ──────────────────────────────────────────────────
echo ""
echo "════════════ BASELINE BUILDS (main) ════════════"

MAIN_DIR="${WORKTREE_BASE}-main"
rm -rf "$MAIN_DIR" 2>/dev/null || true
git worktree remove "$MAIN_DIR" --force 2>/dev/null || true
git worktree prune 2>/dev/null || true
git worktree add "$MAIN_DIR" origin/main --quiet 2>/dev/null || \
  git worktree add "$MAIN_DIR" main --quiet 2>/dev/null || \
  cp -r "$REPO_ROOT" "$MAIN_DIR"

main_npm_exit="-1"
main_npm_install_exit="-1"
main_npm_tsc_exit="-1"
main_npm_output=""
main_go_exit="-1"
main_go_output=""
_GO_MULTI_MODULE="false"
main_go_test_exit="-1"
main_go_test_output=""
main_pip_exit="-1"
main_pip_output=""


# For single-repo (root package.json), still build baseline upfront
if [[ -f "$MAIN_DIR/package.json" ]]; then
  echo "  npm: root package.json detected, building baseline..."
  build_npm_baseline_for_dir "."
  main_npm_exit=$(cat "/tmp/_bc_main_npm_install_..txt" 2>/dev/null || echo "-1")
  main_npm_output=$(cat "/tmp/_bc_main_npm_out_..txt" 2>/dev/null || echo "")
else
  echo "  npm: monorepo detected (no root package.json), baselines will be built on demand"
  main_npm_exit=-1
  main_npm_output=""
fi

# Go baseline — detect go.work (multi-module workspace), multi-module (multiple go.mod), or single module
# ── GOSUMDB/GONOSUMCHECK environment sanitization (A3-3/CR3-1/CR4-2/A4-2) ──
# The target repo or runner image may have GOSUMDB=off set via:
#   1. Shell environment variable ($GOSUMDB) — cleared by unset
#   2. Go persistent env file ($GOENV / ~/.config/go/env) — cleared by go env -u
#   3. Runner image defaults — overridden by go env -w
# All three sources must be addressed. The V7 E2E failure was caused by
# GOSUMDB=off persisting in the Go env file after shell unset.
unset GOSUMDB 2>/dev/null || true
unset GONOSUMCHECK 2>/dev/null || true
# Clear Go's persistent env file — this is the CRITICAL fix for V7 failure.
# go env -u removes the key entirely, falling back to default (sum.golang.org).
go env -u GOSUMDB 2>/dev/null || true
go env -u GONOSUMCHECK 2>/dev/null || true
go env -u GONOSUMDB 2>/dev/null || true
# Ensure GONOSUMDB matches GOPRIVATE — only private modules skip the sum DB
if [[ -n "${GOPRIVATE:-}" ]]; then
  export GONOSUMDB="${GOPRIVATE}"
  go env -w GONOSUMDB="${GOPRIVATE}" 2>/dev/null || true
fi
if [[ -f "$MAIN_DIR/go.work" ]]; then
  echo "  go: workspace (go.work) detected, syncing..."
  main_go_output=$(cd "$MAIN_DIR" && {
    # Bug fix: && ensures go build is skipped if go work sync fails (Bug 5).
    # _BUILD_RC captures go build exit so go vet warnings don't clobber it (Bug 3).
    _BUILD_RC=0
    go_free_disk
    retry_cmd 3 5 go work sync && {
      GOMEMLIMIT=1500MiB timeout -k 15 $GO_TIMEOUT go build -p 2 -o /dev/null ./... || _BUILD_RC=$?
      if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
      exit $_BUILD_RC
    }
  } 2>&1)
  main_go_exit=$?
  # Cache corruption retry for baseline
  if [[ "$main_go_exit" -ne 0 ]] && [[ "$(classify_go_error "$main_go_output")" == "cache_corruption" ]]; then
    echo "  ⚠ Go build cache corruption on baseline — cleaning and retrying..."
    (cd "$MAIN_DIR" && go clean -cache 2>/dev/null || true)
    main_go_output=$(cd "$MAIN_DIR" && {
      _BUILD_RC=0
      go_free_disk
      retry_cmd 3 5 go work sync && {
        GOMEMLIMIT=1500MiB timeout -k 15 $GO_TIMEOUT go build -p 2 -o /dev/null ./... || _BUILD_RC=$?
        if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
        exit $_BUILD_RC
      }
    } 2>&1)
    main_go_exit=$?
    echo "  go baseline cache-clean retry: exit=$main_go_exit"
  fi
  echo "  go baseline (workspace): exit=$main_go_exit"
elif [[ -n "$(find "$MAIN_DIR" -name go.mod -not -path '*/vendor/*' -not -path '*/.git/*' -print -quit 2>/dev/null)" ]]; then
  # Check for multi-module layout (one or more go.mod without go.work, including repos with no root go.mod)
  _GO_MODULES=$(find "$MAIN_DIR" -name go.mod -not -path '*/vendor/*' -not -path '*/.git/*' 2>/dev/null | sort)
  _MOD_COUNT=$(echo "$_GO_MODULES" | grep -c . || echo 0)

  if [[ "$_MOD_COUNT" -gt 1 ]]; then
    echo "  go: multi-module repo detected ($_MOD_COUNT modules) — building each separately..."
    main_go_output=""
    main_go_exit=0
    _GO_MULTI_MODULE="true"
    # Clean build cache ONCE before the loop, not per-module (CR2-3).
    # Per-module cleanup wipes the previous module's cache, forcing cold rebuilds.
    go_free_disk
    while IFS= read -r _mod_file; do
      _mod_dir=$(dirname "$_mod_file")
      _mod_rel=$(realpath --relative-to="$MAIN_DIR" "$_mod_dir" 2>/dev/null || echo "$_mod_dir")
      echo "  go baseline: building module $_mod_rel ..."
      _mod_output=$(cd "$_mod_dir" && {
        _BUILD_RC=0
        retry_cmd 3 5 go mod tidy && {
          GOMEMLIMIT=1500MiB timeout -k 15 $GO_TIMEOUT go build -p 2 -o /dev/null ./... || _BUILD_RC=$?
          if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
          exit $_BUILD_RC
        }
      } 2>&1)
      _mod_exit=$?
      # Cache corruption retry for this specific module
      if [[ "$_mod_exit" -ne 0 ]] && [[ "$(classify_go_error "$_mod_output" "$_mod_exit")" == "cache_corruption" ]]; then
        echo "    ⚠ Go build cache corruption on baseline module $_mod_rel — cleaning and retrying..."
        (cd "$_mod_dir" && go clean -cache 2>/dev/null || true)
        _mod_output=$(cd "$_mod_dir" && {
          _BUILD_RC=0
          retry_cmd 3 5 go mod tidy && {
            GOMEMLIMIT=1500MiB timeout -k 15 $GO_TIMEOUT go build -p 2 -o /dev/null ./... || _BUILD_RC=$?
            if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
            exit $_BUILD_RC
          }
        } 2>&1)
        _mod_exit=$?
        echo "    module $_mod_rel cache-clean retry: exit=$_mod_exit"
      fi
      echo "    module $_mod_rel: exit=$_mod_exit"
      # Save per-module baseline exit code and output for PR-level comparison (A2-3/CR2-2).
      # The PR loop will look up the baseline for the specific module the PR touches,
      # instead of comparing against the worst exit code across ALL modules.
      _mod_key=$(echo "$_mod_rel" | tr '/' '_')
      echo "$_mod_exit" > "/tmp/_bc_main_go_mod_exit_${_mod_key}.txt"
      echo "$_mod_output" > "/tmp/_bc_main_go_mod_output_${_mod_key}.txt"
      main_go_output="$main_go_output
--- module: $_mod_rel (exit=$_mod_exit) ---
$_mod_output"
      # CR5-6: Track worst exit code — keep the first non-zero exit.
      # Do NOT let timeout (124) overwrite a real compile error (1), because the
      # compile error has useful baseline data while timeout has none.
      # Per-module baselines are saved to temp files above for PR-level lookup.
      if [[ "$_mod_exit" -ne 0 && "$main_go_exit" -eq 0 ]]; then
        main_go_exit=$_mod_exit
      fi
    done <<< "$_GO_MODULES"
    echo "  go baseline (multi-module): worst_exit=$main_go_exit"
  else
    echo "  go: building single module..."
    # Supply chain integrity is ensured by go.sum + the default GOSUMDB (sum.golang.org).
    # Do NOT set GOSUMDB=off — that disables the checksum database, breaking go mod verify/tidy/build
    # and actually REDUCING security (modules can't be verified against the sum DB).
    # Do NOT set GOPROXY=direct — the Go module proxy (proxy.golang.org) provides immutable caching
    # which protects against source repo takeover. Direct fetches are LESS secure.
    main_go_output=$(cd "$MAIN_DIR" && {
      # _BUILD_RC captures go build exit so go vet warnings don't clobber it (Bug 3).
      _BUILD_RC=0
      go_free_disk
      retry_cmd 3 5 timeout -k 15 120 go mod tidy && {
        GOMEMLIMIT=1500MiB timeout -k 15 $GO_TIMEOUT go build -p 2 -o /dev/null ./... || _BUILD_RC=$?
        if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
        exit $_BUILD_RC
      }
    } 2>&1)
    main_go_exit=$?
    # Cache corruption retry for single-module baseline
    if [[ "$main_go_exit" -ne 0 ]] && [[ "$(classify_go_error "$main_go_output")" == "cache_corruption" ]]; then
      echo "  ⚠ Go build cache corruption on baseline — cleaning and retrying..."
      (cd "$MAIN_DIR" && go clean -cache 2>/dev/null || true)
      main_go_output=$(cd "$MAIN_DIR" && {
        _BUILD_RC=0
        go_free_disk
        retry_cmd 3 5 go mod tidy && {
          GOMEMLIMIT=1500MiB timeout -k 15 $GO_TIMEOUT go build -p 2 -o /dev/null ./... || _BUILD_RC=$?
          if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
          exit $_BUILD_RC
        }
      } 2>&1)
      main_go_exit=$?
      echo "  go baseline cache-clean retry: exit=$main_go_exit"
    fi
    echo "  go baseline: exit=$main_go_exit"
  fi
fi

# ── Go baseline vulnerability scan (main) ────────────────────────────────────
# Scans main branch ONCE per batch to establish baseline CVE set.
# Each PR's findings are then diff'd against this so we only flag NEW vulns.
# Without this, every PR appears to "introduce" repo-wide pre-existing CVEs.
MAIN_VULN_FINDINGS_FILE="/tmp/_bc_main_vuln_findings.txt"
MAIN_VULN_STATUS_FILE="/tmp/_bc_main_vuln_status.txt"
: > "$MAIN_VULN_FINDINGS_FILE"
echo "unknown" > "$MAIN_VULN_STATUS_FILE"
if command -v govulncheck &>/dev/null && [[ -d "$MAIN_DIR" ]]; then
  echo ""
  echo "  [security] scanning main baseline for existing vulnerabilities..."
  MAIN_VULN_OUT=$(go_check_vulnerabilities "$MAIN_DIR" 2>&1) || true
  MAIN_VULN_STATUS_VAL=$(echo "$MAIN_VULN_OUT" | grep -oE '^###VULN_STATUS=[a-z_]+' | tail -1 | cut -d= -f2)
  [[ -z "$MAIN_VULN_STATUS_VAL" ]] && MAIN_VULN_STATUS_VAL="unknown"
  echo "$MAIN_VULN_STATUS_VAL" > "$MAIN_VULN_STATUS_FILE"
  # Extract all unique GO-YYYY-NNNN IDs from main baseline (one per line)
  echo "$MAIN_VULN_OUT" | grep -oE 'GO-[0-9]{4}-[0-9]+' | sort -u > "$MAIN_VULN_FINDINGS_FILE"
  MAIN_VULN_COUNT=$(wc -l < "$MAIN_VULN_FINDINGS_FILE" | tr -d ' ')
  echo "  [security] main baseline: status=$MAIN_VULN_STATUS_VAL, pre-existing vulns=$MAIN_VULN_COUNT"
else
  echo "  [security] skipping main baseline vuln scan (govulncheck not available or MAIN_DIR missing)"
fi

# Go baseline test — deferred to per-PR targeted comparison.
# We don't run full ./... here (takes 30+ min on large monorepos).
# Instead, per-PR in the PR loop (gomod test block), we run the SAME targeted
# tests on both the main worktree and the PR worktree, storing the result in
# MAIN_GO_TEST_EXIT_PR. This enables pre-existing test failure detection.
# The global value below is kept for metadata only (Finding-3.1).
main_go_test_exit=-1
main_go_test_output="deferred — per-PR targeted comparison"

# Python baseline — detect requirements.txt / pyproject.toml / poetry.lock
_PY_SRC_FILE=""
[[ -f "$MAIN_DIR/requirements.txt" ]] && _PY_SRC_FILE="requirements.txt"
[[ -z "$_PY_SRC_FILE" && -f "$MAIN_DIR/pyproject.toml" ]] && _PY_SRC_FILE="pyproject.toml"
[[ -z "$_PY_SRC_FILE" && -f "$MAIN_DIR/poetry.lock" ]] && _PY_SRC_FILE="poetry.lock"
if [[ -n "$_PY_SRC_FILE" ]]; then
  echo "  pip: installing in isolated venv ($_PY_SRC_FILE)..."
  _PY_VENV_MAIN=$(mktemp -d /tmp/bc_venv_main_XXXXXX)
  if python3 -m venv "$_PY_VENV_MAIN" 2>/dev/null; then
    _PY_PIP_MAIN="$_PY_VENV_MAIN/bin/pip"
    _PY_PYTHON_MAIN="$_PY_VENV_MAIN/bin/python"
  else
    rm -rf "$_PY_VENV_MAIN" 2>/dev/null || true
    _PY_VENV_MAIN=""
    command -v pip3 &>/dev/null && _PY_PIP_MAIN="pip3" || _PY_PIP_MAIN="pip"
    _PY_PYTHON_MAIN="python3"
  fi
  case "$_PY_SRC_FILE" in
    requirements.txt)
      main_pip_output=$(cd "$MAIN_DIR" && retry_cmd 3 5 "$_PY_PIP_MAIN" install -r requirements.txt --quiet 2>&1) ;;
    pyproject.toml)
      main_pip_output=$(cd "$MAIN_DIR" && retry_cmd 3 5 "$_PY_PIP_MAIN" install -e . --quiet 2>&1) ;;
    poetry.lock)
      main_pip_output=$(cd "$MAIN_DIR" && {
        retry_cmd 3 5 "$_PY_PIP_MAIN" install poetry --quiet 2>&1 && \
        retry_cmd 3 5 "$_PY_PYTHON_MAIN" -m poetry install --quiet 2>&1
      }) ;;
  esac
  main_pip_exit=$?
  [[ -n "$_PY_VENV_MAIN" ]] && rm -rf "$_PY_VENV_MAIN" 2>/dev/null || true
  echo "  pip baseline: exit=$main_pip_exit"
fi

# Write main_build to results
echo "$main_npm_output" | tail -n 50 > /tmp/_bc_main_npm.txt
echo "$main_go_output" | tail -n 50 > /tmp/_bc_main_go.txt
echo "$main_go_test_output" | tail -n 30 > /tmp/_bc_main_go_test.txt
echo "$main_pip_output" | tail -n 50 > /tmp/_bc_main_pip.txt

python3 << PYEOF
import json

with open("$RESULTS_FILE") as f:
    data = json.load(f)

def read_output(path):
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""

data["main_build"] = {
    "npm": {"exit": $main_npm_exit, "output_tail": read_output("/tmp/_bc_main_npm.txt")},
    "go": {"exit": $main_go_exit, "test_exit": $main_go_test_exit, "output_tail": read_output("/tmp/_bc_main_go.txt"), "test_output_tail": read_output("/tmp/_bc_main_go_test.txt")},
    "pip": {"exit": $main_pip_exit, "output_tail": read_output("/tmp/_bc_main_pip.txt")}
}

import os
import tempfile

def atomic_json_write(data, filepath):
    tmpfd, tmppath = tempfile.mkstemp(dir=os.path.dirname(filepath) or '.', suffix='.tmp')
    try:
        with os.fdopen(tmpfd, 'w') as f:
            json.dump(data, f, indent=2)
        os.rename(tmppath, filepath)
    except Exception:
        if os.path.exists(tmppath):
            os.remove(tmppath)
        raise

atomic_json_write(data, "$RESULTS_FILE")
PYEOF

# NOTE: main worktree kept alive for lazy per-directory baselines during PR processing
}
