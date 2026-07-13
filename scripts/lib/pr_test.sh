#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# pr_test.sh — Per-PR test execution, smoke probe, and go.sum diff
#
# Provides run_pr_test() which conditionally runs ecosystem-specific tests
# (npm test, go test, pytest), smoke probes for npm, and computes go.sum
# transitive dependency diff.
#
# Globals read:  ECOSYSTEM, PR_NUM, BUILD_VERDICT, PKG, PKG_DIR, DEP_TYPE,
#                BUMP, INSTALL_OK, WORKTREE_BASE, PR_BRANCH, TIMEOUT,
#                MAIN_DIR, FILES_IMPORTING, GO_TIMEOUT, REPO_ROOT,
#                BC_SCRATCH_DIR, BRK_SCRIPTS
# Globals set:   TEST_RAN, TEST_EXIT, TEST_OUTPUT, SMOKE_RAN, SMOKE_EXIT,
#                EVIDENCE_TEST_COMMAND, EVIDENCE_SMOKE_COMMAND,
#                EVIDENCE_SMOKE_OUTPUT, EVIDENCE_SMOKE_EXIT,
#                MAIN_GO_TEST_EXIT_PR, MAIN_NPM_TEST_EXIT_PR,
#                MAIN_GO_TEST_OUTPUT,
#                GOSUM_NEW_COUNT, GOSUM_NEW_NAMES,
#                GOSUM_TOTAL_PR, GOSUM_TOTAL_MAIN
# ──────────────────────────────────────────────────────────────────────────────

run_pr_test() {
# ── Conditional test run ────────────────────────────────────────
TEST_RAN="false"
TEST_EXIT="null"
TEST_OUTPUT=""
SMOKE_RAN="false"
SMOKE_EXIT="null"

# Run tests for ALL production deps where build passes (not just major bumps).
# Tests catch behavioral changes that tsc misses: changed defaults, new throws,
# altered return shapes, middleware contract changes.
# For dev deps: run only on major bumps or known test runners.
RUN_TESTS="false"
# security_review PRs have passing builds + audit concerns — they deserve
# MORE scrutiny, not less. Run tests so they can reach L4 (Finding-2.4).
# CR2-9: Also run tests for Go pre_existing builds when INSTALL_OK=true.
# pre_existing means the failures are the same on both branches — the upgrade didn't break
# anything. Running tests lets these PRs reach L4 instead of capping at L2.
if [[ "$BUILD_VERDICT" == "pass" || "$BUILD_VERDICT" == "security_review" ]] || \
   [[ "$BUILD_VERDICT" == "pre_existing" && "$ECOSYSTEM" == "gomod" && "$INSTALL_OK" == "true" ]] || \
   [[ "$BUILD_VERDICT" == "pre_existing" && "$ECOSYSTEM" == "npm" && "$INSTALL_OK" == "true" ]]; then
  if [[ "$DEP_TYPE" == "production" ]]; then
    RUN_TESTS="true"
  elif [[ "$BUMP" == "major" && "$DEP_TYPE" == "dev" ]]; then
    RUN_TESTS="true"
  elif [[ "$PKG" == "vitest" || "$PKG" == "jest" || "$PKG" == "mocha" ]]; then
    RUN_TESTS="true"
  fi
fi
# Opt-in fast survey mode: skip per-PR test execution. Default OFF so CI behavior is
# unchanged. Skipping tests only REMOVES build-verification evidence, which can never
# make a verdict less conservative (never introduces a false-green) -- it is used for
# fast local disposition sweeps across many PRs, not for ground-truth accuracy runs.
if [[ "${BREAKABILITY_SKIP_TESTS:-0}" == "1" ]]; then
  RUN_TESTS="false"
fi
if [[ "$RUN_TESTS" == "true" ]]; then
  PR_WORKTREE="${WORKTREE_BASE}-${PR_NUM}-test"
  rm -rf "$PR_WORKTREE" 2>/dev/null || true
  git worktree add "$PR_WORKTREE" "origin/$PR_BRANCH" --quiet 2>/dev/null || true

  if [[ -d "$PR_WORKTREE" ]]; then
    case "$ECOSYSTEM" in
      npm)
        TEST_BUILD_DIR="$PR_WORKTREE"
        [[ "$PKG_DIR" != "/" && -d "$PR_WORKTREE/$PKG_DIR" ]] && TEST_BUILD_DIR="$PR_WORKTREE/$PKG_DIR"
        # Run baseline npm tests on main for pre-existing comparison (Finding-4.5).
        # Without this, npm test failures are always attributed to the upgrade
        # even when tests are already broken on main.
        MAIN_NPM_TEST_EXIT_PR=-1
        if [[ -d "$MAIN_DIR" ]]; then
          _main_test_dir="$MAIN_DIR"
          [[ "$PKG_DIR" != "/" && -d "$MAIN_DIR/$PKG_DIR" ]] && _main_test_dir="$MAIN_DIR/$PKG_DIR"
          if [[ -d "$_main_test_dir/node_modules" ]]; then
            echo "  npm test baseline: running tests on main..."
            _main_npm_test_rc=0
            _main_npm_test_out=$(cd "$_main_test_dir" && timeout 180 npm test -- --passWithNoTests 2>&1) || _main_npm_test_rc=$?
            MAIN_NPM_TEST_EXIT_PR=$_main_npm_test_rc
            # Save baseline npm test output for content-level comparison (Finding-5.5)
            echo "$_main_npm_test_out" | tail -n 30 > "/tmp/_bc_main_npm_test_out_${PR_NUM}.txt"
            echo "  npm test baseline: exit=$MAIN_NPM_TEST_EXIT_PR"
          fi
        fi
        # Run npm ci in a subshell to avoid cd leak into main shell.
        # Track install success separately — if install fails, skip tests
        # rather than recording a spurious test failure (Finding-2.1).
        TEST_INSTALL_OK=false
        if (cd "$TEST_BUILD_DIR" && retry_cmd 3 5 timeout $TIMEOUT npm ci --ignore-scripts) 2>/dev/null; then
          TEST_INSTALL_OK=true
        else
          # Workspace monorepo: npm ci fails on private @scope/* deps. Mirror the
          # build stage's fallback (rewrite to file: links + npm install) so tests
          # actually run instead of being skipped (which left test ran=false).
          echo "  test: npm ci failed — trying workspace-local fallback..."
          rewrite_private_deps_to_local "$TEST_BUILD_DIR" "$PR_WORKTREE"
          if (cd "$TEST_BUILD_DIR" && timeout $TIMEOUT npm install --ignore-scripts --legacy-peer-deps) >/dev/null 2>&1; then
            TEST_INSTALL_OK=true
            echo "  test: workspace-local fallback: SUCCESS"
          fi
        fi
        # Build workspace-internal libs so tsc/jest resolve @scope/* against real dist/.
        [[ "$TEST_INSTALL_OK" == "true" ]] && build_npm_workspace_libs "$PR_WORKTREE" "$TIMEOUT"
        if [[ "$TEST_INSTALL_OK" == "true" ]]; then
          # Use --testPathPattern for scoped test execution in monorepos
          if [[ "$PKG_DIR" != "/" && -f "$TEST_BUILD_DIR/package.json" ]]; then
            # Try scoped tests first (faster), fall back to full test
            echo "  test: npm test in ${TEST_BUILD_DIR#$PR_WORKTREE/}..."
            EVIDENCE_TEST_COMMAND="npm test -- --passWithNoTests"
            TEST_OUTPUT=$(cd "$TEST_BUILD_DIR" && timeout 180 npm test -- --passWithNoTests 2>&1)
            TEST_EXIT=$?
          else
            EVIDENCE_TEST_COMMAND="npm test"
            TEST_OUTPUT=$(cd "$TEST_BUILD_DIR" && timeout 180 npm test 2>&1)
            TEST_EXIT=$?
          fi
          TEST_RAN="true"
        else
          echo "  test: SKIP — npm ci failed in test worktree"
        fi
        # ── Smoke probe: catch DI container / runtime failures ──
        # After tests, compile and try to require the built output. Catches:
        # - NestJS DI container failures (missing providers)
        # - Circular dependency issues
        # - Runtime-only import failures
        # We need to build first because dist/ is .gitignored in most projects.
        # Only run if test install succeeded (need node_modules for build).
        if [[ "$TEST_INSTALL_OK" == "true" ]]; then
          if grep -q '"build"' "$TEST_BUILD_DIR/package.json" 2>/dev/null; then
            echo "  smoke: building (npm run build)..."
            BUILD_SMOKE_OUT=$(cd "$TEST_BUILD_DIR" && timeout 60 npm run build 2>&1)
            BUILD_SMOKE_RC=$?
            if [[ "$BUILD_SMOKE_RC" -ne 0 ]]; then
              echo "  smoke: build failed (rc=$BUILD_SMOKE_RC), skipping probe"
            fi
          fi
          if [[ -f "$TEST_BUILD_DIR/dist/main.js" ]]; then
            echo "  smoke: node require('./dist/main') ..."
            EVIDENCE_SMOKE_COMMAND="node -e \"try { require('./dist/main'); process.exit(0); } catch(e) { console.error(e.message); process.exit(1); }\""
            SMOKE_OUT=$(cd "$TEST_BUILD_DIR" && timeout -k 5 10 node -e "
              try { require('./dist/main'); process.exit(0); }
              catch(e) { console.error(e.message); process.exit(1); }
            " 2>&1)
            SMOKE_EXIT=$?
            EVIDENCE_SMOKE_OUTPUT="$SMOKE_OUT"
            EVIDENCE_SMOKE_EXIT="$SMOKE_EXIT"
            SMOKE_RAN="true"
            echo "  smoke: exit=$SMOKE_EXIT"
          elif [[ -f "$TEST_BUILD_DIR/dist/index.js" ]]; then
            echo "  smoke: node require('./dist/index') ..."
            EVIDENCE_SMOKE_COMMAND="node -e \"try { require('./dist/index'); process.exit(0); } catch(e) { console.error(e.message); process.exit(1); }\""
            SMOKE_OUT=$(cd "$TEST_BUILD_DIR" && timeout -k 5 10 node -e "
              try { require('./dist/index'); process.exit(0); }
              catch(e) { console.error(e.message); process.exit(1); }
            " 2>&1)
            SMOKE_EXIT=$?
            EVIDENCE_SMOKE_OUTPUT="$SMOKE_OUT"
            EVIDENCE_SMOKE_EXIT="$SMOKE_EXIT"
            SMOKE_RAN="true"
            echo "  smoke: exit=$SMOKE_EXIT"
          fi
        fi
        ;;
      gomod)
        # Targeted test: only test packages that import the changed dependency
        # First, run the SAME targeted tests on main for pre-existing comparison (Finding-3.1).
        # Without this, main_go_test_exit stays at -1 and all Go test failures
        # are wrongly attributed to the upgrade.
        # Capture baseline test OUTPUT (not just exit code) for content-level
        # comparison — exit-code-only misses mixed failures (Finding-4.3/4.6).
        MAIN_GO_TEST_EXIT_PR=-1
        MAIN_GO_TEST_OUTPUT=""
        if [[ -d "$MAIN_DIR" ]]; then
          echo "  go test baseline: running same targeted tests on main..."
          _main_test_rc=0
          MAIN_GO_TEST_OUTPUT=$(go_targeted_test "$MAIN_DIR" "$FILES_IMPORTING" 2>&1) || _main_test_rc=$?
          MAIN_GO_TEST_EXIT_PR=$_main_test_rc
          echo "  go test baseline: exit=$MAIN_GO_TEST_EXIT_PR"
        fi
        # CR5-3: Run go mod tidy in the test worktree before tests.
        # The test worktree is a fresh checkout from origin/$PR_BRANCH and
        # doesn't have the benefit of the tidy/build cleanup from the first
        # worktree. Without tidy, go test may fail with checksum errors.
        echo "  go test: preparing test worktree (go mod tidy)..."
        (cd "$PR_WORKTREE" && go mod tidy 2>/dev/null) || true
        echo "  go test: targeted (only affected packages)"
        TEST_OUTPUT=""
        EVIDENCE_TEST_COMMAND="go_targeted_test (timeout ${GO_TIMEOUT} go test -timeout 5m -race <affected packages>)"
        TEST_OUTPUT=$(go_targeted_test "$PR_WORKTREE" "$FILES_IMPORTING" 2>&1)
        TEST_EXIT=$?
        TEST_RAN="true"
        # Save baseline test output for content comparison in verification block
        echo "$MAIN_GO_TEST_OUTPUT" | tail -n 30 > "/tmp/_bc_main_go_test_out_${PR_NUM}.txt"
        ;;
      pip)
        _PY_VENV_TEST=$(mktemp -d /tmp/bc_venv_test_XXXXXX)
        if python3 -m venv "$_PY_VENV_TEST" 2>/dev/null; then
          _PY_PIP_TEST="$_PY_VENV_TEST/bin/pip"
          _PY_PYTHON_TEST="$_PY_VENV_TEST/bin/python"
        else
          rm -rf "$_PY_VENV_TEST" 2>/dev/null || true
          _PY_VENV_TEST=""
          command -v pip3 &>/dev/null && _PY_PIP_TEST="pip3" || _PY_PIP_TEST="pip"
          _PY_PYTHON_TEST="python3"
        fi
        # Run install in subshell to avoid cd leak; track success separately (Finding-2.1)
        TEST_INSTALL_OK=false
        if [[ -f "$PR_WORKTREE/requirements.txt" ]]; then
          if (cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_TEST" install -r requirements.txt --quiet) 2>/dev/null; then
            TEST_INSTALL_OK=true
          fi
        elif [[ -f "$PR_WORKTREE/pyproject.toml" ]]; then
          if (cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_TEST" install -e . --quiet) 2>/dev/null; then
            TEST_INSTALL_OK=true
          fi
        elif [[ -f "$PR_WORKTREE/poetry.lock" ]]; then
          # Chain poetry install commands so second only runs if first succeeds (Finding-2.8)
          if (cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_TEST" install poetry --quiet 2>&1 && \
              retry_cmd 3 5 "$_PY_PYTHON_TEST" -m poetry install --quiet) 2>/dev/null; then
            TEST_INSTALL_OK=true
          fi
        fi
        if [[ "$TEST_INSTALL_OK" == "true" ]]; then
          EVIDENCE_TEST_COMMAND="python -m pytest"
          TEST_OUTPUT=$(cd "$PR_WORKTREE" && timeout 180 "$_PY_PYTHON_TEST" -m pytest 2>&1)
          TEST_EXIT=$?
          TEST_RAN="true"
        else
          echo "  test: SKIP — pip/poetry install failed in test worktree"
        fi
        [[ -n "$_PY_VENV_TEST" ]] && rm -rf "$_PY_VENV_TEST" 2>/dev/null || true
        ;;
    esac
    echo "  test: exit=$TEST_EXIT"

    # ── go.sum diff: count net-new transitive entries added by this PR ──
    # Compare go.sum in PR worktree vs main. New lines = new transitive deps.
    GOSUM_NEW_COUNT=0
    GOSUM_NEW_NAMES=""
    GOSUM_TOTAL_PR=0
    GOSUM_TOTAL_MAIN=0
    if [[ "$ECOSYSTEM" == "gomod" && -d "$PR_WORKTREE" ]]; then
      _GOSUM_PR="$PR_WORKTREE/go.sum"
      _GOSUM_MAIN="$REPO_ROOT/go.sum"
      # For multi-module repos, use PKG_DIR go.sum if available
      if [[ "$PKG_DIR" != "/" && -f "$PR_WORKTREE/$PKG_DIR/go.sum" ]]; then
        _GOSUM_PR="$PR_WORKTREE/$PKG_DIR/go.sum"
        _GOSUM_MAIN="$REPO_ROOT/$PKG_DIR/go.sum"
      fi
      if [[ -f "$_GOSUM_PR" && -f "$_GOSUM_MAIN" ]]; then
        _GOSUM_NEW_LINES=$(comm -13 <(sort "$_GOSUM_MAIN") <(sort "$_GOSUM_PR") 2>/dev/null || true)
        GOSUM_NEW_COUNT=$(echo "$_GOSUM_NEW_LINES" | awk '{print $1}' | sort -u | grep -c . || echo "0")
        # Extract top-5 unique package names (first column of go.sum: module version hash)
        GOSUM_NEW_NAMES=$(echo "$_GOSUM_NEW_LINES" | awk '{print $1}' | sort -u | head -5 | tr '\n' ',' | sed 's/,$//' || echo "")
        GOSUM_TOTAL_PR=$(wc -l < "$_GOSUM_PR" | tr -d ' ' || echo "0")
        GOSUM_TOTAL_MAIN=$(wc -l < "$_GOSUM_MAIN" | tr -d ' ' || echo "0")
        # Capture the resulting version of every module this PR RAISED (direct or
        # transitive). go.sum lines are "module version[/go.mod] hash"; the diff
        # against main yields only modules this PR introduced/bumped. The CVE
        # matcher uses this to credit a fix delivered via a TRANSITIVE go.mod bump
        # (e.g. an otel/sdk PR that also raises go.opentelemetry.io/otel), not just
        # the PR's primary package.
        _BC_GOSUM_NEW_LINES="$_GOSUM_NEW_LINES" python3 "$BRK_SCRIPTS/core/pr_utils.py" gosum_bumps > "/tmp/_bc_bumped_mods_${PR_NUM}.json" 2>/dev/null || echo '{}' > "/tmp/_bc_bumped_mods_${PR_NUM}.json"
      fi
      echo "  go.sum: $GOSUM_NEW_COUNT new transitive entries ($GOSUM_NEW_NAMES)"
    fi

    git worktree remove "$PR_WORKTREE" --force 2>/dev/null || { chmod -R u+w "$PR_WORKTREE" 2>/dev/null; rm -rf "$PR_WORKTREE" 2>/dev/null; } || true
  fi
fi
}
