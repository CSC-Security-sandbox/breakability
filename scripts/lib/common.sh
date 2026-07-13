#!/usr/bin/env bash
# Common utility functions: cleanup, JSON helpers, retry logic, timeout polyfill.
# Sourced by build-check.sh — do not run directly.

_bc_cleanup() {
  if [[ -n "${BC_SCRATCH_DIR:-}" && -n "${REPO_ROOT:-}" && "$BC_SCRATCH_DIR" == "${REPO_ROOT}"/.breakability-scratch* ]]; then
    rm -rf "$BC_SCRATCH_DIR" 2>/dev/null || true
  fi
  rm -rf "${WORKTREE_BASE:-/tmp/worktree}"-*/ 2>/dev/null || true
  git worktree list --porcelain 2>/dev/null | grep '^/' | while IFS= read -r wt; do
    git worktree remove "$wt" --force 2>/dev/null || true
  done
}
trap '_bc_cleanup; exit 130' TERM INT
trap _bc_cleanup EXIT

# ── Polyfill timeout for macOS ────────────────────────────────────────────────
if ! command -v timeout &>/dev/null; then
  if command -v gtimeout &>/dev/null; then
    timeout() { gtimeout "$@"; }
  else
    # Simple fallback: run the command WITHOUT enforcing a timeout. Callers use
    # GNU-style invocations like `timeout -k 15 180 cmd ...`, so we must strip any
    # leading options (and their arguments) AND the DURATION before exec'ing the
    # command — otherwise the option value (e.g. `15`) gets run as a command
    # ("15: command not found") and every wrapped go build/test/vet/govulncheck
    # fails, truncating per-PR analysis.
    timeout() {
      while [[ "$1" == -* ]]; do
        case "$1" in
          -k|--kill-after|-s|--signal) shift 2 ;;  # option takes an argument
          *) shift ;;                              # flag or attached-arg form
        esac
      done
      shift   # drop the DURATION positional
      "$@"
    }
  fi
fi

# ── Helpers ───────────────────────────────────────────────────────────────────
json_escape() {
  python3 -c "import json,sys; print(json.dumps(sys.stdin.read()))" 2>/dev/null || echo '""'
}

tail_output() {
  # Last N lines of output, JSON-safe
  local lines="${1:-50}"
  tail -n "$lines" | json_escape
}

# Retry a command with exponential backoff
# Usage: retry_cmd <max_attempts> <base_delay_seconds> <command...>
# Special handling: if command contains 'timeout', treat 124 (timeout) as retryable
# with increasing timeout per attempt (instead of same timeout × retries)
retry_cmd() {
  local max_attempts="$1"
  local base_delay="$2"
  shift 2
  local attempt=1
  local rc=0
  local has_timeout=0
  local timeout_val="" _dur_idx=-1
  local _args=("$@")
  local _i _ti=-1

  for _i in "${!_args[@]}"; do
    if [[ "${_args[$_i]}" == "timeout" ]]; then
      has_timeout=1
      _ti=$_i
      break
    fi
  done

  # Locate the DURATION positional: the first bare (non-option) token after the
  # literal `timeout`. GNU options -k/--kill-after and -s/--signal consume a
  # value, so they must be skipped — otherwise the kill-after value (e.g. the
  # `15` in `timeout -k 15 120 …`) is mistaken for the duration, the command is
  # rebuilt as `timeout 15 15 120 …`, and the runner tries to exec `15`
  # ("15: command not found", exit 127), silently breaking go mod tidy/build.
  if [[ $has_timeout -eq 1 ]]; then
    local _j=$((_ti + 1))
    while [[ $_j -lt ${#_args[@]} ]]; do
      case "${_args[$_j]}" in
        -k|--kill-after|-s|--signal) _j=$((_j + 2)) ;;
        -*)                          _j=$((_j + 1)) ;;
        *) _dur_idx=$_j; timeout_val="${_args[$_j]}"; break ;;
      esac
    done
  fi

  while [[ $attempt -le $max_attempts ]]; do
    if [[ $has_timeout -eq 1 && -n "$timeout_val" ]]; then
      # A5-5: Cap scaled timeout at 2x original to avoid 720s worst-case.
      # Attempt 1: 1x, Attempt 2: 2x, Attempt 3+: 2x (capped).
      local _scale=$((attempt < 3 ? attempt : 2))
      local scaled_timeout=$((timeout_val * _scale))
      # Rebuild the command, replacing ONLY the duration positional with the
      # scaled value — preserving any -k/-s options and their arguments.
      local cmd=()
      local _k
      for _k in "${!_args[@]}"; do
        if [[ $_k -eq $_dur_idx ]]; then
          cmd+=("$scaled_timeout")
        else
          cmd+=("${_args[$_k]}")
        fi
      done
      # CR5-4: Capture exit code correctly. `if cmd; then` loses the actual
      # exit code — $? after `if` is always 0 or 1. Use direct execution.
      "${cmd[@]}" && return 0
      rc=$?
      if [[ $rc -eq 124 ]]; then
        echo "  ⚠️  Command timed out (attempt $attempt/$max_attempts, timeout=${scaled_timeout}s), retrying..." >&2
      fi
    else
      "$@" && return 0
      rc=$?
    fi
    if [[ $rc -eq 124 ]]; then
      if [[ $has_timeout -eq 0 ]]; then
        return $rc
      fi
    elif [[ $rc -eq 137 ]]; then
      # V9.8 iter6 (E): SIGKILL / OOM — retrying will just OOM again, waste CI. Bail out.
      echo "  ⚠️  Command killed (OOM, exit=137) — not retrying" >&2
      return $rc
    else
      if [[ $attempt -lt $max_attempts ]]; then
        local delay=$((base_delay * (2 ** (attempt - 1))))
        echo "  ⚠️  Command failed (attempt $attempt/$max_attempts, exit=$rc), retrying in ${delay}s..." >&2
        sleep "$delay"
      fi
    fi
    ((attempt++))
  done
  return $rc
}
