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

TIMEOUT=120
DIFF_MAX_LINES=500
BATCH_ID="${BATCH_ID:-}"
if [[ -n "$BATCH_ID" ]]; then
  RESULTS_FILE="/tmp/build-results-${BATCH_ID}.json"
else
  RESULTS_FILE="/tmp/build-results.json"
fi
CLI_PATH="${CLI_PATH:-.github/actions/breakability-check/index.js}"
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

# ── Go changelog fetch (shared source for verdict + display, PRD G6) ──────────
# Resolves the module's GitHub repo (incl. common vanity import paths the in-CLI
# fetcher cannot), pulls release bodies for tags in (from,to] plus CHANGELOG.md, and
# emits the breaking-change-relevant lines as plain text. The output is fed to the CLI
# via --changelog-file so computeMergeRisk sees declared breaking changes, and persisted
# so the renderer shows the SAME source.
fetch_go_changelog_text() {
  local _pkg="$1" _from="$2" _to="$3" _gh_path _releases _changelog _content
  [[ -z "$_pkg" || -z "$_from" || -z "$_to" ]] && return 0
  _gh_path=$(echo "$_pkg" | grep -oE '^github\.com/[^/]+/[^/]+' || echo "")
  [[ -z "$_gh_path" ]] && _gh_path=$(echo "$_pkg" | sed -n 's|^golang.org/x/\([^/]*\)|github.com/golang/\1|p')
  [[ -z "$_gh_path" ]] && _gh_path=$(echo "$_pkg" | sed -n 's|^go.opentelemetry.io/.*|github.com/open-telemetry/opentelemetry-go|p' | head -1)
  [[ -z "$_gh_path" ]] && return 0
  ( unset GH_TOKEN
    _releases=$(gh api "repos/${_gh_path#github.com/}/releases?per_page=100" --jq '[.[] | {tag_name,name,body: ((.body // "")[0:4000])}]' 2>/dev/null || echo '[]')
    _changelog=""
    for _candidate in CHANGELOG.md CHANGES.md HISTORY.md RELEASES.md; do
      _content=$(gh api "repos/${_gh_path#github.com/}/contents/${_candidate}" --jq '.content // ""' 2>/dev/null | python3 -c 'import base64,sys; data=sys.stdin.read().strip(); print(base64.b64decode(data).decode("utf-8","replace") if data else "")' 2>/dev/null | head -c 24000 || true)
      [[ -n "$_content" ]] && { _changelog="$_content"; break; }
    done
    _BC_RELEASES="$_releases" _BC_CHANGELOG="$_changelog" _BC_FROM="$_from" _BC_TO="$_to" python3 -c '
import json, os, re
releases = json.loads(os.environ.get("_BC_RELEASES", "[]") or "[]")
changelog = os.environ.get("_BC_CHANGELOG", "") or ""
from_v = os.environ.get("_BC_FROM", ""); to_v = os.environ.get("_BC_TO", "")
def norm(v):
    v = (v or "").strip().lstrip("v")
    m = re.search(r"(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", v)
    return m.group(1) if m else v
def tup(v):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", norm(v))
    return tuple(map(int, m.groups())) if m else None
lo, hi = tup(from_v), tup(to_v)
def versions_in(s):
    return [tuple(map(int, m)) for m in re.findall(r"(\d+)\.(\d+)\.(\d+)", s or "")]
def in_range(tag):
    tv = tup(tag)
    # Fail closed when range is unparsable: only the exact to-version counts.
    if not lo or not hi:
        return norm(tag) == norm(to_v)
    if not tv:
        return False
    return lo < tv <= hi
pat = re.compile(r"\b(BREAKING|breaking[\s-]?change|backward[\s-]?incompatible|migration[\s-]?required|removed?|incompatible|default(?:s| value)?\s+(?:change|changed|now)|deprecated|renamed|deleted|no longer|behavior change|API change)\b", re.I)
lines = []
for rel in releases:
    tag = rel.get("tag_name") or rel.get("name") or ""
    if not in_range(tag): continue
    text = "\n".join([str(rel.get("name") or tag), str(rel.get("body") or "")])
    for line in text.splitlines():
        line = line.strip(" -*\t")
        if line and pat.search(line):
            lines.append(line[:300])
# CHANGELOG.md: scope to the section between to and from headers to avoid stale matches.
if changelog:
    sect = []; capture = False
    hdr = re.compile(r"^#+\s*\[?v?\d+\.\d+\.\d+")
    for line in changelog.splitlines():
        ls = line.strip()
        if hdr.match(ls):
            # Headers may list several modules, e.g. "## [1.42.0/0.64.0/0.18.0]".
            # Capture if ANY listed version falls in (from, to]. Fail closed if
            # the range is unparsable: only capture the exact to-version section.
            vs = versions_in(ls)
            if lo and hi:
                capture = any(lo < v <= hi for v in vs)
            else:
                capture = any(v == tup(to_v) for v in vs)
        if capture:
            s = line.strip(" -*\t")
            if s and pat.search(s):
                sect.append(s[:300])
        if len(sect) >= 40: break
    lines.extend(sect)
seen = []
for l in lines:
    if l not in seen: seen.append(l)
print("\n".join(f"- {l}" for l in seen[:40]))
' 2>/dev/null || true
  )
}

# ── npm changelog fetch (release bodies + scoped changelog sections) ──────────
# Resolves npm package metadata to a GitHub repository (including scoped
# monorepo directories), pulls release bodies/tags in (from,to] plus
# CHANGELOG/CHANGES/HISTORY/RELEASES sections, and emits nothing on failure so
# release-notes evidence remains UNAVAILABLE rather than falsely clean.
fetch_npm_changelog_text() {
  local _pkg="$1" _from="$2" _to="$3"
  [[ -z "$_pkg" || -z "$_from" || -z "$_to" ]] && return 0
  python3 "$REPO_ROOT/.github/scripts/npm_changelog.py" fetch \
    --package "$_pkg" --from-version "$_from" --to-version "$_to" 2>/dev/null || true
}


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

# Parse the breakability config and cache the parsed JSON in a temp file
# so we don't re-parse YAML for every call. Uses PyYAML if available, falls
# back to a simple regex parser for the subset of YAML we need.
_parse_bc_config() {
  local cache="/tmp/_bc_config_parsed.json"
  if [[ -f "$cache" ]]; then
    cat "$cache"
    return 0
  fi
  [[ -f "$BC_CONFIG" ]] || { echo "{}"; return 0; }

  python3 << 'PARSECFG' > "$cache" 2>/dev/null
import json, os, re

config_path = os.environ.get("BC_CONFIG", "")
if not config_path or not os.path.isfile(config_path):
    print("{}")
    exit()

with open(config_path) as f:
    raw = f.read()

# Try PyYAML first
try:
    import yaml
    config = yaml.safe_load(raw) or {}
    print(json.dumps(config))
    exit()
except ImportError:
    pass

# Fallback: simple parser for our known config structure
config = {}

# Parse private_registries list
registries = []
in_registries = False
current = {}
for line in raw.split("\n"):
    stripped = line.strip()
    if stripped.startswith("#") or not stripped:
        continue
    if stripped.startswith("private_registries:"):
        in_registries = True
        val = stripped.split(":", 1)[1].strip()
        if val == "[]":
            registries = []
            in_registries = False
        continue
    if in_registries:
        if stripped.startswith("- "):
            if current:
                registries.append(current)
            current = {}
            # Parse "- key: value"
            kv = stripped[2:].strip()
            m = re.match(r'(\w+):\s*["\']?([^"\']+?)["\']?\s*$', kv)
            if m:
                current[m.group(1)] = m.group(2)
        elif stripped.startswith(("scope:", "registry:", "auth_token_env:")):
            m = re.match(r'(\w+):\s*["\']?([^"\']+?)["\']?\s*$', stripped)
            if m:
                current[m.group(1)] = m.group(2)
        elif not stripped.startswith((" ", "\t", "-")):
            in_registries = False
            if current:
                registries.append(current)
                current = {}
if current:
    registries.append(current)

config["private_registries"] = registries

# Parse extra_infra_patterns list
patterns = []
in_patterns = False
for line in raw.split("\n"):
    stripped = line.strip()
    if stripped.startswith("#") or not stripped:
        continue
    if stripped.startswith("extra_infra_patterns:"):
        in_patterns = True
        val = stripped.split(":", 1)[1].strip()
        if val == "[]":
            patterns = []
            in_patterns = False
        continue
    if in_patterns:
        if stripped.startswith("- "):
            val = stripped[2:].strip().strip("\"'")
            if val:
                patterns.append(val)
        elif not stripped.startswith((" ", "\t")):
            in_patterns = False

config["extra_infra_patterns"] = patterns

# Parse mode (advisory | enforce)
mode_match = re.search(r'^mode:\s*["\']?(\w+)["\']?', raw, re.MULTILINE)
config["mode"] = mode_match.group(1) if mode_match else "advisory"

print(json.dumps(config))
PARSECFG
  cat "$cache"
}

# Set up .npmrc with private registry auth in a given directory
setup_private_registries() {
  local target_dir="$1"
  local config_json
  config_json=$(_parse_bc_config)

  [[ "$config_json" == "{}" ]] && return 0

  _BC_CONFIG_JSON="$config_json" _BC_TARGET_DIR="$target_dir" python3 << 'SETUPREG'
import json, os, sys

config = json.loads(os.environ.get('_BC_CONFIG_JSON', '{}'))
target_dir = os.environ.get('_BC_TARGET_DIR', '.')

registries = config.get("private_registries", [])
if not registries:
    sys.exit(0)

npmrc_path = os.path.join(target_dir, ".npmrc")

# Read existing .npmrc (preserve non-registry lines)
existing_lines = []
if os.path.isfile(npmrc_path):
    with open(npmrc_path) as f:
        existing_lines = f.readlines()

# Build set of scopes/hosts we'll configure
scopes = {r.get("scope","") for r in registries if r.get("scope")}

# Filter out old lines for scopes we're replacing
filtered = []
for line in existing_lines:
    s = line.strip()
    skip = False
    for reg in registries:
        scope = reg.get("scope","")
        registry_url = reg.get("registry","")
        if scope and s.startswith(f"{scope}:registry="):
            skip = True; break
        if registry_url and "//" in registry_url:
            host = registry_url.split("//",1)[1]
            if s.startswith(f"//{host}"):
                skip = True; break
    if s.startswith("//") and ":_authToken=" in s:
        # Check if this is for one of our registries
        for reg in registries:
            rurl = reg.get("registry","")
            if rurl and "//" in rurl and rurl.split("//",1)[1].rstrip("/") in s:
                skip = True; break
    if not skip:
        filtered.append(line)

# Generate new .npmrc lines
new_lines = []
configured = 0
for reg in registries:
    scope = reg.get("scope","")
    registry_url = reg.get("registry","")
    auth_env = reg.get("auth_token_env","")

    if not scope or not registry_url:
        print(f"  [registry] SKIP: missing scope or registry in config")
        continue

    token = os.environ.get(auth_env, "")
    if not token:
        print(f"  [registry] WARNING: {auth_env} not set — {scope} may fail to install")
        new_lines.append(f"{scope}:registry={registry_url}\n")
        continue

    host_part = registry_url.split("//",1)[1] if "//" in registry_url else registry_url
    if not host_part.endswith("/"):
        host_part += "/"

    new_lines.append(f"{scope}:registry={registry_url}\n")
    new_lines.append(f"//{host_part}:_authToken={token}\n")
    new_lines.append(f"//{host_part}:always-auth=true\n")
    configured += 1
    print(f"  [registry] {scope} -> {registry_url} (auth: {auth_env})")

if new_lines:
    with open(npmrc_path, "w") as f:
        f.writelines(filtered)
        f.write("\n# -- breakability-check: private registry auth --\n")
        f.writelines(new_lines)
    print(f"  [registry] .npmrc updated: {configured} registry(ies) configured")

SETUPREG

  # Check if any registries are configured
  if echo "$config_json" | python3 -c "
import json, sys
c = json.load(sys.stdin)
sys.exit(0 if c.get('private_registries') else 1)
" 2>/dev/null; then
    PRIVATE_REGISTRY_CONFIGURED=true
    echo "  [registry] Private registry support: ENABLED"
  fi

  # ── Go private module support (GOPRIVATE + netrc) ──────────────
  # Reads go_private_modules from config and sets GOPRIVATE + ~/.netrc
  local go_private
  go_private=$(echo "$config_json" | python3 -c "
import json, sys, os
c = json.load(sys.stdin)
modules = c.get('go_private_modules', [])
if not modules:
    sys.exit(0)
goprivate = []
netrc_lines = []
for m in modules:
    pattern = m.get('pattern', '')
    if pattern:
        goprivate.append(pattern)
    host = m.get('host', '')
    auth_env = m.get('auth_token_env', '')
    if host and auth_env:
        token = os.environ.get(auth_env, '')
        if token:
            netrc_lines.append(f'machine {host}')
            netrc_lines.append(f'login token')
            netrc_lines.append(f'password {token}')
if goprivate:
    print('GOPRIVATE=' + ','.join(goprivate))
if netrc_lines:
    import pathlib
    netrc_path = pathlib.Path.home() / '.netrc'
    with open(netrc_path, 'a') as f:
        f.write('\n'.join(netrc_lines) + '\n')
    netrc_path.chmod(0o600)
    print(f'NETRC={len(netrc_lines)//3} entries')
" 2>/dev/null) || true
  if [[ -n "$go_private" ]]; then
    local gp_val="${go_private#GOPRIVATE=}"
    gp_val="${gp_val%%$'\n'*}"
    if [[ -n "$gp_val" && "$gp_val" != "GOPRIVATE=" ]]; then
      export GOPRIVATE="$gp_val"
      export GONOSUMDB="$gp_val"
      echo "  [registry] Go: GOPRIVATE=$GOPRIVATE"
    fi
    [[ "$go_private" == *"NETRC="* ]] && echo "  [registry] Go: ~/.netrc configured"
  fi
}

load_extra_infra_patterns() {
  # Load project-specific infra error patterns from config
  local config_json
  config_json=$(_parse_bc_config)
  [[ "$config_json" == "{}" ]] && return 0
  echo "$config_json" | python3 -c "
import json, sys
config = json.load(sys.stdin)
for p in config.get('extra_infra_patterns', []):
    print(p)
" 2>/dev/null
}

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

# ── Python import name mapping ────────────────────────────────────────────────
map_import_name() {
  local pkg="$1"
  local pkg_lower
  pkg_lower=$(echo "$pkg" | tr '[:upper:]' '[:lower:]')
  case "$pkg_lower" in
    pyyaml|pyyaml)  echo "yaml" ;;
    pillow)         echo "PIL" ;;
    scikit-learn)   echo "sklearn" ;;
    python-dateutil) echo "dateutil" ;;
    beautifulsoup4) echo "bs4" ;;
    *)              echo "$pkg" | tr '-' '_' ;;
  esac
}

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

detect_ecosystem() {
  local branch="$1"
  case "$branch" in
    dependabot/npm_and_yarn/*) echo "npm" ;;
    dependabot/go_modules/*)   echo "gomod" ;;
    dependabot/pip/*)           echo "pip" ;;
    dependabot/github_actions/*) echo "actions" ;;
    dependabot/docker/*)        echo "docker" ;;
    dependabot/maven/*)         echo "maven" ;;

    *)                          echo "unknown" ;;
  esac
}

# For monorepos: extract the subdirectory from the Dependabot branch name.
# e.g., "dependabot/npm_and_yarn/services/admin-service/axios-1.7.0" → "services/admin-service"
# e.g., "dependabot/docker/services/admin-service/node-22" → "services/admin-service"
# e.g., "dependabot/github_actions/actions/checkout-4" → "/" (root)
detect_pkg_dir() {
  local branch="$1" ecosystem="$2"
  local rest=""
  case "$ecosystem" in
    npm)     rest="${branch#dependabot/npm_and_yarn/}" ;;
    gomod)   rest="${branch#dependabot/go_modules/}" ;;
    pip)     rest="${branch#dependabot/pip/}" ;;
    docker)  rest="${branch#dependabot/docker/}" ;;
    maven)   rest="${branch#dependabot/maven/}" ;;

    actions) echo "/"; return ;;
    *)       echo "/"; return ;;
  esac
  # rest is e.g. "services/admin-service/axios-1.7.0"
  # We need everything before the last path component (the package/version)
  # Strategy: check if removing the last component gives a valid directory
  local dir="$rest"
  while [[ "$dir" == */* ]]; do
    dir="${dir%/*}"
    if [[ -f "${dir}/package.json" ]] || [[ -f "${dir}/go.mod" ]] || [[ -f "${dir}/requirements.txt" ]] || [[ -f "${dir}/Dockerfile" ]] || [[ -f "${dir}/pom.xml" ]]; then
      echo "$dir"
      return
    fi
  done
  echo "/"
}

detect_bump_type() {
  local from="$1" to="$2"
  # Strip leading v for comparison
  from="${from#v}"
  to="${to#v}"

  local from_major from_minor to_major to_minor
  from_major="${from%%.*}"
  to_major="${to%%.*}"
  from_minor="${from#*.}"
  from_minor="${from_minor%%.*}"
  to_minor="${to#*.}"
  to_minor="${to_minor%%.*}"

  if [[ "$from_major" != "$to_major" ]]; then
    echo "major"
  elif [[ "$from_minor" != "$to_minor" ]]; then
    # 0.x versions: per semver spec, major=0 means MINOR acts as the major version
    # 0.21->0.34 is effectively 1.0->14.0. Classify as major.
    if [[ "$from_major" == "0" ]]; then
      echo "major"
    else
      echo "minor"
    fi
  else
    echo "patch"
  fi
}

detect_dep_type_npm() {
  local pkg="$1"
  local pkg_json="${2:-package.json}"
  if jq -e ".dependencies[\"$pkg\"]" "$pkg_json" &>/dev/null; then
    echo "production"
  elif jq -e ".devDependencies[\"$pkg\"]" "$pkg_json" &>/dev/null; then
    echo "dev"
  elif jq -e ".peerDependencies[\"$pkg\"]" "$pkg_json" &>/dev/null; then
    echo "peer"
  elif jq -e ".optionalDependencies[\"$pkg\"]" "$pkg_json" &>/dev/null; then
    echo "optional"
  else
    echo "unknown"
  fi
}

detect_dep_type_go() {
  local pkg="$1"
  local search_dir="${2:-.}"
  # Check if only used in _test.go files (CR4-7: scope to PKG_DIR, not entire monorepo)
  local non_test_count
  non_test_count=$(grep -rn "\"$pkg" --include="*.go" "$search_dir" 2>/dev/null | grep -v "_test.go" | grep -v vendor/ | wc -l || echo "0")
  if [[ "$non_test_count" -eq 0 ]]; then
    # A non-test grep of 0 does NOT prove dev. database/sql drivers and other runtime
    # plugins are conventionally registered via a BLANK import (`_ "pkg"`) that may live
    # in a single main/cmd file outside the scoped search_dir (PR#38: github.com/lib/pq
    # is a production Postgres driver mislabeled dev). Two guards before concluding dev:
    #   1) a blank import anywhere in the repo (incl. outside search_dir) ⇒ production runtime
    #   2) a known runtime-driver allowlist ⇒ production
    local blank_count
    blank_count=$(grep -rEn "_[[:space:]]+\"$pkg\"" --include="*.go" "${REPO_ROOT:-.}" 2>/dev/null | grep -v "_test.go" | grep -v vendor/ | wc -l || echo "0")
    if [[ "$blank_count" -gt 0 ]]; then
      echo "production"
      return
    fi
    case "$pkg" in
      github.com/lib/pq|github.com/go-sql-driver/mysql|github.com/mattn/go-sqlite3|github.com/jackc/pgx/*|github.com/denisenkom/go-mssqldb|github.com/microsoft/go-mssqldb|github.com/godror/godror|github.com/ClickHouse/clickhouse-go/*|github.com/sijms/go-ora/*)
        echo "production"
        return
        ;;
    esac
    echo "dev"
  else
    echo "production"
  fi
}

detect_dep_relation() {
  local ecosystem="$1" pkg="$2"
  case "$ecosystem" in
    npm)
      local pkg_json="package.json"
      [[ -n "${PKG_DIR:-}" && "$PKG_DIR" != "/" && -f "$PKG_DIR/package.json" ]] && pkg_json="$PKG_DIR/package.json"
      if jq -e ".dependencies[\"$pkg\"] // .devDependencies[\"$pkg\"] // .peerDependencies[\"$pkg\"] // .optionalDependencies[\"$pkg\"]" "$pkg_json" &>/dev/null; then
        echo "direct"
      else
        echo "transitive"
      fi
      ;;
    gomod)
      # CR4-10: use the module's go.mod (PKG_DIR), not root go.mod in multi-module repos
      local go_mod="${3:-go.mod}"
      if grep -q "// indirect" "$go_mod" 2>/dev/null && grep "$pkg" "$go_mod" | grep -q "// indirect"; then
        echo "transitive"
      else
        echo "direct"
      fi
      ;;
    pip)
      if grep -qi "^${pkg}" requirements.txt 2>/dev/null; then
        echo "direct"
      else
        echo "transitive"
      fi
      ;;
    *) echo "direct" ;;
  esac
}

extract_cves() {
  local body="$1"
  echo "$body" | grep -oE 'CVE-[0-9]{4}-[0-9]{4,}' | sort -u | tr '\n' ',' | sed 's/,$//'
}

# ── Usage scan helpers ────────────────────────────────────────────────────────
scan_usage_npm() {
  local pkg="$1"
  # Also scan for @types/X → X (the runtime package the types describe).
  local scan_name="$pkg"
  [[ "$pkg" == @types/* ]] && scan_name="${pkg#@types/}"

  # Escape regex metacharacters in the package name (scoped pkgs contain '/', some
  # contain '.') so e.g. "react-dom" or "@scope/pkg.io" match literally.
  local esc
  esc=$(printf '%s' "$scan_name" | sed -E 's/[][(){}.^$*+?|\\]/\\&/g')

  # Match every static + dynamic import form for the EXACT package (optionally a
  # subpath import like pkg/sub), across the whole worktree minus build/vendor dirs.
  # A name boundary ((/…)?['"]) prevents react-router from matching react-router-dom.
  #   import … from 'pkg'      export … from 'pkg'
  #   require('pkg')           import('pkg')   (dynamic)
  grep -rnE \
    "(from|require\(|import\()[[:space:]]*['\"]${esc}(/[^'\"]+)?['\"]" \
    --include="*.ts" --include="*.tsx" --include="*.js" \
    --include="*.mjs" --include="*.cjs" --include="*.jsx" \
    --exclude-dir=node_modules --exclude-dir=dist --exclude-dir=build \
    --exclude-dir=.git --exclude-dir=coverage --exclude-dir=.next \
    --exclude-dir=out --exclude-dir=.turbo --exclude-dir=.cache \
    . 2>/dev/null | head -50 || true
}

scan_usage_go() {
  local pkg="$1"
  local search_dir="${2:-.}"
  # CR4-13: scope usage scan to the affected module directory when provided
  # Also scan for blank imports: _ "pkg" (database drivers, side-effect imports)
  { grep -rn "\"${pkg}" --include="*.go" "$search_dir" 2>/dev/null | grep -v vendor/;
    grep -rn "_ \"${pkg}" --include="*.go" "$search_dir" 2>/dev/null | grep -v vendor/; } | head -50 || true
}

# ── Go build scalability ─────────────────────────────────────────────────
# Large Go monorepos (3000+ files) can exhaust disk and timeout with `go build ./...`.
# go_targeted_build builds ONLY packages that import the upgraded dependency,
# extracted from FILES_IMPORTING. Falls back to ./... if no import data.
GO_TIMEOUT=${GO_TIMEOUT:-300}

go_free_disk() {
  # Free Go build cache to prevent "no space left on device" on runners
  go clean -cache 2>/dev/null || true
  # Remove old test caches too
  go clean -testcache 2>/dev/null || true
}

go_targeted_build() {
  # Usage: go_targeted_build <files_importing_json> [extra_args...]
  # Builds only the directories that import the upgraded package.
  # Multi-module aware: detects go.mod files and runs from correct module root.
  # Falls back to ./... if no import data available.
  local import_json="${1:-[]}"
  shift 2>/dev/null || true

  # Generate module-aware build commands
  # Pass import_json via env var to avoid triple-quote injection (Finding-4.8)
  local build_script
  build_script=$(_BC_IMPORT_JSON="$import_json" python3 -c "
import json, sys, os, subprocess

try:
    files = json.loads(os.environ.get('_BC_IMPORT_JSON', '[]'))
except:
    files = []

if not files:
    print('FALLBACK')
    sys.exit(0)

# Find all go.mod files to identify module boundaries
mod_roots = []
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('vendor', '.git', 'node_modules')]
    if 'go.mod' in fnames:
        mod_roots.append(os.path.normpath(root))

# Sort by depth (deepest first) for longest-prefix matching
mod_roots.sort(key=lambda x: -x.count('/'))
if not mod_roots:
    mod_roots = ['.']

# Group import files by their owning module
module_dirs = {}  # mod_root -> set of relative dirs
for f in files:
    path = f.split(':')[0]
    d = os.path.dirname(os.path.normpath(path))
    if not d or d == '.':
        d = '.'
    # Find which module owns this directory (longest prefix match)
    owning_mod = '.'
    for mr in mod_roots:
        if d == mr or d.startswith(mr + '/'):
            owning_mod = mr
            break
    # Make dir relative to the module root
    if owning_mod == '.':
        rel = './' + d.lstrip('./') + '/...' if d != '.' else './...'
    else:
        rel_d = os.path.relpath(d, owning_mod)
        rel = './' + rel_d + '/...' if rel_d != '.' else './...'
    module_dirs.setdefault(owning_mod, set()).add(rel)

# Output one line per module: MOD_ROOT|dir1 dir2 dir3
for mod, dirs in sorted(module_dirs.items()):
    print(f'{mod}|{\" \".join(sorted(dirs))}')
" 2>/dev/null)

  if [[ -z "$build_script" || "$build_script" == "FALLBACK" ]]; then
    echo "  full build: no import data available, building ./..."
    go_free_disk
    timeout -k 15 $GO_TIMEOUT go build -o /dev/null ./... "$@"
    return $?
  fi

  local _RC=0
  while IFS='|' read -r mod_root dirs; do
    [[ -z "$mod_root" || -z "$dirs" ]] && continue
    local dir_count
    dir_count=$(echo "$dirs" | wc -w | tr -d ' ')
    if [[ "$mod_root" == "." ]]; then
      echo "  targeted build (root module): $dir_count dirs"
    else
      echo "  targeted build ($mod_root module): $dir_count dirs"
    fi
    echo "    dirs: $dirs"
    go_free_disk
    (cd "$mod_root" && timeout -k 15 $GO_TIMEOUT go build -o /dev/null $dirs "$@") || _RC=$?
  done <<< "$build_script"
  return $_RC
}

go_targeted_vet() {
  local import_json="${1:-[]}"
  # Pass import_json via env var to avoid triple-quote injection (Finding-4.8)
  local build_script
  build_script=$(_BC_IMPORT_JSON="$import_json" python3 -c "
import json, sys, os

try:
    files = json.loads(os.environ.get('_BC_IMPORT_JSON', '[]'))
except:
    files = []

if not files:
    print('FALLBACK')
    sys.exit(0)

mod_roots = []
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('vendor', '.git', 'node_modules')]
    if 'go.mod' in fnames:
        mod_roots.append(os.path.normpath(root))
mod_roots.sort(key=lambda x: -x.count('/'))
if not mod_roots:
    mod_roots = ['.']

module_dirs = {}
for f in files:
    path = f.split(':')[0]
    d = os.path.dirname(os.path.normpath(path))
    if not d or d == '.':
        d = '.'
    owning_mod = '.'
    for mr in mod_roots:
        if d == mr or d.startswith(mr + '/'):
            owning_mod = mr
            break
    if owning_mod == '.':
        rel = './' + d.lstrip('./') + '/...' if d != '.' else './...'
    else:
        rel_d = os.path.relpath(d, owning_mod)
        rel = './' + rel_d + '/...' if rel_d != '.' else './...'
    module_dirs.setdefault(owning_mod, set()).add(rel)

for mod, dirs in sorted(module_dirs.items()):
    print(f'{mod}|{\" \".join(sorted(dirs))}')
" 2>/dev/null)

  if [[ -z "$build_script" || "$build_script" == "FALLBACK" ]]; then
    timeout -k 15 60 go vet ./... 2>&1 || true
    return
  fi

  while IFS='|' read -r mod_root dirs; do
    [[ -z "$mod_root" || -z "$dirs" ]] && continue
    (cd "$mod_root" && timeout -k 15 60 go vet $dirs 2>&1) || true
  done <<< "$build_script"
}

go_check_vulnerabilities() {
  # Usage: go_check_vulnerabilities <workdir>
  # Checks for known vulnerabilities using govulncheck if available.
  # Runs per-module with GOMEMLIMIT to prevent OOM-kills on large monorepos.
  # Writes VULN_STATUS (ok|vulns_found|failed_oom|failed_timeout|failed_error|not_installed)
  # to the last line (prefixed ###VULN_STATUS=) so callers can extract it.
  local workdir="${1:-.}"

  # Opt-in gate: Dependabot already supplies the CVE list, and govulncheck is a heavy
  # CPU/time cost on the self-hosted runner (esp. under TLS inspection). Default OFF.
  # Set BREAKABILITY_GOVULNCHECK=1 to re-enable the CVE-reachability hint scan.
  if [[ "${BREAKABILITY_GOVULNCHECK:-0}" != "1" ]]; then
    echo "  [security] govulncheck disabled (BREAKABILITY_GOVULNCHECK!=1) — Dependabot supplies CVE list; skipping"
    echo "###VULN_STATUS=skipped_disabled"
    return 0
  fi

  if ! command -v govulncheck &>/dev/null; then
    echo "  [security] govulncheck not installed — skipping vulnerability scan"
    echo "###VULN_STATUS=not_installed"
    return 0
  fi

  # Discover all go.mod roots (deepest first so submodules run independently)
  local mod_roots
  mod_roots=$(cd "$workdir" && find . -name go.mod -not -path './vendor/*' -not -path './.git/*' 2>/dev/null | sed 's|/go.mod$||' | sort)
  [[ -z "$mod_roots" ]] && mod_roots="."

  local any_oom=0 any_timeout=0 any_error=0 any_vuln=0 any_ok=0
  local combined_out=""

  while IFS= read -r mod; do
    [[ -z "$mod" ]] && continue
    local mod_label="${mod#./}"
    [[ -z "$mod_label" ]] && mod_label="(root)"
    echo "  [security] govulncheck: scanning module $mod_label"

    # GOMEMLIMIT caps heap — prevents OOM-killer (exit 137).
    # timeout 180s per module (was 120s for whole repo).
    local mod_out mod_exit
    mod_out=$(cd "$workdir/$mod" && GOMEMLIMIT=1500MiB GOGC=50 timeout -k 15 180 govulncheck ./... 2>&1)
    mod_exit=$?

    combined_out="$combined_out
=== module: $mod_label (exit=$mod_exit) ==="
    combined_out="$combined_out
$mod_out"

    case "$mod_exit" in
      0)   any_ok=1 ;;
      3)   any_vuln=1 ;;              # govulncheck exit 3 = vulns found
      124) any_timeout=1 ;;            # timeout
      137) any_oom=1 ;;                # SIGKILL (OOM)
      *)
        # Check output for OOM pattern even if exit isn't 137 (e.g., signal in nested shell)
        if echo "$mod_out" | grep -qiE "killed|out of memory|signal: killed|cannot allocate"; then
          any_oom=1
        elif echo "$mod_out" | grep -qiE "Vulnerability #[0-9]+|=== Symbol Results|=== Package Results"; then
          # Some govulncheck versions return non-zero on findings regardless
          any_vuln=1
        else
          any_error=1
        fi
        ;;
    esac
  done <<< "$mod_roots"

  # Print combined output for HOW_CHECKED section
  echo "$combined_out"

  # Determine overall status (priority: vulns_found > failed_* > ok)
  local status="ok"
  if [[ "$any_vuln" -eq 1 ]]; then
    status="vulns_found"
  elif [[ "$any_oom" -eq 1 ]]; then
    status="failed_oom"
  elif [[ "$any_timeout" -eq 1 ]]; then
    status="failed_timeout"
  elif [[ "$any_error" -eq 1 ]]; then
    status="failed_error"
  fi

  # Sentinel line (callers grep for ###VULN_STATUS=)
  echo "###VULN_STATUS=$status"
}

go_targeted_test() {
  # Usage: go_targeted_test <workdir> <files_importing_json>
  # Runs targeted tests only on packages that import the changed dependency.
  # Multi-module aware. Returns exit code from test run.
  local workdir="${1:-.}"
  local import_json="${2:-[]}"

  # Pass import_json and workdir via env vars to avoid injection (Finding-4.8)
  local test_script
  test_script=$(_BC_IMPORT_JSON="$import_json" _BC_WORKDIR="$workdir" python3 -c "
import json, sys, os

try:
    files = json.loads(os.environ.get('_BC_IMPORT_JSON', '[]'))
except:
    files = []

if not files:
    print('FALLBACK')
    sys.exit(0)

# Walk from workdir to find go.mod files
workdir = os.environ.get('_BC_WORKDIR', '.')
mod_roots = []
for root, dirs, fnames in os.walk(workdir):
    dirs[:] = [d for d in dirs if d not in ('vendor', '.git', 'node_modules')]
    if 'go.mod' in fnames:
        mod_roots.append(os.path.relpath(root, workdir))

mod_roots = [os.path.normpath(m) for m in mod_roots]
mod_roots.sort(key=lambda x: -x.count('/'))
if not mod_roots:
    mod_roots = ['.']

module_dirs = {}
for f in files:
    path = f.split(':')[0]
    d = os.path.dirname(os.path.normpath(path))
    if not d or d == '.':
        d = '.'
    owning_mod = '.'
    for mr in mod_roots:
        if d == mr or d.startswith(mr + '/'):
            owning_mod = mr
            break
    if owning_mod == '.':
        rel = './' + d.lstrip('./') + '/...' if d != '.' else './...'
    else:
        rel_d = os.path.relpath(d, owning_mod)
        rel = './' + rel_d + '/...' if rel_d != '.' else './...'
    module_dirs.setdefault(owning_mod, set()).add(rel)

for mod, dirs in sorted(module_dirs.items()):
    print(f'{mod}|{chr(32).join(sorted(dirs))}')" 2>/dev/null)

  if [[ -z "$test_script" || "$test_script" == "FALLBACK" ]]; then
    echo "  go test: no import data, running full ./..."
    local _RC=0
    for mod_root in .; do
      (cd "$workdir" && timeout -k 15 $GO_TIMEOUT go test ./... 2>&1) || _RC=$?
    done
    return $_RC
  fi

  local _RC=0
  local _OUTPUT=""
  while IFS='|' read -r mod_root dirs; do
    [[ -z "$mod_root" || -z "$dirs" ]] && continue
    local abs_mod="$workdir"
    [[ "$mod_root" != "." ]] && abs_mod="$workdir/$mod_root"
    local dir_count
    dir_count=$(echo "$dirs" | wc -w | tr -d ' ')
    echo "    testing $mod_root module: $dir_count dirs — $dirs"
    (cd "$abs_mod" && timeout -k 15 $GO_TIMEOUT go test -timeout 5m -race $dirs 2>&1) || _RC=$?
  done <<< "$test_script"
  return $_RC
}

scan_usage_pip() {
  local pkg="$1"
  local import_name
  import_name=$(map_import_name "$pkg")
  grep -rn "from ${import_name} import\\|import ${import_name}" \
    --include="*.py" . 2>/dev/null | head -50 || true
}

format_usage_files() {
  # Takes grep output (file:line:content), outputs JSON array of unique "file:line"
  # V9.6 FIX: deduplicate by FILE PATH only — a file importing multiple sub-packages
  # of the same module (e.g., k8s.io/client-go/kubernetes + k8s.io/client-go/rest)
  # previously appeared once per import line. Now deduped to one entry per file.
  local input="$1"
  if [[ -z "$input" ]]; then
    echo "[]"
    return
  fi
  # Extract file paths only (strip line numbers), dedup, then format as JSON array
  echo "$input" | awk -F: '{print $1}' | sort -u | \
    python3 -c "import sys,json; lines=[l.strip() for l in sys.stdin.read().strip().split('\n') if l.strip()]; print(json.dumps(lines))" 2>/dev/null || echo "[]"
}

# ──────────────────────────────────────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────────────────────────────────────


# ── Monorepo: workspace dependency graph ──────────────────────
build_workspace_dep_graph() {
  local repo_dir="${1:-.}"
  python3 - "$repo_dir" << 'GRAPHEOF'
import json, os, glob, sys, re
repo = sys.argv[1]
pkgs = {}
for pj in glob.glob(os.path.join(repo, "**/package.json"), recursive=True):
    if "node_modules" in pj: continue
    try:
        with open(pj) as f: data = json.load(f)
    except: continue
    name = data.get("name", "")
    if not name: continue
    rel_path = os.path.relpath(os.path.dirname(pj), repo)
    deps = data.get("dependencies", {})
    dev_deps = data.get("devDependencies", {})
    internal_deps = [d for d in deps if "netapp" in d.lower() or "datamigrate" in d.lower()]
    nestjs_versions = {k: v for k, v in {**deps, **dev_deps}.items() if k.startswith("@nestjs/")}
    pkgs[name] = {"path": rel_path, "internal_deps": internal_deps, "nestjs_versions": nestjs_versions, "all_dep_names": list(deps.keys())}
consumers = {}
for name, info in pkgs.items():
    for dep in info["internal_deps"]:
        for lib_name, lib_info in pkgs.items():
            if lib_name.lower() == dep.lower():
                consumers.setdefault(lib_name, []).append({"service": name, "path": info["path"]})
nestjs_skew = []
all_nestjs = set()
for info in pkgs.values(): all_nestjs.update(info["nestjs_versions"].keys())
for npkg in sorted(all_nestjs):
    vbs = {n: i["nestjs_versions"][npkg] for n, i in pkgs.items() if npkg in i["nestjs_versions"]}
    majors = set()
    for v in vbs.values():
        m = re.match(r"[^0-9]*([0-9]+)", v)
        if m: majors.add(m.group(1))
    if len(majors) > 1: nestjs_skew.append({"package": npkg, "versions": vbs, "majors": sorted(majors)})
result = {"packages": {n: {"path": i["path"], "internal_deps": i["internal_deps"], "nestjs_versions": i["nestjs_versions"]} for n, i in pkgs.items()}, "consumers": consumers, "nestjs_skew": nestjs_skew}
with open("/tmp/_bc_workspace_graph.json", "w") as f: json.dump(result, f, indent=2)
for lib, svcs in consumers.items(): print(f"  {lib} consumed by: {', '.join(s['service'] for s in svcs)}")
for skew in nestjs_skew: print(f"  NestJS skew: {skew['package']} has majors {skew['majors']}")
GRAPHEOF
}

# ── Test oracle: build workspace-internal libs to dist/ before a consumer build ─
# Services import private @scope/* workspace libs (resolved via the file: fallback)
# whose package.json points main/types at dist/. Until those libs are COMPILED, the
# consumer's `tsc --noEmit` / `npm test` fails to resolve the workspace import — a
# PRE-EXISTING infra failure unrelated to the dependency bump, which leaves build at
# `pre_existing` and denies us a real PASS/FAIL test oracle. This builds the internal
# libs in topological order (deps first) so the consumer compiles against real .d.ts.
# Fail-open by design: any per-lib install/compile error is logged and skipped — we
# NEVER abort the pipeline and NEVER manufacture a green (a still-missing lib simply
# leaves the consumer build as it was). tsc emits .d.ts even with unrelated peer-dep
# errors, which is exactly what the consumer needs to resolve types.
build_npm_workspace_libs() {
  local worktree="${1:-.}"
  local timeout_s="${2:-300}"
  # Topologically order internal libs (name contains netapp/datamigrate) from the
  # workspace graph, deps first. Emits "relpath\tname" lines.
  local order
  order=$(_BC_WT="$worktree" python3 - << 'TOPOEOF'
import json, os, sys
try:
    g = json.load(open("/tmp/_bc_workspace_graph.json"))
except Exception:
    sys.exit(0)
pkgs = g.get("packages", {})
def is_internal(n):
    n = (n or "").lower()
    return "netapp" in n or "datamigrate" in n
libs = {n: i for n, i in pkgs.items() if is_internal(n)}
# Map lower(name)->canonical for resolving internal_deps regardless of case.
by_lower = {n.lower(): n for n in libs}
visited, order = set(), []
def visit(n, stack):
    if n in visited or n in stack:
        return
    stack.add(n)
    for dep in libs.get(n, {}).get("internal_deps", []) or []:
        c = by_lower.get((dep or "").lower())
        if c and c in libs:
            visit(c, stack)
    stack.discard(n)
    visited.add(n)
    order.append(n)
for n in libs:
    visit(n, set())
wt = os.environ.get("_BC_WT", ".")
for n in order:
    rel = libs[n].get("path", "")
    if rel and os.path.isfile(os.path.join(wt, rel, "package.json")):
        print(f"{rel}\t{n}")
TOPOEOF
)
  [[ -z "$order" ]] && return 0
  local built=0
  while IFS=$'\t' read -r rel name; do
    [[ -z "$rel" ]] && continue
    local libdir="$worktree/$rel"
    [[ -d "$libdir/dist" ]] && { echo "  [test-oracle] $name: dist/ present, skip"; continue; }
    setup_private_registries "$libdir" 2>/dev/null || true
    # Rewrite this lib's own internal @scope/* deps to file: links so a non-leaf lib
    # (e.g. api-handler-lib -> logger-lib) installs against the already-built sibling
    # instead of the unreachable private registry. Topological order guarantees the
    # dependency's dist/ already exists by the time we reach the dependent lib.
    rewrite_private_deps_to_local "$libdir" "$worktree" 2>/dev/null || true
    ( cd "$libdir" && retry_cmd 2 3 timeout "$timeout_s" npm ci --ignore-scripts >/dev/null 2>&1 ) \
      || ( cd "$libdir" && timeout "$timeout_s" npm install --ignore-scripts --legacy-peer-deps >/dev/null 2>&1 ) \
      || { echo "  [test-oracle] $name: install failed (skip)"; continue; }
    # Prefer the package's own build script; fall back to a bare tsc. tsc emits the
    # .d.ts even when unrelated peer-dep type errors are present, so ignore the exit.
    if ( cd "$libdir" && timeout "$timeout_s" npm run build >/dev/null 2>&1 ); then :; else
      ( cd "$libdir" && timeout "$timeout_s" npx tsc >/dev/null 2>&1 ) || true
    fi
    if [[ -d "$libdir/dist" ]]; then
      echo "  [test-oracle] $name: built dist/"
      built=$((built+1))
    else
      echo "  [test-oracle] $name: no dist/ after build (skip)"
    fi
  done <<< "$order"
  [[ "$built" -gt 0 ]] && echo "  [test-oracle] built $built workspace lib(s)"
  return 0
}

check_cascade_impact() {
  local pkg_dir="$1"
  _BC_PKG_DIR="$pkg_dir" python3 -c "
import json, os
try:
    pkg_dir = os.environ.get('_BC_PKG_DIR', '/')
    with open('/tmp/_bc_workspace_graph.json') as f: g = json.load(f)
    pn = next((n for n, i in g.get('packages',{}).items() if i['path']==pkg_dir), None)
    if not pn: pn = next((n for n, i in g.get('packages',{}).items() if i['path'].lower()==pkg_dir.lower()), None)
    cs = g.get('consumers',{}).get(pn, []) if pn else []
    if not cs and pn:
        for k, v in g.get('consumers',{}).items():
            if k.lower() == pn.lower(): cs = v; break
    print(json.dumps(cs))
except: print('[]')
" 2>/dev/null
}

classify_npm_error() {
  local output="$1"
  if echo "$output" | grep -qE 'E401|E403|ENOTFOUND|ETIMEDOUT|EAI_AGAIN|code E401|code E403'; then
    echo "infra_error"
  elif echo "$output" | grep -qE 'ERESOLVE|peer dep|Could not resolve dependency'; then
    echo "peer_dep_conflict"
  elif echo "$output" | grep -qE 'Invalid.*lock|lock.?file|sha512.*integrity|EUSAGE.*lock|package-lock\.json.*in sync|Missing:.*from lock'; then
    echo "lockfile_desync"
  else
    echo "build_fail"
  fi
}

# ── Go error normalization ────────────────────────────────────────────────
# Normalize Go compiler/linker error lines so that path-only differences
# (build cache hashes, GOMODCACHE versions, worktree roots, GOPATH)
# don't cause false "new error" detections when diffing main vs PR output.
normalize_go_errors() {
  # Reads stdin, writes normalized lines to stdout.
  sed \
    -e "s|${WORKTREE_BASE}[^/]*/|./|g" \
    -e 's|go-build/[a-f0-9]*/[a-f0-9]*|go-build/HASH|g' \
    -e 's|[^ ]*/go/pkg/mod/|GOMODCACHE/|g' \
    -e 's|@v[0-9][0-9.]*[^/:]*/|@VERSION/|g'
}

# Classify Go build failures. Detects cache corruption vs real compile errors.
classify_go_error() {
  local output="$1"
  local exit_code="${2:-0}"
  # Cache corruption: "open …/go-build/…: no such file or directory"
  if echo "$output" | grep -qE 'go-build/[a-f0-9]+.*no such file or directory'; then
    echo "cache_corruption"
  # V9.8 iter6 (E): exit 137 = 128 + SIGKILL (OOM). Recognize by exit code even when output empty.
  elif [[ "$exit_code" -eq 137 ]]; then
    echo "resource_exhaustion"
  # OOM / resource exhaustion: compiler killed by OS, out of memory (A2-1)
  elif echo "$output" | grep -qiE 'signal: killed|cannot allocate memory|out of memory|oom-kill'; then
    echo "resource_exhaustion"
  # Timeout: exit code 124 from timeout(1) — build didn't finish (A2-2)
  elif [[ "$exit_code" -eq 124 ]]; then
    echo "timeout"
  # Network / module download failures / checksum database issues
  elif echo "$output" | grep -qE 'GONOSUMDB|GONOSUMCHECK|GOSUMDB|GOPROXY|checksum database disabled|checksum mismatch|connection refused|dial tcp|TLS handshake timeout|module lookup disabled|proxyconnect|i/o timeout'; then
    echo "infra_error"
  # Private module access denied
  elif echo "$output" | grep -qE '410 Gone|404 Not Found.*module|fatal:.*Authentication|could not read Username'; then
    echo "private_module"
  else
    echo "build_fail"
  fi
}

# Rewrite private scoped deps to file: links when private registry is inaccessible.
# In monorepos, @org/foo-lib packages often exist locally at lib/foo-lib/ or packages/foo-lib/.
# This lets npm install succeed without registry auth for workspace-internal dependencies.
# Args: $1 = build_dir (the service dir), $2 = worktree root
rewrite_private_deps_to_local() {
  local build_dir="$1"
  local worktree="$2"
  
  # Strip auth tokens from .npmrc so npm doesn't try (and fail) to auth
  if [[ -f "$build_dir/.npmrc" ]]; then
    sed -i.bak \
      -e '/:_authToken/d' \
      -e '/always-auth/d' \
      "$build_dir/.npmrc" 2>/dev/null || true
  fi
  
  [[ -f "$build_dir/package.json" ]] || return 1
  
  # CR5-5: Use quoted heredoc ('REWRITEEOF') and pass paths via env vars
  # to prevent shell injection from paths with special characters.
  _BC_BUILD_DIR="$build_dir" _BC_WORKTREE="$worktree" python3 << 'REWRITEEOF'
import json, os, glob

build_dir = os.environ["_BC_BUILD_DIR"]
worktree = os.environ["_BC_WORKTREE"]
pkg_path = os.path.join(build_dir, "package.json")

with open(pkg_path) as f:
    pkg = json.load(f)

changed = 0
for dep_key in ("dependencies", "devDependencies"):
    deps = pkg.get(dep_key, {})
    for name, ver in list(deps.items()):
        if ver.startswith("file:"):
            continue
        # Check if this scoped package has a matching local directory
        short = name.split("/")[-1] if "/" in name else name
        for candidate in glob.glob(os.path.join(worktree, "lib", "*", "package.json")) + \
                         glob.glob(os.path.join(worktree, "packages", "*", "package.json")):
            try:
                with open(candidate) as cf:
                    cpkg = json.load(cf)
                if cpkg.get("name", "").lower() == name.lower():
                    rel = os.path.relpath(os.path.dirname(candidate), build_dir)
                    deps[name] = f"file:{rel}"
                    changed += 1
                    print(f"  rewrite: {name} -> file:{rel}")
                    break
            except Exception:
                pass

if changed:
    with open(pkg_path, "w") as f:
        json.dump(pkg, f, indent=2)
    print(f"  {changed} dep(s) rewritten")
REWRITEEOF
}


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
  FILTERED_JSON=$(echo "$PR_JSON" | _BC_PR_FILTER="$PR_FILTER" python3 -c "
import json, sys, os, re
prs = json.load(sys.stdin)
pr_filter = os.environ.get('_BC_PR_FILTER', '')
allowed = set()
for token in pr_filter.split(','):
    token = token.strip().lstrip('#')
    if not re.fullmatch(r'[0-9]+', token or ''):
        continue
    allowed.add(token)
filtered = [p for p in prs if str(p['number']) in allowed]
print(json.dumps(filtered))
")
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

# npm baseline — for monorepos, baselines are built lazily per-directory
# We define a function that builds the baseline for a specific directory on demand.
# This avoids building ALL 12+ services upfront (which would take 30+ minutes).
build_npm_baseline_for_dir() {
  local target_dir="$1"  # relative path like "services/admin-service" or "."
  local dir_key="${target_dir//\//_}"
  local marker="/tmp/_bc_main_npm_done_${dir_key}.txt"
  
  # Skip if already built
  if [[ -f "$marker" ]]; then
    return 0
  fi
  
  local full_dir="$MAIN_DIR"
  [[ "$target_dir" != "." && "$target_dir" != "/" ]] && full_dir="$MAIN_DIR/$target_dir"
  
  if [[ ! -f "$full_dir/package.json" ]]; then
    echo "-1" > "/tmp/_bc_main_npm_install_${dir_key}.txt"
    echo "-1" > "/tmp/_bc_main_npm_tsc_${dir_key}.txt"
    echo "" > "/tmp/_bc_main_npm_out_${dir_key}.txt"
    echo "" > "/tmp/_bc_main_npm_tscout_${dir_key}.txt"
    echo "1" > "$marker"
    return 0
  fi
  
  echo "  [lazy baseline] npm ci in $target_dir ..."
  # Set up private registry auth if configured
  setup_private_registries "$full_dir"
  local dir_install_out dir_install_exit dir_tsc_out dir_tsc_exit
  dir_install_out=$(cd "$full_dir" && retry_cmd 3 5 timeout $TIMEOUT npm ci --ignore-scripts 2>&1)
  dir_install_exit=$?
  # If npm ci fails with infra_error, try workspace-local fallback
  if [[ "$dir_install_exit" -ne 0 ]]; then
    local err_class
    err_class=$(classify_npm_error "$dir_install_out")
    if [[ "$err_class" == "infra_error" ]]; then
      echo "  [lazy baseline] infra_error — trying workspace-local fallback..."
      rewrite_private_deps_to_local "$full_dir" "$MAIN_DIR"
      dir_install_out=$(cd "$full_dir" && timeout $TIMEOUT npm install --ignore-scripts --legacy-peer-deps 2>&1)
      dir_install_exit=$?
      [[ "$dir_install_exit" -eq 0 ]] && echo "  [lazy baseline] workspace-local fallback: SUCCESS"
    elif [[ "$err_class" == "lockfile_desync" ]]; then
      echo "  [lazy baseline] lockfile_desync — trying npm install fallback..."
      rewrite_private_deps_to_local "$full_dir" "$MAIN_DIR"
      dir_install_out=$(cd "$full_dir" && timeout $TIMEOUT npm install --ignore-scripts --legacy-peer-deps 2>&1)
      dir_install_exit=$?
      [[ "$dir_install_exit" -eq 0 ]] && echo "  [lazy baseline] npm install fallback: SUCCESS"
    fi
  fi
  if [[ "$dir_install_exit" -eq 0 && -f "$full_dir/tsconfig.json" ]]; then
    # Build workspace-internal libs to dist/ so the consumer resolves @scope/* types
    # (symmetric with the PR build/test worktrees — keeps build comparison honest).
    build_npm_workspace_libs "$MAIN_DIR" "$TIMEOUT"
    echo "  [lazy baseline] tsc in $target_dir ..."
    dir_tsc_out=$(cd "$full_dir" && timeout $TIMEOUT npx tsc --noEmit 2>&1)
    dir_tsc_exit=$?
  else
    dir_tsc_exit=-1
    dir_tsc_out=""
  fi
  
  echo "$dir_install_exit" > "/tmp/_bc_main_npm_install_${dir_key}.txt" 2>/dev/null || true
  echo "$dir_tsc_exit" > "/tmp/_bc_main_npm_tsc_${dir_key}.txt" 2>/dev/null || true
  echo "$dir_install_out" > "/tmp/_bc_main_npm_out_${dir_key}.txt" 2>/dev/null || true
  echo "$dir_tsc_out" > "/tmp/_bc_main_npm_tscout_${dir_key}.txt" 2>/dev/null || true
  echo "1" > "$marker"
  echo "  [lazy baseline] $target_dir: install=$dir_install_exit tsc=$dir_tsc_exit"
}

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
elif [[ -f "$MAIN_DIR/go.mod" ]]; then
  # Check for multi-module layout (multiple go.mod without go.work)
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

# ── Build workspace dependency graph ─────────────────────────────────
echo ""
echo "════════════ WORKSPACE DEPENDENCY GRAPH ════════════"
build_workspace_dep_graph "$REPO_ROOT"

echo ""
echo "════════════ DYNAMIC PEER DEPENDENCY DISCOVERY ════════════"
python3 << 'PEERDEPS_SCRIPT'
import json, os, glob
peer_groups = {}
for pj_path in glob.glob(os.path.join(os.environ.get("REPO_ROOT", "."), "**/package.json"), recursive=True):
    if "node_modules" not in pj_path: continue
    try:
        with open(pj_path) as f: data = json.load(f)
    except: continue
    name = data.get("name", "")
    peers = data.get("peerDependencies", {})
    if name and peers: peer_groups[name] = list(peers.keys())
nestjs_group = set()
for pkg, peers in peer_groups.items():
    if pkg.startswith("@nestjs/"):
        nestjs_group.add(pkg)
        nestjs_group.update(p for p in peers if p.startswith("@nestjs/"))
react_group = set()
for pkg, peers in peer_groups.items():
    if "react" in pkg.lower():
        react_group.add(pkg)
        react_group.update(p for p in peers if "react" in p.lower())
result = {"peer_groups": peer_groups, "nestjs_group": sorted(nestjs_group), "react_group": sorted(react_group)}
with open("/tmp/_bc_peer_groups.json", "w") as f: json.dump(result, f, indent=2)
if nestjs_group: print(f"  NestJS peer group: {len(nestjs_group)} packages")
if react_group: print(f"  React peer group: {len(react_group)} packages")
print(f"  Total packages with peer deps: {len(peer_groups)}")
PEERDEPS_SCRIPT


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
  python3 -c '
import json, sys
alerts = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
        if isinstance(obj, list):
            alerts.extend(obj)
        else:
            alerts.append(obj)
    except json.JSONDecodeError:
        pass
with open(sys.argv[1], "w") as f:
    json.dump(alerts, f)
print(len(alerts))
' "$_BC_ALERTS_CACHE" < "$_BC_ALERTS_RAW"
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

  # Initialize vuln-scan outputs (in case code path skips the scan below)
  printf 'unknown' > "/tmp/_bc_vuln_status_${PR_NUM}.txt"
  printf '' > "/tmp/_bc_vuln_finding_${PR_NUM}.txt"
  printf '' > "/tmp/_bc_vuln_new_findings_${PR_NUM}.txt"
  printf '0' > "/tmp/_bc_vuln_preexisting_count_${PR_NUM}.txt"
  printf '' > "/tmp/_bc_vuln_output_${PR_NUM}.txt"
  printf '' > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
  printf 'null' > "$BC_SCRATCH_DIR/_bc_go_resolution_exit_${PR_NUM}.txt"
  printf '' > "$BC_SCRATCH_DIR/_bc_go_resolution_output_${PR_NUM}.txt"
  printf '' > "$BC_SCRATCH_DIR/_bc_go_modsum_diff_${PR_NUM}.txt"
  printf '' > "$BC_SCRATCH_DIR/_bc_usage_raw_${PR_NUM}.txt"
  printf '' > "$BC_SCRATCH_DIR/_bc_cli_output_${PR_NUM}.txt"
  printf '' > "$BC_SCRATCH_DIR/_bc_smoke_output_${PR_NUM}.txt"

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
    python3 << SKIPEOF
import json, os
results_file = "$RESULTS_FILE"
pr_num = "$PR_NUM"
try:
    with open(f"/tmp/_bc_skip_title_{pr_num}.txt") as f:
        pr_title = f.read().strip()
except:
    pr_title = "unknown"
pr_branch = "$_SKIP_BRANCH"
with open(results_file) as f:
    data = json.load(f)
data["prs"][pr_num] = {
    "package": pr_title,
    "from": "",
    "to": "",
    "ecosystem": "unknown",
    "bump": "unknown",
    "dep_type": "unknown",
    "dep_relation": "unknown",
    "cves": [],
    "build": {"verdict": "skipped", "main_exit": -1, "pr_exit": -1, "output_tail": "", "new_errors": [], "install_method": "none", "error_class": ""},
    "test": {"ran": False, "exit": None, "output_tail": ""},
    "smoke": {"ran": False, "exit": None},
    "files_importing": [],
    "additional_imports": [],
    "diff_lines": 0,
    "diff_truncated": False,
    "pkg_dir": "/",
    "cascade_impact": [],
    "nestjs_peer_warning": "",
    "install_ok": False,
    "additional_packages": "",
    "mergeable_status": "UNKNOWN",
    "npm_audit": {"critical": 0, "high": 0},
    "ownership_class": "unknown",
    "verification_level": -1,
    "verification_label": "NA_not_applicable",
    "verification_steps": [],
    "evidence": [{
        "signal": "dependency_resolution",
        "label": "Dependency resolution",
        "status": "skipped",
        "command": "",
        "stdout": "",
        "exit_code": None,
        "summary": "PR skipped by breakability:skip label",
        "na_reason": "breakability:skip label"
    }],
    "skip_reason": "breakability:skip label"
}
_tmp = results_file + ".tmp"
with open(_tmp, "w") as f:
    json.dump(data, f, indent=2)
os.rename(_tmp, results_file)
print(f"  ✓ PR #{pr_num} written (skipped)")
SKIPEOF
    continue
  fi

  # Skip non-Dependabot PRs (safety guard — label filter should catch these)
  if [[ "$PR_BRANCH" != dependabot/* ]]; then
    echo "  ⏭️  SKIP — not a Dependabot branch: $PR_BRANCH"
    continue
  fi

  INSTALL_METHOD="ci"
  ERROR_CLASS=""
  CASCADE_IMPACT="[]"
  NESTJS_PEER_WARNING=""
  INSTALL_OK="false"
  MERGEABLE_STATUS="UNKNOWN"
  NEW_ERRORS=""

  # Check mergeable status — skip deep analysis for conflicted PRs
  MERGEABLE_JSON=$(gh pr view "$PR_NUM" --json mergeable,mergeStateStatus 2>/dev/null || echo '{}')
  MERGEABLE_STATUS=$(echo "$MERGEABLE_JSON" | jq -r '.mergeable // "UNKNOWN"')
  MERGE_STATE=$(echo "$MERGEABLE_JSON" | jq -r '.mergeStateStatus // "UNKNOWN"')
  echo "  mergeable: $MERGEABLE_STATUS ($MERGE_STATE)"

  # If PR has merge conflicts, record it and skip full analysis
  if [[ "$MERGEABLE_STATUS" == "CONFLICTING" ]]; then
    echo "  ⚠️  PR has merge conflicts — skipping build analysis"
    BUILD_VERDICT="conflict"
    # Still need to parse title for package info, then write minimal JSON
  fi

  # Detect ecosystem
  ECOSYSTEM=$(detect_ecosystem "$PR_BRANCH")
  echo "  ecosystem: $ECOSYSTEM"

  # Detect package subdirectory for monorepos
  PKG_DIR=$(cd "$REPO_ROOT" && detect_pkg_dir "$PR_BRANCH" "$ECOSYSTEM")
  echo "  pkg_dir: $PKG_DIR"

  # Parse package name and versions from title
  # Handles: "Bump X from A to B", "Bump X and Y"
  PKG=""
  FROM_VER=""
  TO_VER=""
  ADDITIONAL_PACKAGES=""

  # Dependabot uses two title styles: legacy "Bump X from A to B" and the
  # conventional-commits "build(deps): bump X from A to B" (lowercase, prefixed).
  # Match [Bb]ump unanchored so both styles — and scoped names like @nestjs/common
  # — are captured. ("build" contains no "bump" substring, so this is unambiguous.)
  if [[ "$PR_TITLE" =~ [Bb]ump[[:space:]]+(.+)[[:space:]]+from[[:space:]]+([^ ]+)[[:space:]]+to[[:space:]]+([^ ]+) ]]; then
    PKG="${BASH_REMATCH[1]}"
    FROM_VER="${BASH_REMATCH[2]}"
    TO_VER="${BASH_REMATCH[3]}"
  elif [[ "$PR_TITLE" =~ [Bb]ump[[:space:]]+(.+)[[:space:]]+and[[:space:]]+(.*) ]]; then
    # Multi-package PR — take the first package name, record others
    PKG="${BASH_REMATCH[1]}"
    ADDITIONAL_PACKAGES="${BASH_REMATCH[2]}"
    # Clean "in /dir" from additional packages
    ADDITIONAL_PACKAGES=$(echo "$ADDITIONAL_PACKAGES" | sed 's/ in \/.*$//')
    # Try multiple patterns from PR body to find versions:
    FIRST_BUMP_LINE=""
    for pattern in \
      'from \`\?[0-9][0-9.]*\`\? to \`\?[0-9][0-9.]*\`\?' \
      '[Uu]pdates.*from [0-9][0-9.]* to [0-9][0-9.]*' \
      '[Bb]umps.*from [0-9][0-9.]* to [0-9][0-9.]*'; do
      FIRST_BUMP_LINE=$(echo "$PR_BODY" | tr -d '`' | grep -m1 -oE "$pattern" || true)
      [[ -n "$FIRST_BUMP_LINE" ]] && break
    done
    if [[ -n "$FIRST_BUMP_LINE" ]]; then
      FROM_VER=$(echo "$FIRST_BUMP_LINE" | grep -oE '[0-9][0-9.]*' | head -1)
      TO_VER=$(echo "$FIRST_BUMP_LINE" | grep -oE '[0-9][0-9.]*' | tail -1)
    fi
    echo "  multi-package PR: $PKG + $ADDITIONAL_PACKAGES"
  fi

  # Sanitize: strip any trailing HTML/whitespace from version strings
  FROM_VER=$(echo "$FROM_VER" | tr -d '\n\r' | sed 's/[^0-9a-zA-Z._-].*//; s/[[:space:]]//g')
  TO_VER=$(echo "$TO_VER" | tr -d '\n\r' | sed 's/[^0-9a-zA-Z._-].*//; s/[[:space:]]//g')

  echo "  package: $PKG ($FROM_VER → $TO_VER)"

  # Bump type
  BUMP="unknown"
  if [[ -n "$FROM_VER" && -n "$TO_VER" ]]; then
    BUMP=$(detect_bump_type "$FROM_VER" "$TO_VER")
  fi
  echo "  bump: $BUMP"

  # Update-type risk profile: patch updates are SAFE (no breaking changes by semver).
  # Major updates carry HIGH_RISK (semver contract broken, API surface changed).
  # Minor updates are MODERATE_RISK (new features, but backwards compatible).

  # Dep type
  DEP_TYPE="unknown"
  case "$ECOSYSTEM" in
    npm)     
      if [[ "$PKG_DIR" != "/" && -f "$PKG_DIR/package.json" ]]; then
        DEP_TYPE=$(detect_dep_type_npm "$PKG" "$PKG_DIR/package.json")
      else
        DEP_TYPE=$(detect_dep_type_npm "$PKG")
      fi
      ;;
    gomod)
      # CR4-7: pass PKG_DIR to scope grep to the affected module
      if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
        DEP_TYPE=$(detect_dep_type_go "$PKG" "$PKG_DIR")
      else
        DEP_TYPE=$(detect_dep_type_go "$PKG")
      fi
      ;;
    pip)     DEP_TYPE="production" ;;
    actions) DEP_TYPE="dev" ;;
    docker)  DEP_TYPE="production" ;;
    maven)   DEP_TYPE="production" ;;

  esac
  echo "  dep_type: $DEP_TYPE"

  # Dep relation (CR4-10: pass correct go.mod path for multi-module repos)
  _GO_MOD_PATH="go.mod"
  if [[ "$ECOSYSTEM" == "gomod" && "$PKG_DIR" != "/" && -f "${PKG_DIR}/go.mod" ]]; then
    _GO_MOD_PATH="${PKG_DIR}/go.mod"
  fi
  DEP_RELATION=$(detect_dep_relation "$ECOSYSTEM" "$PKG" "$_GO_MOD_PATH")
  echo "  dep_relation: $DEP_RELATION"

  # Security / CVEs — from PR body AND Dependabot alerts cache
  # Dependabot usually does NOT put CVE/GHSA IDs in PR bodies.
  # We enrich from the cached alerts API response.
  CVES=$(extract_cves "$PR_BODY")
  # Enrich from Dependabot alerts: find alerts matching this package name
  # V8 FIX: Also extract severity, CVSS score, and advisory URL for each CVE
  CVE_DETAILS="[]"
  if [[ -f "$_BC_ALERTS_CACHE" ]]; then
    _CVE_ENRICH=$(python3 -c "
import json, sys
pkg = \"$PKG\"
try:
    with open(\"$_BC_ALERTS_CACHE\") as f:
        alerts = json.load(f)
    matches = [a for a in alerts
               if a.get('dependency',{}).get('package',{}).get('name','') == pkg
               and a.get('state') == 'open']
    cves = []
    cve_details = []
    for a in matches:
        adv = a.get('security_advisory', {})
        cve_id = adv.get('cve_id') or ''
        ghsa_id = adv.get('ghsa_id') or ''
        _id = cve_id or ghsa_id
        if _id and _id not in cves:
            cves.append(_id)
            # Extract CVSS score from cvss object (if present)
            cvss = adv.get('cvss', {})
            cvss_score = cvss.get('score', None)
            severity = adv.get('severity', 'unknown')
            summary = adv.get('summary', '')
            # Build advisory URL
            adv_url = ''
            if ghsa_id:
                adv_url = f'https://github.com/advisories/{ghsa_id}'
            cve_details.append({
                'id': _id,
                'severity': severity,
                'cvss_score': cvss_score,
                'summary': summary[:200] if summary else '',
                'advisory_url': adv_url,
                'ghsa_id': ghsa_id,
                'cve_id': cve_id,
            })
    # Output: line 1 = comma-separated IDs, line 2 = JSON details
    print(','.join(cves))
    print(json.dumps(cve_details))
except Exception:
    print('')
    print('[]')
" 2>/dev/null)
    ALERT_CVES=$(echo "$_CVE_ENRICH" | head -1)
    CVE_DETAILS=$(echo "$_CVE_ENRICH" | tail -1)
    [[ -z "$CVE_DETAILS" || "$CVE_DETAILS" == "" ]] && CVE_DETAILS="[]"
    # Merge: body CVEs + alert CVEs (deduplicated)
    if [[ -n "$ALERT_CVES" ]]; then
      if [[ -n "$CVES" ]]; then
        CVES=$(echo "$CVES,$ALERT_CVES" | tr "," "\n" | sort -u | tr "\n" "," | sed "s/,$//" )
      else
        CVES="$ALERT_CVES"
      fi
    fi
  fi
  [[ -n "$CVES" ]] && echo "  cves: $CVES"

  # ── Collect diff ────────────────────────────────────────────────
  DIFF_FILE="/tmp/pr-${PR_NUM}.diff"
  gh pr diff "$PR_NUM" > "$DIFF_FILE" 2>/dev/null || echo "" > "$DIFF_FILE"
  DIFF_LINES=$(wc -l < "$DIFF_FILE" | tr -d ' ')
  DIFF_TRUNCATED="false"
  if [[ "$DIFF_LINES" -gt "$DIFF_MAX_LINES" ]]; then
    DIFF_TRUNCATED="true"
    head -n "$DIFF_MAX_LINES" "$DIFF_FILE" > "${DIFF_FILE}.tmp"
    mv "${DIFF_FILE}.tmp" "$DIFF_FILE"
  fi
  echo "  diff: $DIFF_LINES lines (truncated=$DIFF_TRUNCATED)"

  # ── Usage scan (shell-level) ────────────────────────────────────
  USAGE_RAW=""
  case "$ECOSYSTEM" in
    npm)   
      # For monorepos, scan from PKG_DIR if available
      if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
        USAGE_RAW=$(cd "$PKG_DIR" && scan_usage_npm "$PKG")
      else
        USAGE_RAW=$(scan_usage_npm "$PKG")
      fi
      ;;
    gomod)
      # CR4-13: scope usage scan to PKG_DIR module to avoid inflating import count
      if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
        USAGE_RAW=$(scan_usage_go "$PKG" "$PKG_DIR")
      else
        USAGE_RAW=$(scan_usage_go "$PKG")
      fi
      ;;
    pip)   USAGE_RAW=$(scan_usage_pip "$PKG") ;;
  esac
  printf '%s' "$USAGE_RAW" > "$BC_SCRATCH_DIR/_bc_usage_raw_${PR_NUM}.txt"
  FILES_IMPORTING=$(format_usage_files "$USAGE_RAW")
  IMPORT_COUNT=$(echo "$FILES_IMPORTING" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
  echo "  imports found: $IMPORT_COUNT files"

  # ── Usage scan for additional packages (multi-package PRs) ──────
  ADDITIONAL_IMPORTS="[]"
  if [[ -n "$ADDITIONAL_PACKAGES" ]]; then
    for EXTRA_PKG in $(echo "$ADDITIONAL_PACKAGES" | tr ',' ' '); do
      EXTRA_PKG=$(echo "$EXTRA_PKG" | xargs)  # trim whitespace
      [[ -z "$EXTRA_PKG" ]] && continue
      EXTRA_RAW=""
      case "$ECOSYSTEM" in
        npm)
          if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
            EXTRA_RAW=$(cd "$PKG_DIR" && scan_usage_npm "$EXTRA_PKG")
          else
            EXTRA_RAW=$(scan_usage_npm "$EXTRA_PKG")
          fi
          ;;
        gomod)
          if [[ "$PKG_DIR" != "/" && -d "$PKG_DIR" ]]; then
            EXTRA_RAW=$(scan_usage_go "$EXTRA_PKG" "$PKG_DIR")
          else
            EXTRA_RAW=$(scan_usage_go "$EXTRA_PKG")
          fi
          ;;
        pip)   EXTRA_RAW=$(scan_usage_pip "$EXTRA_PKG") ;;
      esac
      EXTRA_FILES=$(format_usage_files "$EXTRA_RAW")
      EXTRA_COUNT=$(echo "$EXTRA_FILES" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
      echo "  additional pkg $EXTRA_PKG: $EXTRA_COUNT import sites"
      # Merge into ADDITIONAL_IMPORTS as {"package": "...", "files": [...]}
      # Use temp files to avoid shell double-quote parsing issues on 2nd+ iteration
      # (Finding-5.3) and special chars in package names (Finding-5.6).
      printf '%s' "$ADDITIONAL_IMPORTS" > /tmp/_bc_addl_accum.json
      printf '%s' "$EXTRA_FILES" > /tmp/_bc_extra_files.json
      printf '%s' "$EXTRA_PKG" > /tmp/_bc_extra_pkg.txt
      _addl_result=""
      _addl_result=$(python3 2>/dev/null << 'ADDLEOF'
import json
with open('/tmp/_bc_addl_accum.json') as f: existing = json.loads(f.read() or '[]')
with open('/tmp/_bc_extra_files.json') as f: files = json.loads(f.read() or '[]')
with open('/tmp/_bc_extra_pkg.txt') as f: pkg = f.read().strip()
existing.append({'package': pkg, 'files': files, 'count': len(files)})
print(json.dumps(existing))
ADDLEOF
) && ADDITIONAL_IMPORTS="$_addl_result" || true
    done
  fi

  # ── Cascade impact (shared lib analysis) ────────────────────────
  if [[ "$PKG_DIR" == lib/* ]]; then
    CASCADE_IMPACT=$(check_cascade_impact "$PKG_DIR")
    CASCADE_COUNT=$(echo "$CASCADE_IMPACT" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
    echo "  cascade: $CASCADE_COUNT downstream services affected"
  fi

  # ── NestJS peer group warning ───────────────────────────────────
  if [[ "$PKG" == @nestjs/* ]]; then
    NESTJS_PEER_WARNING=$(python3 -c "
import json
try:
    with open('/tmp/_bc_peer_groups.json') as f: pg = json.load(f)
    with open('$RESULTS_FILE') as f: data = json.load(f)
    nestjs = pg.get('nestjs_group', [])
    pkg = '$PKG'
    if pkg in nestjs:
        others = [f'#{n} ({p["package"]})' for n, p in data.get('prs',{}).items() if p.get('package','').startswith('@nestjs/') and p['package'] != pkg]
        if others: print('NestJS peer group: upgrade ' + pkg + ' with: ' + ', '.join(others[:5]))
except (FileNotFoundError, json.JSONDecodeError, KeyError):
    pass
except Exception as e:
    import sys
    print(f"WARNING: NestJS peer detection error: {e}", file=sys.stderr)
" 2>/dev/null || true)
    [[ -n "$NESTJS_PEER_WARNING" ]] && echo "  $NESTJS_PEER_WARNING"
  fi


  # ── Run TS pipeline CLI (for npm/gomod/pip) ────────────────────
  DETERMINISTIC="{}"
  if [[ "$ECOSYSTEM" == "npm" || "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "pip" ]] && [[ -n "$PKG" && -n "$FROM_VER" && -n "$TO_VER" ]]; then
    echo "  running TS pipeline..."
    CLI_ECO="$ECOSYSTEM"
    PR_BODY_FILE="/tmp/_bc_pr_${PR_NUM}.body"
    printf '%s' "$PR_BODY" > "$PR_BODY_FILE"

    # CLI sends logs to stdout mixed with JSON.  Capture all stdout,
    # then extract only the JSON portion (from first '{' to end).
    CLI_OUTPUT_FILE="/tmp/_bc_cli_${PR_NUM}.raw"
    CLI_JSON_FILE="/tmp/_bc_cli_${PR_NUM}.json"
    CLI_ERR_FILE="/tmp/_bc_cli_${PR_NUM}.err"
    # Pre-fetch comprehensive changelogs/release notes and feed them to the CLI so
    # computeMergeRisk sees declared breaking changes; the CLI also persists
    # deterministic.changelogText + deterministic.changelogSignal.
    CLI_CHANGELOG_ARGS=()
    if [[ "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "npm" ]]; then
      CLI_CHANGELOG_FILE="/tmp/_bc_changelog_${PR_NUM}.txt"
      if [[ "$ECOSYSTEM" == "gomod" ]]; then
        fetch_go_changelog_text "$PKG" "$FROM_VER" "$TO_VER" > "$CLI_CHANGELOG_FILE" 2>/dev/null || true
      else
        fetch_npm_changelog_text "$PKG" "$FROM_VER" "$TO_VER" > "$CLI_CHANGELOG_FILE" 2>/dev/null || true
      fi
      if [[ -s "$CLI_CHANGELOG_FILE" ]]; then
        CLI_CHANGELOG_ARGS=(--changelog-file "$CLI_CHANGELOG_FILE")
        echo "  changelog: fetched $(wc -l < "$CLI_CHANGELOG_FILE" | tr -d ' ') line(s) for CLI verdict"
      fi
    fi
    timeout -k 15 180 node "$CLI_PATH" \
      -p "$PKG" -f "$FROM_VER" -t "$TO_VER" \
      -r "$REPO_ROOT" -e "$CLI_ECO" -d "$DEP_TYPE" \
      --pr-body-file "$PR_BODY_FILE" \
      ${CLI_CHANGELOG_ARGS[@]+"${CLI_CHANGELOG_ARGS[@]}"} \
      --json > "$CLI_OUTPUT_FILE" 2>"$CLI_ERR_FILE" || true

    # Extract JSON: find the first line starting with '{' and take everything from there
    sed -n '/^{/,$p' "$CLI_OUTPUT_FILE" > "$CLI_JSON_FILE"

    if python3 -c "import json; json.load(open('$CLI_JSON_FILE'))" 2>/dev/null; then
      DETERMINISTIC=$(python3 -c "
import json, sys
with open('$CLI_JSON_FILE') as f:
    data = json.load(f)
result = {
  'api_changes': len(data.get('apiChanges', [])),
  'api_changes_detail': data.get('apiChanges', []),
  'usages': data.get('usages', []),
  'verification': {
    'tier': data.get('verification', {}).get('tier', 0),
    'verified': data.get('verification', {}).get('verified', False),
    'compatible': data.get('verification', {}).get('compatible', None),
    'symbol_results': data.get('verification', {}).get('symbolResults', {})
  },
  'score': data.get('score', {}).get('total', 0),
  'classification': data.get('classification', 'INCONCLUSIVE'),
  'merge_risk': data.get('mergeRisk', {}),
  'confidence': data.get('confidence', 'UNVERIFIED'),
  'adapter': data.get('adapterUsed', 'unknown'),
  'api_diff_tool': data.get('apiDiffTool', None),
  'security': data.get('securityUpdate', None),
  'changelogText': data.get('changelogText', ''),
  'changelogSignal': data.get('changelogSignal', None)
}
print(json.dumps(result))
" 2>/dev/null || echo "{}")
      echo "  pipeline: classification=$(echo "$DETERMINISTIC" | python3 -c "import json,sys; print(json.load(sys.stdin).get('classification','?'))" 2>/dev/null || echo "?")"
      echo "  pipeline: merge_risk=$(echo "$DETERMINISTIC" | python3 -c "import json,sys; d=json.load(sys.stdin); mr=d.get('merge_risk') or {}; cs=d.get('changelogSignal') or {}; print((mr.get('tag') or '?')+' changelog_status='+str((cs or {}).get('status'))+' bullets='+str(len((cs or {}).get('bullets',[]))))" 2>/dev/null || echo "?")"
    else
      echo "  pipeline: failed to parse CLI output"
      echo "  pipeline-stderr: $(tail -3 "$CLI_ERR_FILE" 2>/dev/null | tr '\n' ' ')"
      DETERMINISTIC="{}"
    fi
    cat "$CLI_OUTPUT_FILE" "$CLI_ERR_FILE" 2>/dev/null | tail -c 4000 > "$BC_SCRATCH_DIR/_bc_cli_output_${PR_NUM}.txt" || true
    rm -f "$CLI_OUTPUT_FILE" "$CLI_JSON_FILE" "$CLI_ERR_FILE"
  else
    echo "  pipeline: skipped ($ECOSYSTEM)"
  fi

  # ── npm semantic API/type-surface diff (mirrors Go apidiff) ───────────────────
  # The bundled TS CLI does not compare the dependency's exported type surface, so
  # for npm we run a standalone TypeScript-compiler diff of the package's .d.ts at
  # the from/to versions (downloaded from the public registry). Results are merged
  # into the deterministic block as the api_diff signal. Fails safe: compatible=null
  # (UNAVAILABLE) whenever either version ships no types or extraction fails — never
  # a false "compatible". A clean compatible result only clears patch/minor bumps;
  # the evidence contract still gates major bumps on semver + changelog.
  if [[ "$ECOSYSTEM" == "npm" && -n "$PKG" && -n "$FROM_VER" && -n "$TO_VER" ]]; then
    APIDIFF_SCRIPT="$REPO_ROOT/.github/scripts/npm_apidiff.mjs"
    if [[ -f "$APIDIFF_SCRIPT" ]] && command -v node >/dev/null 2>&1; then
      echo "  npm api-diff: $PKG $FROM_VER -> $TO_VER ..."
      APIDIFF_JSON=$(timeout -k 15 240 node "$APIDIFF_SCRIPT" "$PKG" "$FROM_VER" "$TO_VER" 2>/dev/null | tail -1 || echo "")
      if [[ -n "$APIDIFF_JSON" ]]; then
        DETERMINISTIC=$(DET_IN="$DETERMINISTIC" AD_IN="$APIDIFF_JSON" python3 -c "
import json, os
_din = os.environ.get('DET_IN') or '{}'
try:
    det = json.loads(_din)
except Exception:
    det = {}
if not isinstance(det, dict):
    det = {}
try:
    ad = json.loads(os.environ['AD_IN'])
except Exception:
    ad = {}
compatible = ad.get('compatible', None)
removed = ad.get('removed', []) or []
changed = ad.get('changed', []) or []
# Structured detail so policy_lowering._has_breaking_api_change classifies removals/
# signature changes as hard breaks (changeType in its hard set), while a clean diff
# (compatible) carries an empty detail list.
detail = [{'name': n, 'changeType': 'removed'} for n in removed]
detail += [
    {'name': (c.get('name') if isinstance(c, dict) else c), 'changeType': 'signature_changed'}
    for c in changed
]
det['api_changes'] = int(ad.get('apiChanges', 0) or 0)
det['api_changes_detail'] = detail
# Mark as a SEMANTIC, module-mode tool: ts-apidiff compares exported type
# signatures via the TypeScript compiler (the npm analogue of Go's apidiff), so a
# zero-change result is HIGH-confidence proof of API backward-compatibility.
# UNAVAILABLE (compatible is None) must NOT look like a clean module diff — set
# api_changes None and omit module mode so the api_diff signal is UNAVAILABLE
# (never a false "compatible"), e.g. a major bump whose old version shipped no types.
if compatible is None:
    det['api_changes'] = None
    det['api_changes_detail'] = []
    det['api_diff_tool'] = {'name': 'ts-apidiff', 'status': 'unavailable'}
else:
    det['api_diff_tool'] = {'name': 'ts-apidiff', 'mode': 'module', 'status': 'semantic'}
ver = det.get('verification') or {}
if not isinstance(ver, dict):
    ver = {}
ver['compatible'] = compatible
ver['api_diff_unavailable_reason'] = ad.get('reason', '') if compatible is None else ''
det['verification'] = ver
print(json.dumps(det))
" 2>/dev/null || echo "$DETERMINISTIC")
        echo "  npm api-diff: compatible=$(echo "$APIDIFF_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('compatible'))" 2>/dev/null || echo '?') api_changes=$(echo "$APIDIFF_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('apiChanges'))" 2>/dev/null || echo '?')"
      else
        echo "  npm api-diff: no output (unavailable)"
      fi
    fi
  fi

  # ── Build check on PR branch ───────────────────────────────────
  BUILD_EXIT="-1"
  BUILD_OUTPUT=""
  BUILD_VERDICT="skip"
  PR_WORKTREE="${WORKTREE_BASE}-${PR_NUM}"
  AUDIT_CRITICAL=0
  AUDIT_HIGH=0
  # Initialize PR-level variables BEFORE the worktree check — if worktree creation
  # fails (BUILD_VERDICT="error"), these are used in the Python heredoc at line ~2626.
  # Without initialization, set -u would abort the script (Finding-2.12).
  PR_TSC_EXIT=-1
  PR_INSTALL_EXIT=0
  MAIN_GO_TEST_EXIT_PR=-1
  MAIN_NPM_TEST_EXIT_PR=-1
  EVIDENCE_DEP_COMMAND=""
  EVIDENCE_BUILD_COMMAND=""
  EVIDENCE_TEST_COMMAND=""
  EVIDENCE_SMOKE_COMMAND=""
  EVIDENCE_SMOKE_OUTPUT=""
  EVIDENCE_SMOKE_EXIT="null"

  # Re-check MERGEABLE_STATUS (conflict verdict was set at line 1526 but BUILD_VERDICT
  # was just reset to "skip" — so we must check the source of truth, not the overwritten var)
  if [[ "$MERGEABLE_STATUS" == "CONFLICTING" ]]; then
    BUILD_VERDICT="conflict"
    echo "  Skipping build — PR has merge conflicts"
  elif [[ "$ECOSYSTEM" == "npm" || "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "pip" ]]; then
    rm -rf "$PR_WORKTREE" 2>/dev/null || true
    git worktree remove "$PR_WORKTREE" --force 2>/dev/null || true
    git worktree prune 2>/dev/null || true
    git worktree add "$PR_WORKTREE" "origin/$PR_BRANCH" --quiet 2>"$BC_SCRATCH_DIR/_bc_wt_err_${PR_NUM}.txt" || {
      echo "  worktree: failed to create for $PR_BRANCH"
      echo "  worktree-err: $(tail -2 "$BC_SCRATCH_DIR/_bc_wt_err_${PR_NUM}.txt" 2>/dev/null | tr '\n' ' ')"
      BUILD_VERDICT="error"
    }

    if [[ -d "$PR_WORKTREE" ]]; then
      PR_INSTALL_EXIT=0
      PR_TSC_EXIT=-1
      case "$ECOSYSTEM" in
        npm)
          # For monorepos, build in the specific service/lib directory
          BUILD_DIR="$PR_WORKTREE"
          [[ "$PKG_DIR" != "/" && -d "$PR_WORKTREE/$PKG_DIR" ]] && BUILD_DIR="$PR_WORKTREE/$PKG_DIR"
          echo "  build: npm ci + tsc in ${BUILD_DIR#$PR_WORKTREE/}..."
          # Set up private registry auth if configured
          setup_private_registries "$BUILD_DIR"
          EVIDENCE_DEP_COMMAND="npm ci --ignore-scripts"
          BUILD_OUTPUT=$(cd "$BUILD_DIR" && retry_cmd 3 5 timeout $TIMEOUT npm ci --ignore-scripts 2>&1)
          PR_INSTALL_EXIT=$?
          INSTALL_METHOD="ci"
          if [[ "$PR_INSTALL_EXIT" -ne 0 ]]; then
            ERROR_CLASS=$(classify_npm_error "$BUILD_OUTPUT")
            echo "  npm ci failed ($ERROR_CLASS)"
            if [[ "$ERROR_CLASS" == "lockfile_desync" ]]; then
              echo "  trying npm install fallback..."
              rewrite_private_deps_to_local "$BUILD_DIR" "$PR_WORKTREE"
              FALLBACK_OUT=$(cd "$BUILD_DIR" && timeout $TIMEOUT npm install --ignore-scripts --legacy-peer-deps 2>&1)
              _FALLBACK_RC=$?
              BUILD_OUTPUT="$BUILD_OUTPUT
--- npm install fallback ---
$FALLBACK_OUT"
              EVIDENCE_DEP_COMMAND="npm ci --ignore-scripts; npm install --ignore-scripts --legacy-peer-deps"
              if [[ "$_FALLBACK_RC" -eq 0 ]]; then
                echo "  npm install fallback: SUCCESS"
                PR_INSTALL_EXIT=0
                INSTALL_METHOD="install_fallback"
              fi
            elif [[ "$ERROR_CLASS" == "infra_error" ]]; then
              # ── Workspace-local fallback ──
              # If the infra_error is from a private registry for packages that
              # exist locally in the monorepo (e.g., @org/auth-lib → lib/auth-lib/),
              # rewrite those deps to file: links so npm resolves them locally.
              echo "  INFRA_ERROR: trying workspace-local fallback..."
              rewrite_private_deps_to_local "$BUILD_DIR" "$PR_WORKTREE"
              FALLBACK_OUT=$(cd "$BUILD_DIR" && timeout $TIMEOUT npm install --ignore-scripts --legacy-peer-deps 2>&1)
              _FALLBACK_RC=$?
              BUILD_OUTPUT="$BUILD_OUTPUT
--- npm install fallback ---
$FALLBACK_OUT"
              EVIDENCE_DEP_COMMAND="npm ci --ignore-scripts; npm install --ignore-scripts --legacy-peer-deps"
              if [[ "$_FALLBACK_RC" -eq 0 ]]; then
                echo "  workspace-local fallback: SUCCESS"
                PR_INSTALL_EXIT=0
                INSTALL_METHOD="local_fallback"
              else
                INSTALL_METHOD="infra_error"
                echo "  INFRA_ERROR: registry auth failure (workspace fallback also failed)"
              fi
            fi
          fi
          BUILD_EXIT=$PR_INSTALL_EXIT
          # Track whether the package was actually installed (for confidence calibration)
          [[ "$PR_INSTALL_EXIT" -eq 0 ]] && INSTALL_OK="true"

          # npm audit — run after successful install to get security data
          AUDIT_JSON=""
          AUDIT_CRITICAL=0
          AUDIT_HIGH=0
          if [[ "$PR_INSTALL_EXIT" -eq 0 ]]; then
            AUDIT_JSON=$(cd "$BUILD_DIR" && timeout 30 npm audit --json --production 2>/dev/null || echo '{}')
            AUDIT_CRITICAL=$(echo "$AUDIT_JSON" | jq -r '.metadata.vulnerabilities.critical // 0' 2>/dev/null | awk '{s+=$1} END{print s+0}')
            AUDIT_HIGH=$(echo "$AUDIT_JSON" | jq -r '.metadata.vulnerabilities.high // 0' 2>/dev/null | awk '{s+=$1} END{print s+0}')
            [[ "$AUDIT_CRITICAL" -gt 0 || "$AUDIT_HIGH" -gt 0 ]] && echo "  npm audit: ${AUDIT_CRITICAL} critical, ${AUDIT_HIGH} high"
          fi

          if [[ "$PR_INSTALL_EXIT" -eq 0 && -f "$BUILD_DIR/tsconfig.json" ]]; then
            build_npm_workspace_libs "$PR_WORKTREE" "$TIMEOUT"
            EVIDENCE_BUILD_COMMAND="npx tsc --noEmit"
            TSC_OUT=$(cd "$BUILD_DIR" && timeout $TIMEOUT npx tsc --noEmit 2>&1)
            PR_TSC_EXIT=$?
            BUILD_EXIT=$PR_TSC_EXIT
            BUILD_OUTPUT="$BUILD_OUTPUT
--- tsc ---
$TSC_OUT"
          fi
          ;;
        gomod)
          # Sanitize Go env before each PR build (A3-3/CR3-1/CR4-2/A4-2)
          # Clear both shell env AND Go's persistent env file (root cause of V7 failure)
          unset GOSUMDB 2>/dev/null || true
          unset GONOSUMCHECK 2>/dev/null || true
          go env -u GOSUMDB 2>/dev/null || true
          go env -u GONOSUMCHECK 2>/dev/null || true
          go env -u GONOSUMDB 2>/dev/null || true
          if [[ -n "${GOPRIVATE:-}" ]]; then
            export GONOSUMDB="${GOPRIVATE}"
            go env -w GONOSUMDB="${GOPRIVATE}" 2>/dev/null || true
          fi
          if [[ "$GO_AVAILABLE" == "false" ]]; then
            echo "  build: SKIP — Go is not installed on this runner"
            BUILD_OUTPUT="SKIPPED: Go not available (go version returned error or Go not found)"
            BUILD_EXIT=0
            INSTALL_OK="true"
          elif [[ -f "$PR_WORKTREE/go.work" ]]; then
            echo "  build: go.work workspace — sync + targeted build..."
            # Supply chain integrity ensured by go.sum + default GOSUMDB (sum.golang.org).
            # Do NOT set GOSUMDB=off or GOPROXY=direct — see baseline comments for rationale.
            # Separate sync (dependency resolution) from build so INSTALL_OK tracks deps correctly.
            _GO_SYNC_OUT=""
            _GO_SYNC_EXIT=0
            EVIDENCE_DEP_COMMAND="go work sync"
            _GO_SYNC_OUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 go work sync 2>&1) || _GO_SYNC_EXIT=$?
            if [[ "$_GO_SYNC_EXIT" -eq 0 ]]; then
              INSTALL_OK="true"
              EVIDENCE_BUILD_COMMAND="go_targeted_build (timeout ${GO_TIMEOUT} go build -o /dev/null <affected packages>)"
              BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
                _BUILD_RC=0
                go_targeted_build "$FILES_IMPORTING" || _BUILD_RC=$?
                if [[ $_BUILD_RC -eq 0 ]]; then go_targeted_vet "$FILES_IMPORTING"; fi
                exit $_BUILD_RC
              } 2>&1)
              BUILD_EXIT=$?
              # Cache corruption retry: if build failed due to stale cache, clean and retry
              if [[ "$BUILD_EXIT" -ne 0 ]] && [[ "$(classify_go_error "$BUILD_OUTPUT")" == "cache_corruption" ]]; then
                echo "  ⚠ Go build cache corruption detected — cleaning cache and retrying..."
                (cd "$PR_WORKTREE" && go clean -cache 2>/dev/null || true)
                BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
                  _BUILD_RC=0
                  go_targeted_build "$FILES_IMPORTING" || _BUILD_RC=$?
                  if [[ $_BUILD_RC -eq 0 ]]; then go_targeted_vet "$FILES_IMPORTING"; fi
                  exit $_BUILD_RC
                } 2>&1)
                BUILD_EXIT=$?
                echo "  cache-clean retry: exit=$BUILD_EXIT"
              fi
            else
              # Sync failed — dependency resolution failed
              echo "  go work sync failed after 3 retries"
              BUILD_OUTPUT="$_GO_SYNC_OUT"
              BUILD_EXIT=$_GO_SYNC_EXIT
            fi
          else
            echo "  build: go mod tidy + build + vet..."
            # Supply chain integrity ensured by go.sum + default GOSUMDB (sum.golang.org).
            # Do NOT set GOSUMDB=off or GOPROXY=direct — see baseline comments for rationale.
            # Separate tidy from build so we can track dependency resolution independently.
            # go mod tidy succeeding = dependency resolution passed (INSTALL_OK=true).
            # This decouples L0 (install failed) from L2 (build failed but deps resolved).
            #
            # Multi-module fix (A2-4): run go mod tidy in the correct module directory
            # based on PKG_DIR, not always in the worktree root. In a multi-module repo,
            # the Dependabot PR modifies a sub-module's go.mod (e.g., cicd/go.mod), so
            # tidy must run there to resolve the correct dependencies.
            # Multi-module fix (A3-7): run go mod tidy in ALL modules that have
            # files importing the changed package, not just the PKG_DIR module.
            # go_targeted_build builds across all affected modules, so each needs
            # its go.sum updated by tidy. Otherwise modules that didn't get tidy'd
            # will fail on checksum verification during go build.
            _GO_TIDY_EXIT=0
            _GO_TIDY_OUT=""
            EVIDENCE_DEP_COMMAND="go mod tidy"
            if [[ "$_GO_MULTI_MODULE" == "true" ]]; then
              # Find all go.mod files and tidy each module that has importing files
              _TIDY_MODULES=$(_BC_IMPORT_JSON="$FILES_IMPORTING" _BC_PKG_DIR="$PKG_DIR" python3 -c "
import json, os, sys
try:
    files = json.loads(os.environ.get('_BC_IMPORT_JSON', '[]'))
except:
    files = []
# Find all go.mod files
mod_roots = set()
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('vendor', '.git', 'node_modules')]
    if 'go.mod' in fnames:
        mod_roots.add(os.path.normpath(root))
# Always include PKG_DIR module
pkg_dir = os.environ.get('_BC_PKG_DIR', '/')
if pkg_dir != '/' and os.path.isfile(os.path.join(pkg_dir, 'go.mod')):
    mod_roots.add(os.path.normpath(pkg_dir))
# Find which modules own importing files
affected = set()
for f in files:
    path = f.split(':')[0]
    d = os.path.dirname(os.path.normpath(path)) or '.'
    for mr in sorted(mod_roots, key=lambda x: -x.count('/')):
        if d == mr or d.startswith(mr + '/'):
            affected.add(mr)
            break
    else:
        if '.' in mod_roots:
            affected.add('.')
# If no importing files, at least tidy the PKG_DIR module
if not affected:
    if pkg_dir != '/' and os.path.isfile(os.path.join(pkg_dir, 'go.mod')):
        affected.add(os.path.normpath(pkg_dir))
    elif '.' in mod_roots:
        affected.add('.')
for m in sorted(affected):
    print(m)
" 2>/dev/null)
              # _BC_PKG_DIR is now passed inline to the python3 invocation above (CR4-1/A4-6)
              # Run tidy in each affected module
              if [[ -n "$_TIDY_MODULES" ]]; then
                # Accumulate ALL tidy output across modules (CR4-3/CR4-4).
                # Previous code only kept the last failure's output, losing earlier errors.
                _GO_TIDY_ALL_OUT=""
                while IFS= read -r _tidy_mod; do
                  [[ -z "$_tidy_mod" ]] && continue
                  _tidy_dir="$PR_WORKTREE/$_tidy_mod"
                  [[ "$_tidy_mod" == "." ]] && _tidy_dir="$PR_WORKTREE"
                  if [[ -f "$_tidy_dir/go.mod" ]]; then
                    echo "  multi-module: go mod tidy in $_tidy_mod"
                    _mod_tidy_out=""
                    _mod_tidy_rc=0
                    _mod_tidy_out=$(cd "$_tidy_dir" && retry_cmd 3 5 timeout -k 15 120 go mod tidy 2>&1) || _mod_tidy_rc=$?
                    if [[ "$_mod_tidy_rc" -ne 0 ]]; then
                      _GO_TIDY_EXIT=$_mod_tidy_rc
                      echo "  ⚠ go mod tidy failed in $_tidy_mod (exit=$_mod_tidy_rc)"
                    fi
                    _GO_TIDY_ALL_OUT="${_GO_TIDY_ALL_OUT}
--- go mod tidy: ${_tidy_mod} (exit=${_mod_tidy_rc}) ---
${_mod_tidy_out}"
                  fi
                done <<< "$_TIDY_MODULES"
                _GO_TIDY_OUT="$_GO_TIDY_ALL_OUT"
              else
                # Fallback: tidy in PKG_DIR or root
                _GO_TIDY_DIR="$PR_WORKTREE"
                if [[ "$PKG_DIR" != "/" && -f "$PR_WORKTREE/$PKG_DIR/go.mod" ]]; then
                  _GO_TIDY_DIR="$PR_WORKTREE/$PKG_DIR"
                  echo "  multi-module: running go mod tidy in $PKG_DIR (not root)"
                fi
                _GO_TIDY_OUT=$(cd "$_GO_TIDY_DIR" && retry_cmd 3 5 timeout -k 15 120 go mod tidy 2>&1) || _GO_TIDY_EXIT=$?
              fi
            else
              # Single-module: tidy in worktree root
              _GO_TIDY_OUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 timeout -k 15 120 go mod tidy 2>&1) || _GO_TIDY_EXIT=$?
            fi
            if [[ "$_GO_TIDY_EXIT" -eq 0 ]]; then
              INSTALL_OK="true"
              EVIDENCE_BUILD_COMMAND="go_targeted_build (timeout ${GO_TIMEOUT} go build -o /dev/null <affected packages>)"
              BUILD_OUTPUT=$(cd "$PR_WORKTREE" && go_targeted_build "$FILES_IMPORTING" 2>&1)
              BUILD_EXIT=$?
              # Cache corruption retry: if build failed due to stale cache, clean and retry
              if [[ "$BUILD_EXIT" -ne 0 ]] && [[ "$(classify_go_error "$BUILD_OUTPUT")" == "cache_corruption" ]]; then
                echo "  ⚠ Go build cache corruption detected — cleaning cache and retrying..."
                (cd "$PR_WORKTREE" && go clean -cache 2>/dev/null || true)
                BUILD_OUTPUT=$(cd "$PR_WORKTREE" && go_targeted_build "$FILES_IMPORTING" 2>&1)
                BUILD_EXIT=$?
                echo "  cache-clean retry: exit=$BUILD_EXIT"
              fi
            else
              # Tidy failed — dependency resolution failed
              BUILD_OUTPUT="$_GO_TIDY_OUT"
              BUILD_EXIT=$_GO_TIDY_EXIT
            fi
            # Run go vet if build passed
            GO_VET_OUT=""
            if [[ "$BUILD_EXIT" -eq 0 ]]; then
              GO_VET_OUT=$(cd "$PR_WORKTREE" && go_targeted_vet "$FILES_IMPORTING" 2>&1) || true
              if [[ -n "$GO_VET_OUT" ]]; then
                echo "  go vet warnings found"
                BUILD_OUTPUT="$BUILD_OUTPUT
--- go vet ---
$GO_VET_OUT"
              fi
            fi
            # Security vulnerability check
            GO_VULN_OUT=$(go_check_vulnerabilities "$PR_WORKTREE" 2>&1) || true
            # Extract ###VULN_STATUS=... sentinel, strip from displayed output
            VULN_STATUS=$(echo "$GO_VULN_OUT" | grep -oE '^###VULN_STATUS=[a-z_]+' | tail -1 | cut -d= -f2)
            [[ -z "$VULN_STATUS" ]] && VULN_STATUS="unknown"
            GO_VULN_OUT_DISPLAY=$(echo "$GO_VULN_OUT" | grep -v '^###VULN_STATUS=')
            # Extract all findings in PR worktree
            _PR_VULNS=$(echo "$GO_VULN_OUT_DISPLAY" | grep -oE 'GO-[0-9]{4}-[0-9]+' | sort -u)
            # Diff against main baseline — only NEW findings count as "introduced by this PR"
            _MAIN_VULNS=""
            [[ -f "/tmp/_bc_main_vuln_findings.txt" ]] && _MAIN_VULNS=$(cat /tmp/_bc_main_vuln_findings.txt)
            _NEW_VULNS=$(comm -23 <(echo "$_PR_VULNS" | sort -u) <(echo "$_MAIN_VULNS" | sort -u) | grep -v '^$' || true)
            _NEW_VULN_COUNT=$(echo -n "$_NEW_VULNS" | grep -c . || true)
            _PRE_VULN_COUNT=$(echo -n "$_PR_VULNS" | grep -c . || true)
            _PRE_VULN_COUNT=$((_PRE_VULN_COUNT - _NEW_VULN_COUNT))
            [[ "$_PRE_VULN_COUNT" -lt 0 ]] && _PRE_VULN_COUNT=0
            # Refine status: if PR had vulns_found but ALL were pre-existing on main,
            # treat as "ok_preexisting" — the PR itself introduces no new vulns.
            if [[ "$VULN_STATUS" == "vulns_found" && "$_NEW_VULN_COUNT" -eq 0 ]]; then
              VULN_STATUS="ok_preexisting"
              echo "  [security] PR has $_PRE_VULN_COUNT pre-existing vuln(s) also present on main — no new vulns introduced"
            elif [[ "$VULN_STATUS" == "vulns_found" && "$_NEW_VULN_COUNT" -gt 0 ]]; then
              echo "  [security] PR introduces $_NEW_VULN_COUNT NEW vuln(s) (plus $_PRE_VULN_COUNT pre-existing on main)"
            fi
            echo "$VULN_STATUS" > "/tmp/_bc_vuln_status_${PR_NUM}.txt"
            # Persist new findings (one per line) and pre-existing count
            echo "$_NEW_VULNS" > "/tmp/_bc_vuln_new_findings_${PR_NUM}.txt"
            echo "$_PRE_VULN_COUNT" > "/tmp/_bc_vuln_preexisting_count_${PR_NUM}.txt"
            # Extract first NEW vuln finding for header badge (if any)
            _VULN_FINDING=$(echo "$_NEW_VULNS" | head -1)
            [[ -n "$_VULN_FINDING" ]] && echo "$_VULN_FINDING" > "/tmp/_bc_vuln_finding_${PR_NUM}.txt" || printf '' > "/tmp/_bc_vuln_finding_${PR_NUM}.txt"
            # V9.8 iter6 (C): keep govulncheck output in its OWN variable.
            # Do NOT append to BUILD_OUTPUT — that caused vuln text to be misclassified
            # as compile errors (iter5c finding F4/P0-1). Emit dedicated vuln scan file.
            VULN_OUTPUT="$GO_VULN_OUT_DISPLAY"
            if [[ -n "$VULN_OUTPUT" ]]; then
              printf '%s' "$VULN_OUTPUT" > "/tmp/_bc_vuln_output_${PR_NUM}.txt"
            else
              printf '' > "/tmp/_bc_vuln_output_${PR_NUM}.txt"
            fi
          fi
          if [[ "$ECOSYSTEM" == "gomod" ]]; then
            if [[ -n "${_GO_TIDY_OUT:-}" ]]; then
              if [[ "${_GO_MULTI_MODULE:-false}" == "true" ]]; then
                printf 'go mod tidy (affected modules) for %s %s→%s' "$PKG" "$FROM_VER" "$TO_VER" > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
              else
                printf 'go mod tidy for %s %s→%s' "$PKG" "$FROM_VER" "$TO_VER" > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
              fi
              printf '%s' "$_GO_TIDY_OUT" > "$BC_SCRATCH_DIR/_bc_go_resolution_output_${PR_NUM}.txt"
              printf '%s' "${_GO_TIDY_EXIT:-null}" > "$BC_SCRATCH_DIR/_bc_go_resolution_exit_${PR_NUM}.txt"
            elif [[ -n "${_GO_SYNC_OUT:-}" ]]; then
              printf 'go work sync for %s %s→%s' "$PKG" "$FROM_VER" "$TO_VER" > "$BC_SCRATCH_DIR/_bc_go_resolution_command_${PR_NUM}.txt"
              printf '%s' "$_GO_SYNC_OUT" > "$BC_SCRATCH_DIR/_bc_go_resolution_output_${PR_NUM}.txt"
              printf '%s' "${_GO_SYNC_EXIT:-null}" > "$BC_SCRATCH_DIR/_bc_go_resolution_exit_${PR_NUM}.txt"
            fi
            _GO_MODSUM_FILES=$(cd "$PR_WORKTREE" && git --no-pager diff --name-only origin/main -- 2>/dev/null | grep -E '(^|/)go\.(mod|sum)$' || true)
            if [[ -n "$_GO_MODSUM_FILES" ]]; then
              _go_modsum_args=()
              while IFS= read -r _go_modsum_file; do
                [[ -n "$_go_modsum_file" ]] && _go_modsum_args+=("$_go_modsum_file")
              done <<< "$_GO_MODSUM_FILES"
              if [[ ${#_go_modsum_args[@]} -gt 0 ]]; then
                (cd "$PR_WORKTREE" && git --no-pager diff --unified=0 origin/main -- "${_go_modsum_args[@]}" 2>/dev/null || true) > "$BC_SCRATCH_DIR/_bc_go_modsum_diff_${PR_NUM}.txt"
              fi
            fi
          fi
          # Classify Go build error for JSON output (pass exit code for timeout detection — A2-2)
          # CR5-2: Include tidy output in classification input so infra errors (GOSUMDB,
          # network, proxy) that appeared during tidy are not lost when tidy succeeds
          # but build fails. Without this, classify_go_error only sees build output and
          # may miss the infra_error pattern, defaulting to build_fail.
          if [[ "$BUILD_EXIT" -ne 0 && "$ECOSYSTEM" == "gomod" ]]; then
            _CLASSIFY_INPUT="${_GO_TIDY_OUT:-}
${BUILD_OUTPUT}"
            ERROR_CLASS=$(classify_go_error "$_CLASSIFY_INPUT" "$BUILD_EXIT")
          fi
          ;;
        pip)
          echo "  build: pip install (isolated venv) + import check..."
          _PY_VENV_PR=$(mktemp -d /tmp/bc_venv_pr_XXXXXX)
          if python3 -m venv "$_PY_VENV_PR" 2>/dev/null; then
            _PY_PIP_PR="$_PY_VENV_PR/bin/pip"
            _PY_PYTHON_PR="$_PY_VENV_PR/bin/python"
          else
            rm -rf "$_PY_VENV_PR" 2>/dev/null || true
            _PY_VENV_PR=""
            command -v pip3 &>/dev/null && _PY_PIP_PR="pip3" || _PY_PIP_PR="pip"
            _PY_PYTHON_PR="python3"
          fi
          if [[ -f "$PR_WORKTREE/requirements.txt" ]]; then
            EVIDENCE_DEP_COMMAND="pip install -r requirements.txt --quiet"
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_PR" install -r requirements.txt --quiet 2>&1)
          elif [[ -f "$PR_WORKTREE/pyproject.toml" ]]; then
            EVIDENCE_DEP_COMMAND="pip install -e . --quiet"
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_PR" install -e . --quiet 2>&1)
          elif [[ -f "$PR_WORKTREE/poetry.lock" ]]; then
            # Chain with && so poetry install only runs if pip install poetry succeeds (Finding-2.8)
            EVIDENCE_DEP_COMMAND="pip install poetry --quiet && python -m poetry install --quiet"
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
              retry_cmd 3 5 "$_PY_PIP_PR" install poetry --quiet 2>&1 && \
              retry_cmd 3 5 "$_PY_PYTHON_PR" -m poetry install --quiet 2>&1
            })
          else
            BUILD_OUTPUT="No requirements.txt, pyproject.toml, or poetry.lock found"
          fi
          BUILD_EXIT=$?
          [[ "$BUILD_EXIT" -eq 0 ]] && INSTALL_OK="true"
          if [[ "$BUILD_EXIT" -eq 0 && -n "$PKG" ]]; then
            IMPORT_NAME=$(map_import_name "$PKG")
            EVIDENCE_BUILD_COMMAND="python -c 'import ${IMPORT_NAME}'"
            IMPORT_OUT=$(timeout 30 "$_PY_PYTHON_PR" -c "import $IMPORT_NAME" 2>&1)
            IMPORT_EXIT=$?
            if [[ "$IMPORT_EXIT" -ne 0 ]]; then
              BUILD_EXIT=$IMPORT_EXIT
              BUILD_OUTPUT="$BUILD_OUTPUT
--- import check ---
$IMPORT_OUT"
            fi
          fi
          [[ -n "$_PY_VENV_PR" ]] && rm -rf "$_PY_VENV_PR" 2>/dev/null || true
          ;;
      esac

      # Determine verdict by comparing to main baseline
      # For npm: compare install-vs-install, tsc-vs-tsc separately
      # Also detect NEW errors: if PR tsc fails AND main tsc fails, check if PR
      # introduced additional error lines not present on main.
      NEW_ERRORS=""
      if [[ "$ECOSYSTEM" == "npm" ]]; then
        # For monorepos, build lazy baseline for this directory if not done yet
        rel_pkg_dir="${PKG_DIR}"
        [[ "$rel_pkg_dir" == "/" ]] && rel_pkg_dir="."
        build_npm_baseline_for_dir "$rel_pkg_dir"
        dir_key="${rel_pkg_dir//\//_}"
        main_dir_install_exit=""
        main_dir_tsc_exit=""
        # Sanitize exit codes to pure integers — trailing whitespace or corrupt
        # file content would cause bash -gt / -ne to fail under set -u (Finding-2.6)
        main_dir_install_exit=$(cat "/tmp/_bc_main_npm_install_${dir_key}.txt" 2>/dev/null | tr -dc '0-9-' || echo "-1")
        [[ -z "$main_dir_install_exit" ]] && main_dir_install_exit="-1"
        main_dir_tsc_exit=$(cat "/tmp/_bc_main_npm_tsc_${dir_key}.txt" 2>/dev/null | tr -dc '0-9-' || echo "-1")
        [[ -z "$main_dir_tsc_exit" ]] && main_dir_tsc_exit="-1"
        main_npm_output=$(cat "/tmp/_bc_main_npm_out_${dir_key}.txt" 2>/dev/null || echo "")
        # Read tsc-specific output for error comparison (Finding-2.2).
        # _bc_main_npm_out_ contains install output; _bc_main_npm_tscout_ contains tsc output.
        # Using install output for tsc error grep yields empty results — all PR errors appear "new".
        main_npm_tsc_output=$(cat "/tmp/_bc_main_npm_tscout_${dir_key}.txt" 2>/dev/null || echo "")
        main_npm_tsc_exit=$main_dir_tsc_exit
        main_npm_install_exit=$main_dir_install_exit
        main_npm_exit=$main_dir_install_exit
        [[ "$main_dir_tsc_exit" != "-1" ]] && main_npm_exit=$main_dir_tsc_exit

        if [[ "$PR_INSTALL_EXIT" -ne 0 ]]; then
          if [[ "$main_dir_install_exit" -ne 0 && "$main_dir_install_exit" -ne -1 ]]; then
            BUILD_VERDICT="pre_existing"
          else
            BUILD_VERDICT="fail"
          fi
        elif [[ "$PR_TSC_EXIT" -gt 0 ]]; then
          if [[ "$main_dir_tsc_exit" -gt 0 ]]; then
            # Both fail — but does PR have NEW errors?
            # Extract error lines (TS format: file(line,col): error TSXXXX: message)
            # Normalize: strip worktree paths from error messages so
            # '/tmp/worktree-main/node_modules/...' and '/tmp/worktree-N/node_modules/...'
            # compare as identical (avoids false pre_existing_plus_new).
            MAIN_ERRORS_FILE="/tmp/_bc_main_tsc_errors.txt"
            PR_ERRORS_FILE="/tmp/_bc_pr_tsc_errors_${PR_NUM}.txt"
            echo "$main_npm_tsc_output" | grep -oE 'error TS[0-9]+:.*' | sed "s|${WORKTREE_BASE}[^/]*/|./|g" | sort -u > "$MAIN_ERRORS_FILE" 2>/dev/null || true
            echo "$BUILD_OUTPUT" | grep -oE 'error TS[0-9]+:.*' | sed "s|${WORKTREE_BASE}[^/]*/|./|g" | sort -u > "$PR_ERRORS_FILE" 2>/dev/null || true
            NEW_ERRORS=$(comm -23 "$PR_ERRORS_FILE" "$MAIN_ERRORS_FILE" 2>/dev/null | head -10)
            rm -f "$MAIN_ERRORS_FILE" "$PR_ERRORS_FILE"
            if [[ -n "$NEW_ERRORS" ]]; then
              BUILD_VERDICT="pre_existing_plus_new"
              echo "  ⚠ NEW tsc errors on PR branch:"
              echo "$NEW_ERRORS" | head -5 | sed 's/^/    /'
            else
              BUILD_VERDICT="pre_existing"
            fi
          else
            BUILD_VERDICT="fail"
          fi
        else
          # Check npm audit severity — CRITICAL vulnerabilities should trigger security review
          if [[ "$ECOSYSTEM" == "npm" && "$AUDIT_CRITICAL" -gt 0 ]]; then
            BUILD_VERDICT="security_review"
            echo "  ⚠️  CRITICAL vulnerabilities detected — manual security review required"
          elif [[ "$ECOSYSTEM" == "npm" && "$AUDIT_HIGH" -gt 0 && "$BUMP" == "major" ]]; then
            BUILD_VERDICT="security_review"
            echo "  ⚠️  HIGH vulnerabilities with major version bump — manual security review recommended"
          else
            BUILD_VERDICT="pass"
          fi
        fi
      else
        MAIN_EXIT="-1"
        _MAIN_OUTPUT_FOR_COMPARISON=""
        case "$ECOSYSTEM" in
          gomod)
            # For multi-module repos, use per-module baseline instead of worst_exit (A2-3/CR2-2).
            # Look up the baseline for the specific module this PR touches via PKG_DIR.
            if [[ "$_GO_MULTI_MODULE" == "true" && "$PKG_DIR" != "/" ]]; then
              _pkg_mod_key=$(echo "$PKG_DIR" | tr '/' '_')
              _pkg_mod_exit_file="/tmp/_bc_main_go_mod_exit_${_pkg_mod_key}.txt"
              _pkg_mod_output_file="/tmp/_bc_main_go_mod_output_${_pkg_mod_key}.txt"
              if [[ -f "$_pkg_mod_exit_file" ]]; then
                MAIN_EXIT=$(cat "$_pkg_mod_exit_file" 2>/dev/null || echo "$main_go_exit")
                _MAIN_OUTPUT_FOR_COMPARISON=$(cat "$_pkg_mod_output_file" 2>/dev/null || echo "$main_go_output")
                echo "  multi-module: using per-module baseline for $PKG_DIR (exit=$MAIN_EXIT)"
              else
                # PKG_DIR might be a subdirectory of a module — try the root module
                _root_exit_file="/tmp/_bc_main_go_mod_exit_..txt"
                if [[ -f "$_root_exit_file" ]]; then
                  MAIN_EXIT=$(cat "$_root_exit_file" 2>/dev/null || echo "$main_go_exit")
                  _MAIN_OUTPUT_FOR_COMPARISON=$(cat "/tmp/_bc_main_go_mod_output_..txt" 2>/dev/null || echo "$main_go_output")
                  echo "  multi-module: PKG_DIR=$PKG_DIR not found as module, using root module baseline (exit=$MAIN_EXIT)"
                else
                  MAIN_EXIT=$main_go_exit
                  _MAIN_OUTPUT_FOR_COMPARISON="$main_go_output"
                  echo "  multi-module: no per-module baseline for $PKG_DIR, using worst_exit=$MAIN_EXIT"
                fi
              fi
            else
              MAIN_EXIT=$main_go_exit
              _MAIN_OUTPUT_FOR_COMPARISON="$main_go_output"
            fi
            ;;
          pip)   MAIN_EXIT=$main_pip_exit; _MAIN_OUTPUT_FOR_COMPARISON="$main_pip_output" ;;
        esac

        if [[ "$BUILD_EXIT" -eq 0 ]]; then
          BUILD_VERDICT="pass"
        elif [[ "$MAIN_EXIT" -eq 0 || "$MAIN_EXIT" -eq -1 ]]; then
          # P0 FIX (V8 review C3/1.1): baseline PASSES (exit=0) or wasn't run (-1),
          # but PR build FAILS. This is a genuine regression, NOT pre-existing.
          # Previous code fell through to error text comparison which could
          # false-positive match go vet warnings in baseline output against
          # go build errors in PR output, misclassifying as pre_existing.
          BUILD_VERDICT="fail"
          echo "  build: baseline exit=$MAIN_EXIT, PR exit=$BUILD_EXIT — genuine failure (not pre-existing)"
        elif [[ "$MAIN_EXIT" -eq 124 ]]; then
          # Baseline timed out (A4-4). The timeout means we got PARTIAL output.
          # V9.6 FIX (P0-1): Still compare errors — if PR has NEW .go:NNN errors
          # not present in main's partial output, those are genuine regressions.
          # Example: k8s version-skew errors (undefined: metav1.*) only appear
          # on PR branch but not on main because main timed out before reaching
          # the type-check of client-go. These must be caught.
          BUILD_VERDICT="pre_existing"
          echo "  ⚠ baseline build timed out (exit=124) — comparing partial errors"
          if [[ "$ECOSYSTEM" == "gomod" && -n "$BUILD_OUTPUT" ]]; then
            MAIN_ERR_FILE="/tmp/_bc_main_go_errors.txt"
            PR_ERR_FILE="/tmp/_bc_pr_go_errors_${PR_NUM}.txt"
            echo "${_MAIN_OUTPUT_FOR_COMPARISON:-$main_go_output}" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
            echo "$BUILD_OUTPUT" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$PR_ERR_FILE" 2>/dev/null || true
            NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
            rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
            if [[ -n "$NEW_ERRORS" ]]; then
              BUILD_VERDICT="pre_existing_plus_new"
              echo "  ⚠ NEW errors on PR branch (not in timed-out main output):"
              echo "$NEW_ERRORS" | head -5 | sed 's/^/    /'
            fi
          fi
        elif [[ "$MAIN_EXIT" -ne 0 ]]; then
          # Both fail (main_exit > 0, not 124) — check for new errors vs baseline
          if [[ "$ECOSYSTEM" == "gomod" ]]; then
            MAIN_ERR_FILE="/tmp/_bc_main_go_errors.txt"
            PR_ERR_FILE="/tmp/_bc_pr_go_errors_${PR_NUM}.txt"
            # Use per-module baseline output for comparison instead of global main_go_output (A2-3)
            echo "${_MAIN_OUTPUT_FOR_COMPARISON:-$main_go_output}" | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
            echo "$BUILD_OUTPUT"   | grep -E '^.*\.go:[0-9]+' | normalize_go_errors | sort -u > "$PR_ERR_FILE"   2>/dev/null || true
            NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
            rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
          elif [[ "$ECOSYSTEM" == "pip" ]]; then
            MAIN_ERR_FILE="/tmp/_bc_main_pip_errors.txt"
            PR_ERR_FILE="/tmp/_bc_pr_pip_errors_${PR_NUM}.txt"
            echo "$main_pip_output" | grep -iE 'error:|could not find|no matching distribution|importerror|modulenotfounderror|attributeerror|typeerror|runtimeerror|syntaxerror|command errored|setup\.py error|environment error|resolve.*failed|dependency.*conflict|unspecified satisfies requirement' | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
            echo "$BUILD_OUTPUT" | grep -iE 'error:|could not find|no matching distribution|importerror|modulenotfounderror|attributeerror|typeerror|runtimeerror|syntaxerror|command errored|setup\.py error|environment error|resolve.*failed|dependency.*conflict|unspecified satisfies requirement' | sort -u > "$PR_ERR_FILE"   2>/dev/null || true
            NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
            rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
          fi
          if [[ -n "$NEW_ERRORS" ]]; then
            BUILD_VERDICT="pre_existing_plus_new"
            echo "  ⚠ NEW errors on PR branch:"
            echo "$NEW_ERRORS" | head -5 | sed 's/^/    /'
          else
            BUILD_VERDICT="pre_existing"
          fi
        else
          BUILD_VERDICT="fail"
        fi
      fi

      echo "  build: exit=$BUILD_EXIT verdict=$BUILD_VERDICT"

      # Clean up worktree
      git worktree remove "$PR_WORKTREE" --force 2>/dev/null || { chmod -R u+w "$PR_WORKTREE" 2>/dev/null; rm -rf "$PR_WORKTREE" 2>/dev/null; } || true
    fi
  elif [[ "$ECOSYSTEM" == "maven" ]]; then
    rm -rf "$PR_WORKTREE" 2>/dev/null || true
    git worktree add "$PR_WORKTREE" "origin/$PR_BRANCH" --quiet 2>/dev/null || { echo "  worktree: failed"; BUILD_VERDICT="error"; }
    if [[ -d "$PR_WORKTREE" ]]; then
      BUILD_DIR="$PR_WORKTREE"
      [[ "$PKG_DIR" != "/" && -d "$PR_WORKTREE/$PKG_DIR" ]] && BUILD_DIR="$PR_WORKTREE/$PKG_DIR"
      if command -v mvn &>/dev/null; then
        echo "  build: mvn compile in ${BUILD_DIR#$PR_WORKTREE/}..."
        BUILD_OUTPUT=$(cd "$BUILD_DIR" && timeout 300 mvn compile -q 2>&1)
        BUILD_EXIT=$?
        BUILD_VERDICT=$([[ "$BUILD_EXIT" -eq 0 ]] && echo "pass" || echo "fail")
        [[ "$BUILD_EXIT" -eq 0 ]] && INSTALL_OK="true"
      else
        echo "  build: maven not available"; BUILD_VERDICT="skip"
      fi
      git worktree remove "$PR_WORKTREE" --force 2>/dev/null || { chmod -R u+w "$PR_WORKTREE" 2>/dev/null; rm -rf "$PR_WORKTREE" 2>/dev/null; } || true
    fi
  elif [[ "$ECOSYSTEM" == "docker" ]]; then
    echo "  build: Docker — validating base image"
    DOCKERFILE_PATH=""
    [[ "$PKG_DIR" != "/" && -f "$PKG_DIR/Dockerfile" ]] && DOCKERFILE_PATH="$PKG_DIR/Dockerfile"
    if [[ -n "$DOCKERFILE_PATH" ]]; then
      DOCKER_BASE=$(grep -m1 "^FROM" "$DOCKERFILE_PATH" 2>/dev/null | sed 's/^FROM //;s/ .*//')
      DOCKER_CMD=$(grep -E "^(CMD|ENTRYPOINT)" "$DOCKERFILE_PATH" 2>/dev/null | tail -1)
      echo "  docker: base=$DOCKER_BASE cmd=$DOCKER_CMD"
      if command -v docker &>/dev/null; then
        if docker pull "$DOCKER_BASE" > /dev/null 2>&1; then
          BUILD_OUTPUT="Dockerfile: $DOCKERFILE_PATH Base: $DOCKER_BASE CMD: $DOCKER_CMD"
          BUILD_EXIT=0
          BUILD_VERDICT="pass"
          INSTALL_OK="true"
        else
          BUILD_OUTPUT="Dockerfile: $DOCKERFILE_PATH Base: $DOCKER_BASE — image pull failed"
          BUILD_EXIT=1
          BUILD_VERDICT="fail"
        fi
      else
        BUILD_OUTPUT="Dockerfile: $DOCKERFILE_PATH Base: $DOCKER_BASE CMD: $DOCKER_CMD (docker not available)"
        BUILD_EXIT=-1
        BUILD_VERDICT="skip"
      fi
    else
      BUILD_OUTPUT="Dockerfile not found for $PKG_DIR"
      BUILD_EXIT=-1
      BUILD_VERDICT="skip"
    fi
  elif [[ "$ECOSYSTEM" == "actions" ]]; then
    # GitHub Actions PRs only affect .github/workflows/ files — no application code.
    # They are inherently safe and need no build verification. Setting unconditionally SAFE
    # instead of trying to validate via GitHub API (which fails for non-actions/* orgs
    # and used a regex that never matched Dependabot PR title format).
    echo "  build: GitHub Actions — CI-only change, inherently safe"
    BUILD_OUTPUT="actions: ${PKG} ${FROM_VER:-?} → ${TO_VER:-?} — CI-only dependency, no build needed"
    BUILD_EXIT=0
    BUILD_VERDICT="pass"
    INSTALL_OK="true"
  else
    echo "  build: skipped ($ECOSYSTEM — no build possible)"

  fi

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
          _BC_GOSUM_NEW_LINES="$_GOSUM_NEW_LINES" python3 -c '
import os, json
def parse(v):
    s = v.lstrip("v").split("+", 1)[0].split("-", 1)[0]
    p = s.split(".")
    try:
        return tuple(int(x) for x in p[:3]) + (0,) * (3 - min(3, len(p)))
    except ValueError:
        return None
best = {}
for line in os.environ.get("_BC_GOSUM_NEW_LINES", "").splitlines():
    f = line.split()
    if len(f) < 2:
        continue
    mod, ver = f[0], f[1]
    # Only the content-hash line ("mod v1.2.3 h1:…") proves the module version was
    # actually SELECTED/built. A "/go.mod"-only line is just an MVS candidate and is
    # NOT proof of a resolved bump — skip it to avoid over-crediting CVE fixes.
    if ver.endswith("/go.mod"):
        continue
    pv = parse(ver)
    if pv is None:
        continue
    if mod not in best or pv > best[mod][0]:
        best[mod] = (pv, ver)
print(json.dumps({m: v for m, (pv, v) in best.items()}))
' > "/tmp/_bc_bumped_mods_${PR_NUM}.json" 2>/dev/null || echo '{}' > "/tmp/_bc_bumped_mods_${PR_NUM}.json"
        fi
        echo "  go.sum: $GOSUM_NEW_COUNT new transitive entries ($GOSUM_NEW_NAMES)"
      fi

      git worktree remove "$PR_WORKTREE" --force 2>/dev/null || { chmod -R u+w "$PR_WORKTREE" 2>/dev/null; rm -rf "$PR_WORKTREE" 2>/dev/null; } || true
    fi
  fi

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

  python3 << PYEOF
import json, os

results_file = "$RESULTS_FILE"
pr_num = "$PR_NUM"

with open(results_file) as f:
    data = json.load(f)

# Read deterministic output (CR2-4: use specific exception types, not bare except)
det_path = f"/tmp/_bc_det_{pr_num}.json"
try:
    with open(det_path) as f:
        det_raw = f.read().strip()
    deterministic = json.loads(det_raw) if det_raw and det_raw != '{}' else {}
except (IOError, OSError, json.JSONDecodeError, ValueError):
    deterministic = {}

# Read cascade_impact (from temp file to avoid shell injection — Finding-3.2)
try:
    with open(f"/tmp/_bc_cascade_{pr_num}.txt") as f:
        cascade_str = f.read().strip()
    cascade_impact = json.loads(cascade_str) if cascade_str else []
except (IOError, OSError, json.JSONDecodeError, ValueError):
    cascade_impact = []


# Read files_importing
files_path = f"/tmp/_bc_files_{pr_num}.json"
try:
    with open(files_path) as f:
        files_importing = json.loads(f.read().strip())
except (IOError, OSError, json.JSONDecodeError, ValueError):
    files_importing = []

# Read additional_imports for multi-package PRs (from temp file — Finding-3.2)
try:
    with open(f"/tmp/_bc_addl_imports_{pr_num}.json") as f:
        additional_imports = json.loads(f.read().strip())
except (IOError, OSError, json.JSONDecodeError, ValueError):
    additional_imports = []

# Read build output
build_out_path = f"/tmp/_bc_build_out_{pr_num}.txt"
try:
    with open(build_out_path) as f:
        build_output = f.read()
except (IOError, OSError):
    build_output = ""

# Read test output
test_out_path = f"/tmp/_bc_test_out_{pr_num}.txt"
try:
    with open(test_out_path) as f:
        test_output = f.read()
except (IOError, OSError):
    test_output = ""

# Read new errors (errors on PR branch not present on main)
new_errors_path = f"/tmp/_bc_new_errors_{pr_num}.txt"
try:
    with open(new_errors_path) as f:
        new_errors_raw = f.read().strip()
    new_errors = [e for e in new_errors_raw.split('\n') if e.strip()] if new_errors_raw else []
except (IOError, OSError, ValueError):
    new_errors = []

# Read go.sum new transitive count and names
try:
    with open(f"/tmp/_bc_gosum_new_{pr_num}.txt") as f:
        gosum_new_count = int(f.read().strip() or "0")
except (IOError, OSError, ValueError):
    gosum_new_count = 0
try:
    with open(f"/tmp/_bc_gosum_names_{pr_num}.txt") as f:
        gosum_new_names = f.read().strip()
except (IOError, OSError):
    gosum_new_names = ""
try:
    with open(f"/tmp/_bc_gosum_total_pr_{pr_num}.txt") as f:
        gosum_total_pr = int(f.read().strip() or "0")
except (IOError, OSError, ValueError):
    gosum_total_pr = 0
try:
    with open(f"/tmp/_bc_gosum_total_main_{pr_num}.txt") as f:
        gosum_total_main = int(f.read().strip() or "0")
except (IOError, OSError, ValueError):
    gosum_total_main = 0
try:
    with open(f"/tmp/_bc_bumped_mods_{pr_num}.json") as f:
        bumped_modules = json.load(f)
        if not isinstance(bumped_modules, dict):
            bumped_modules = {}
except (IOError, OSError, ValueError):
    bumped_modules = {}

# Read govulncheck status + first finding (if any)
try:
    with open(f"/tmp/_bc_vuln_status_{pr_num}.txt") as f:
        vuln_status = f.read().strip() or "unknown"
except (IOError, OSError):
    vuln_status = "unknown"
try:
    with open(f"/tmp/_bc_vuln_finding_{pr_num}.txt") as f:
        vuln_finding = f.read().strip()
except (IOError, OSError):
    vuln_finding = ""
try:
    with open(f"/tmp/_bc_vuln_new_findings_{pr_num}.txt") as f:
        vuln_new_findings = [l.strip() for l in f.readlines() if l.strip()]
except (IOError, OSError):
    vuln_new_findings = []
try:
    with open(f"/tmp/_bc_vuln_preexisting_count_{pr_num}.txt") as f:
        vuln_preexisting_count = int(f.read().strip() or "0")
except (IOError, OSError, ValueError):
    vuln_preexisting_count = 0
# V9.8 iter6 (C): load vuln scan output from its own file (separate from BUILD_OUTPUT)
try:
    with open(f"/tmp/_bc_vuln_output_{pr_num}.txt") as f:
        vuln_output = f.read()
except (IOError, OSError):
    vuln_output = ""

# Read PR metadata from temp files to avoid shell injection (Finding-4.4)
# MUST be defined before INFRA_ERROR_PATTERNS because eco is used there (Finding-5.1)
def _read_tmp(suffix):
    try:
        with open(f"/tmp/_bc_{suffix}_{pr_num}.txt") as f:
            return f.read().strip()
    except (IOError, OSError):
        return ""

pkg = _read_tmp("pkg") or "unknown"
from_ver = _read_tmp("from_ver")
to_ver = _read_tmp("to_ver")
dep_type = _read_tmp("dep_type") or "unknown"
dep_relation = _read_tmp("dep_relation") or "unknown"
bump = _read_tmp("bump") or "unknown"
eco = _read_tmp("ecosystem") or "unknown"

# Parse CVEs
cves_raw = _read_tmp("cves")
cves = [c.strip() for c in cves_raw.split(",") if c.strip()] if cves_raw else []

# V8 FIX: Parse enriched CVE details (severity, CVSS, advisory URL)
try:
    with open(f"/tmp/_bc_cve_details_{pr_num}.json") as f:
        cve_details = json.loads(f.read().strip() or "[]")
except (IOError, OSError, json.JSONDecodeError, ValueError):
    cve_details = []

# Filter out infrastructure artifact errors from new_errors.
# When install_fallback/local_fallback is used, tsc may report different errors
# because file: links don't provide type declarations. These are NOT caused by the upgrade.
# Additionally, when both baseline and PR tsc fail (main_exit=2, pr_exit=2),
# non-deterministic tsc output can produce "new" errors that are actually pre-existing.
# We filter known patterns that are infrastructure artifacts, not genuine regressions.
INFRA_ERROR_PATTERNS = [
    # Private packages resolved via file: links (no .d.ts)
    "Cannot find module '@netapp-cloud-datamigrate/",
    "Cannot find module 'rxjs'",
    "Cannot find module './../../node_modules/",
    # Transitive deps missing when install degrades
    "Cannot find module 'winston'",
    "Cannot find module '../../utils/file-type-detection.service'",
    # Flaky tsc error: appears non-deterministically across runs
    # (confirmed: GitHub Actions-only PRs produce this same error)
    "TS2349: This expression is not callable",
    # Type mismatches from degraded install (jest mock types, etc.)
    "is not assignable to type 'MockInstance<",
    "commands: undefined[]",
    # Missing properties from partial type resolution
    "publishBulkToCommandStream",
    "toThrowError",
]

# Go-specific infra patterns (added separately for clarity)
GO_INFRA_PATTERNS = [
    # Go build cache corruption (stale object files with hash paths)
    "go-build/HASH",   # After normalize_go_errors, cache paths become go-build/HASH
    # Go module download / proxy errors (not caused by upgrade)
    "GOPROXY",
    "connection refused",
    "i/o timeout",
]
if eco == "gomod":
    INFRA_ERROR_PATTERNS.extend(GO_INFRA_PATTERNS)
# Append project-specific patterns from .github/breakability-config.yml
# Read from temp file to avoid shell injection via unquoted heredoc (Finding-3.2)
try:
    with open(f"/tmp/_bc_extra_infra_{pr_num}.txt") as f:
        extra_raw = f.read()
except:
    extra_raw = ""
for line in extra_raw.strip().split('\n'):
    line = line.strip()
    if line and line not in INFRA_ERROR_PATTERNS:
        INFRA_ERROR_PATTERNS.append(line)
if new_errors:
    real_errors = [e for e in new_errors if not any(p in e for p in INFRA_ERROR_PATTERNS)]
    infra_filtered = len(new_errors) - len(real_errors)
    new_errors = real_errors

# Test values
test_ran = True if "$TEST_RAN" == "true" else False
test_exit_raw = "$TEST_EXIT"
test_exit = int(test_exit_raw) if test_exit_raw not in ("null", "") else None
no_go_tests = (eco == "gomod" and test_ran and test_exit == 0 and "[no test files]" in (test_output or ""))

# If all "new" errors were infra artifacts, downgrade verdict to pre_existing
build_verdict = "$BUILD_VERDICT"
if build_verdict == "pre_existing_plus_new" and not new_errors:
    build_verdict = "pre_existing"

# For Go builds: if error_class is infrastructure-related (not a code problem),
# the failure is NOT caused by the upgrade — downgrade verdict.
# P0 FIX (v9): Only downgrade if the baseline ALSO failed (main_exit != 0).
# When main_exit == 0 the baseline passes cleanly, so even infra-looking errors
# on the PR branch are a genuine regression introduced by the upgrade.
error_class = "${ERROR_CLASS:-}"
main_exit_eco = $MAIN_EXIT_FOR_ECO
oom_override = False  # tracks whether verdict was overridden due to OOM on unrelated packages
oom_packages = []     # which packages were OOM-killed (for comment attribution)
if error_class in ("cache_corruption", "infra_error", "private_module", "resource_exhaustion", "timeout"):
    if build_verdict in ("fail", "pre_existing_plus_new") and main_exit_eco != 0:
        build_verdict = "pre_existing"  # baseline also fails — treat as infra issue
    elif build_verdict in ("fail", "pre_existing_plus_new") and main_exit_eco == 0:
        # V9.3 FIX: OOM misclassification (P1 from all reviewers).
        # When error_class is resource_exhaustion and baseline passes, check if ALL
        # build errors are "signal: killed" on packages UNRELATED to the PR's upgraded
        # dependency. If the PR's own targeted dirs built fine (or have 0 imports),
        # the OOM is infrastructure, not a code regression.
        if error_class == "resource_exhaustion" and eco == "gomod":
            import re
            # Extract which packages were killed from build output
            killed_pkgs = set()
            for line in build_output.splitlines():
                if 'signal: killed' in line.lower() or 'signal: kill' in line.lower():
                    # Go build output format: "github.com/org/repo/pkg/subpkg: ...signal: killed"
                    m = re.match(r'^(\S+?):\s', line)
                    if m:
                        killed_pkgs.add(m.group(1))
            # Get the PR's targeted build dirs from files_importing
            targeted_dirs = set()
            for fi in files_importing:
                fpath = fi.split(':')[0] if ':' in fi else fi
                d = os.path.dirname(fpath)
                if d:
                    targeted_dirs.add(d)
            # Check: are ALL errors signal:killed on unrelated packages?
            # Conditions for override:
            # 1. All build errors are signal:killed (no real type errors)
            # 2. None of the killed packages overlap with PR's targeted dirs
            # 3. No new_errors found (or all were infra-filtered)
            has_real_type_errors = False
            for line in build_output.splitlines():
                line_l = line.lower().strip()
                if not line_l:
                    continue
                # Skip info/targeted build output lines
                if line_l.startswith('targeted build') or line_l.startswith('full build') or line_l.startswith('dirs:') or line_l.startswith('---'):
                    continue
                # If line contains a Go compile error (.go:NN:NN:) it's a real error
                if re.search(r'\.go:\d+:\d+:', line):
                    has_real_type_errors = True
                    break
            # Determine if killed packages overlap with targeted dirs
            killed_overlaps_target = False
            for kp in killed_pkgs:
                for td in targeted_dirs:
                    if td in kp or kp.endswith(td):
                        killed_overlaps_target = True
                        break
            if killed_pkgs and not has_real_type_errors and not killed_overlaps_target and not new_errors:
                build_verdict = "pass"
                oom_override = True
                oom_packages = sorted(killed_pkgs)
        # else: baseline passes but errors are real code regressions — keep verdict as-is

pr_data = {
    "package": pkg,
    "from": from_ver,
    "to": to_ver,
    "ecosystem": eco,
    "bump": bump,
    "dep_type": dep_type,
    "dep_relation": dep_relation,
    "cves": cves,
    "cve_details": cve_details,
    "deterministic": deterministic,
    "merge_risk": deterministic.get("merge_risk", {}) if deterministic else {},
    "build": {
        "main_exit": $MAIN_EXIT_FOR_ECO,
        "pr_exit": $BUILD_EXIT,
        "verdict": build_verdict,
        "output_tail": build_output,
        "new_errors": new_errors,
        "install_method": "${INSTALL_METHOD:-ci}",
        "error_class": "${ERROR_CLASS:-}",
        "oom_override": oom_override,
        "oom_packages": oom_packages
    },
    "test": {
        "ran": test_ran,
        "exit": test_exit,
        "main_test_exit": $MAIN_GO_TEST_EXIT_PR,
        "main_npm_test_exit": $MAIN_NPM_TEST_EXIT_PR,
        "output_tail": test_output
    },
    "smoke": {
        "ran": True if "$SMOKE_RAN" == "true" else False,
        "exit": int("$SMOKE_EXIT") if "$SMOKE_EXIT" not in ("null", "") else None
    },
    "files_importing": files_importing,
    "additional_imports": additional_imports,
    "diff_lines": $DIFF_LINES,
    "diff_truncated": True if "$DIFF_TRUNCATED" == "true" else False,
    "diff_path": "/tmp/pr-${PR_NUM}.diff",
    "pkg_dir": "$PKG_DIR",
    "cascade_impact": cascade_impact,
    "gosum_new_count": gosum_new_count,
    "gosum_new_names": gosum_new_names,
    "gosum_total_pr": gosum_total_pr,
    "gosum_total_main": gosum_total_main,
    "bumped_modules": bumped_modules,
    "vuln_status": vuln_status,
    "vuln_finding": vuln_finding,
    "vuln_new_findings": vuln_new_findings,
    "vuln_preexisting_count": vuln_preexisting_count,
    "vuln_output": vuln_output,
    "go_resolution": {
        "command": open(f"$BC_SCRATCH_DIR/_bc_go_resolution_command_{pr_num}.txt").read().strip() if os.path.exists(f"$BC_SCRATCH_DIR/_bc_go_resolution_command_{pr_num}.txt") else "",
        "output_tail": open(f"$BC_SCRATCH_DIR/_bc_go_resolution_output_{pr_num}.txt").read()[-20000:] if os.path.exists(f"$BC_SCRATCH_DIR/_bc_go_resolution_output_{pr_num}.txt") else "",
        "modsum_diff": open(f"$BC_SCRATCH_DIR/_bc_go_modsum_diff_{pr_num}.txt").read()[-30000:] if os.path.exists(f"$BC_SCRATCH_DIR/_bc_go_modsum_diff_{pr_num}.txt") else "",
    },
    "nestjs_peer_warning": open(f"/tmp/_bc_peer_warn_{pr_num}.txt").read().strip() if os.path.exists(f"/tmp/_bc_peer_warn_{pr_num}.txt") else "",
    "install_ok": True if "$INSTALL_OK" == "true" else False,
    "additional_packages": open(f"/tmp/_bc_addl_pkgs_{pr_num}.txt").read().strip() if os.path.exists(f"/tmp/_bc_addl_pkgs_{pr_num}.txt") else "",
    "mergeable_status": "$MERGEABLE_STATUS",
    "npm_audit": {
        "critical": $AUDIT_CRITICAL,
        "high": $AUDIT_HIGH
    },
    "no_test_confidence": {}
}

if eco == "gomod" and no_go_tests:
    api_changes = len(deterministic.get("apiChanges", [])) if deterministic else 0
    symbol_results = deterministic.get("verification", {}).get("symbolResults", {}) if deterministic else {}
    used_symbols = 0
    if isinstance(symbol_results, dict):
        for val in symbol_results.values():
            if isinstance(val, dict) and val.get("used"):
                used_symbols += 1
            elif isinstance(val, (list, tuple, set)):
                used_symbols += len(val)
    usage = len(files_importing) + used_symbols
    score = 0
    if api_changes == 0:
        score += 2
    elif api_changes <= 2:
        score += 1
    if usage == 0:
        score += 2
    elif usage <= 3:
        score += 1
    if bump in ("patch", "minor"):
        score += 1
    if dep_type in ("dev", "development"):
        score += 1
    confidence = "high" if score >= 5 else ("medium" if score >= 3 else "low")
    residual = "No Go test files were present, so runtime behavior is not exercised by CI. "
    if api_changes:
        residual += f"API diff reported {api_changes} change(s). "
    else:
        residual += "API diff reported no removed/changed exported APIs. "
    if usage:
        residual += f"Reachability saw {usage} usage signal(s); review touched call sites if behavior changed."
    else:
        residual += "No direct usage was found in scanned files; remaining risk is transitive/runtime behavior."
    pr_data["no_test_confidence"] = {
        "applies": True,
        "confidence": confidence,
        "basis": {"api_changes": api_changes, "usage_signals": usage, "semver_bump": bump, "dep_type": dep_type},
        "residual_risk": residual
    }

# ── Ownership classification ─────────────────────────────────
# Tells reviewers WHO fixes this and whether THEIR code is affected.
# Re-use eco, pkg, dep_type, dep_relation from _read_tmp() above (Finding-5.2).
# Do NOT re-assign from shell expansion — that re-introduces injection risk.
dep_rel = dep_relation  # alias for shorter references below
pkg_dir = _read_tmp("pkg_dir") or "/"
n_imports = len(files_importing)

KNOWN_BUILD_TOOLS = {
    "typescript", "eslint", "prettier", "webpack", "vite", "rollup",
    "babel", "jest", "vitest", "mocha", "nyc", "c8", "esbuild", "swc",
    "ts-jest", "ts-node", "tsup", "turbo", "lerna", "nx",
    "@typescript-eslint/parser", "@typescript-eslint/eslint-plugin",
    "@nestjs/schematics", "@nestjs/cli", "husky", "lint-staged",
    "commitlint", "@commitlint/cli", "@commitlint/config-conventional",
    "nodemon", "ts-loader", "webpack-cli", "rimraf", "concurrently",
}
# Platform SDKs: you build a plugin ON these (compile against their API)
PLATFORM_SDK_IMAGES = {"keycloak", "liquibase", "tinygo", "maven", "gradle"}
# Service images: you just run these as infrastructure (base_image)
SERVICE_IMAGES = {"postgres", "mysql", "redis", "mongo", "elasticsearch",
                  "rabbitmq", "kafka", "zookeeper", "consul", "vault", "nginx"}

if eco == "actions":
    ownership = "ci_tool"
elif eco == "docker":
    # Platform SDK (you build a plugin on it) vs base image (OS/runtime)
    base_img = (build_output or "").lower()
    if any(p in base_img for p in PLATFORM_SDK_IMAGES):
        ownership = "platform_sdk"
    else:
        ownership = "base_image"
elif eco == "maven":
    ownership = "platform_sdk"
elif dep_type == "dev" and any(t in pkg.lower() for t in ["eslint", "prettier", "webpack", "vite", "rollup", "babel", "jest", "vitest", "typescript", "tsc", "swc", "esbuild", "turbo", "nx"]):
    ownership = "build_tool"
elif pkg.lower() in KNOWN_BUILD_TOOLS:
    ownership = "build_tool"
elif pkg.lower().startswith("@types/"):
    # @types/* with actual imports = direct_dep (your code relies on these types)
    # @types/* with 0 imports and dev dep = build_tool (ambient declarations)
    if n_imports > 0 or dep_type == "production":
        ownership = "direct_dep"
    else:
        ownership = "build_tool"
elif dep_rel == "transitive" and n_imports == 0:
    ownership = "transitive_dep"
else:
    ownership = "direct_dep"

pr_data["ownership_class"] = ownership

# ── Verification Level (L0–L5) ───────────────────────────────
# Graduated confidence based on what ACTUALLY ran, not what we hope.
# L0: Unresolved — couldn't install
# L1: Dep-resolved — npm ci / pip install / go mod tidy succeeded
# L2: Type-checked — tsc --noEmit / go build passed (no new type errors)
# L3: Symbols-verified — ESM/CJS probe confirmed symbol existence (from deterministic.verification)
# L4: Tests-pass — npm test / go test / pytest passed on PR branch
# L5: Fully-verified — tests pass AND no new errors AND API compatible AND smoke pass

# Docker and actions now have real build verdicts — let them flow through normal confidence logic
install_ok = pr_data.get("install_ok", False)
# IMPORTANT: reuse the Python build_verdict from line ~2584, NOT the shell $BUILD_VERDICT.
# The earlier Python code may have downgraded build_verdict (e.g., fail -> pre_existing for
# infra errors). Re-reading from shell would discard that fix. (CR2-1)
# build_verdict is already set correctly above — do NOT overwrite it here.
test_ran_val = test_ran
test_exit_val = test_exit
smoke_ran_val = pr_data["smoke"]["ran"]
smoke_exit_val = pr_data["smoke"]["exit"]
det_verified = deterministic.get("verification", {}).get("verified", False) if deterministic else False
det_compatible = deterministic.get("verification", {}).get("compatible", None) if deterministic else None

steps = []
level = 0

if not install_ok:
    level = 0
    steps.append({"step": "dependency_resolution", "status": "fail", "detail": "${ERROR_CLASS:-}" or "install failed"})
else:
    level = 1
    steps.append({"step": "dependency_resolution", "status": "pass"})

    # L2: Type-checking (tsc / go build)
    tsc_ran = "$PR_TSC_EXIT" not in ("-1", "")
    tsc_passed = "$PR_TSC_EXIT" == "0" if tsc_ran else False
    pr_exit_val = pr_data.get("build", {}).get("pr_exit", -1)
    if eco in ("gomod", "pip"):
        # go build / pip import check IS the type-check equivalent
        if build_verdict in ("pass", "security_review"):
            level = 2
            steps.append({"step": "type_check", "status": "pass"})
        elif build_verdict == "pre_existing" and pr_exit_val == 0:
            # v9.2 FIX: PR build actually passes (exit=0) but verdict was set to
            # pre_existing (e.g., baseline timed out). The PR branch builds clean,
            # so this IS L2 — type-check passed on the PR branch.
            level = 2
            steps.append({"step": "type_check", "status": "pass", "detail": "PR build passes (baseline had errors)"})
        elif build_verdict == "pre_existing":
            # Build fails on both branches with same errors — NOT a real pass (CR3-8).
            # Stay at L1 (like npm does for tsc pre_existing), mark as inconclusive.
            level = 1  # DO NOT promote to L2
            # v9: Include first error line so the comment says WHAT failed
            _pre_sample = new_errors[0] if new_errors else (build_output.strip().splitlines()[-1] if build_output.strip() else "unknown")
            steps.append({"step": "type_check", "status": "pre_existing", "detail": f"same errors on main — {_pre_sample[:120]}"})
        elif build_verdict in ("fail", "pre_existing_plus_new"):
            # V8 FIX (L2/1.4/1.5): Build WAS run and FAILED with new errors.
            # This IS L2 (type-check was attempted), not L1 (dep-resolved only).
            # The BUILD_FAILS comment should show L2, not L1.
            level = 2
            # v9: Include first new error so the comment says WHAT broke
            _fail_sample = new_errors[0] if new_errors else "build exit non-zero"
            steps.append({"step": "type_check", "status": "fail", "detail": f"{len(new_errors)} new error(s): {_fail_sample[:120]}"})
        else:
            steps.append({"step": "type_check", "status": "fail"})
    elif tsc_ran:
        if tsc_passed:
            # tsc actually passed — genuine L2
            level = 2
            steps.append({"step": "type_check", "status": "pass"})
        elif build_verdict == "pre_existing" and "$PR_TSC_EXIT" == "0":
            # v9.2 FIX: tsc actually passed on PR branch (exit=0) but verdict was
            # set to pre_existing (e.g., baseline timed out or had other issues).
            # The PR's type-check passed, so this IS L2.
            level = 2
            steps.append({"step": "type_check", "status": "pass", "detail": "tsc passes on PR (baseline had errors)"})
        elif build_verdict == "pre_existing":
            # tsc failed on both branches with same errors — NOT a real pass
            # Stay at L1, mark type_check as "pre_existing" (inconclusive)
            level = 1  # DO NOT promote to L2
            # v9: Include first error so the comment says WHAT failed
            _tsc_pre_sample = new_errors[0] if new_errors else (build_output.strip().splitlines()[-1] if build_output.strip() else "unknown")
            steps.append({"step": "type_check", "status": "pre_existing", "detail": f"same tsc errors on main — {_tsc_pre_sample[:120]}"})
        elif build_verdict in ("fail", "pre_existing_plus_new"):
            # V8 FIX: tsc WAS run and FAILED. This is L2 (attempted), not L1.
            level = 2
            # v9: Include first new error so the comment says WHAT broke
            _tsc_fail_sample = new_errors[0] if new_errors else "tsc exit non-zero"
            steps.append({"step": "type_check", "status": "fail", "detail": f"{len(new_errors)} new error(s): {_tsc_fail_sample[:120]}"})
        else:
            steps.append({"step": "type_check", "status": "fail"})
    else:
        steps.append({"step": "type_check", "status": "skip", "detail": "no tsconfig.json"})
        if build_verdict in ("pass", "security_review"):
            level = 2  # install passed, no tsc to run = still dep-resolved+

    # L3: Symbol verification (from CLI deterministic layer)
    if det_verified:
        level = max(level, 3)
        steps.append({"step": "symbol_verification", "status": "pass", "detail": f"compatible={det_compatible}"})
    elif deterministic:
        steps.append({"step": "symbol_verification", "status": "skip", "detail": "not run or no .d.ts"})
    else:
        steps.append({"step": "symbol_verification", "status": "skip"})

    # L4: Tests
    # For Go: content-level pre-existing comparison (Finding-4.3).
    # Compare actual FAIL lines, not just exit codes, to detect mixed failures
    # where different tests fail on main vs PR.
    main_go_test_exit_raw = "$MAIN_GO_TEST_EXIT_PR"
    main_go_test_exit_val = int(main_go_test_exit_raw) if main_go_test_exit_raw not in ("-1", "") else -1
    # npm test pre-existing comparison (Finding-4.5)
    main_npm_test_exit_raw = "$MAIN_NPM_TEST_EXIT_PR"
    main_npm_test_exit_val = int(main_npm_test_exit_raw) if main_npm_test_exit_raw not in ("-1", "") else -1
    if test_ran_val and test_exit_val is not None:
        if eco == "gomod" and no_go_tests:
            steps.append({"step": "test_suite", "status": "skip", "detail": "go test reported [no test files]; see no_test_confidence"})
        elif test_exit_val == 0:
            level = max(level, 4)
            steps.append({"step": "test_suite", "status": "pass"})
        else:
            is_preexisting_test = False
            preexisting_detail = ""
            if eco == "gomod" and main_go_test_exit_val > 0 and test_exit_val > 0:
                # Content-level comparison: extract FAIL lines from both (Finding-4.3)
                main_test_file = f"/tmp/_bc_main_go_test_out_{pr_num}.txt"
                try:
                    with open(main_test_file) as f:
                        main_test_lines = f.read()
                except (IOError, OSError):
                    main_test_lines = ""
                # Extract "--- FAIL:" lines from Go test output
                import re
                main_fails = set(re.findall(r'--- FAIL: (\S+)', main_test_lines))
                pr_fails = set(re.findall(r'--- FAIL: (\S+)', test_output))
                new_test_fails = pr_fails - main_fails
                if new_test_fails:
                    # PR has NEW test failures not present on main
                    preexisting_detail = f"exit={test_exit_val} — {len(new_test_fails)} new test failure(s): {', '.join(sorted(new_test_fails)[:5])}"
                else:
                    is_preexisting_test = True
                    preexisting_detail = f"exit={test_exit_val} — same failures on main (exit={main_go_test_exit_val})"
            elif eco == "npm" and main_npm_test_exit_val > 0 and test_exit_val > 0:
                # Content-level comparison for npm tests (Finding-5.4, upgrades Finding-4.5)
                # Read baseline npm test output for comparison
                main_npm_test_file = f"/tmp/_bc_main_npm_test_out_{pr_num}.txt"
                try:
                    with open(main_npm_test_file) as f:
                        main_npm_test_lines = f.read()
                except (IOError, OSError):
                    main_npm_test_lines = ""
                import re
                # Jest format: "FAIL src/tests/foo.test.ts" or "FAIL ./src/tests/foo.test.ts"
                main_npm_fails = set(re.findall(r'FAIL\s+(\S+)', main_npm_test_lines))
                pr_npm_fails = set(re.findall(r'FAIL\s+(\S+)', test_output))
                new_npm_test_fails = pr_npm_fails - main_npm_fails
                if new_npm_test_fails:
                    preexisting_detail = f"exit={test_exit_val} — {len(new_npm_test_fails)} new test failure(s): {', '.join(sorted(new_npm_test_fails)[:5])}"
                else:
                    is_preexisting_test = True
                    preexisting_detail = f"exit={test_exit_val} — same failures on main (exit={main_npm_test_exit_val})"
            if is_preexisting_test:
                steps.append({"step": "test_suite", "status": "pre_existing",
                              "detail": preexisting_detail})
            else:
                detail = preexisting_detail if preexisting_detail else f"exit={test_exit_val}"
                steps.append({"step": "test_suite", "status": "fail", "detail": detail})
    else:
        steps.append({"step": "test_suite", "status": "skip", "detail": "not triggered"})

    # L5: Fully verified (tests pass + no new errors + symbols ok + smoke ok)
    if (test_ran_val and test_exit_val == 0 and
        build_verdict in ("pass", "security_review") and
        (det_compatible is True or det_compatible is None)):
        if smoke_ran_val and smoke_exit_val == 0:
            level = 5
            steps.append({"step": "smoke_probe", "status": "pass"})
        elif smoke_ran_val:
            steps.append({"step": "smoke_probe", "status": "fail", "detail": f"exit={smoke_exit_val}"})
        elif not smoke_ran_val:
            # Tests pass but no smoke — still L4
            steps.append({"step": "smoke_probe", "status": "skip", "detail": "no dist/main.js after build"})
    elif smoke_ran_val:
        if smoke_exit_val == 0:
            steps.append({"step": "smoke_probe", "status": "pass"})
        else:
            steps.append({"step": "smoke_probe", "status": "fail", "detail": f"exit={smoke_exit_val}"})

LEVEL_LABELS = {
    -1: "NA_not_applicable",
    0: "L0_unresolved",
    1: "L1_dep_resolved",
    2: "L2_type_checked",
    3: "L3_symbols_verified",
    4: "L4_tests_pass",
    5: "L5_fully_verified"
}

# V8 FIX (H3): Actions PRs should NOT show L2_type_checked — no type-checking
# was performed. They get a distinct label so the merge plan doesn't lie.
if eco == "actions":
    pr_data["verification_level"] = -1
    pr_data["verification_label"] = "CI_ONLY"
else:
    pr_data["verification_level"] = level
    pr_data["verification_label"] = LEVEL_LABELS.get(level, f"L{level}")
    # A build that FAILED still reaches level 2 (type-check was attempted) but must
    # NOT be labelled "L2_type_checked" — that reads as a clean pass. Use a distinct
    # "L2_build_failed" label so the merge plan / signal table never imply the build
    # passed (PR#38 false-positive).
    if level == 2 and build_verdict in ("fail", "pre_existing_plus_new"):
        pr_data["verification_label"] = "L2_build_failed"
if isinstance(pr_data.get("merge_risk"), dict):
    pr_data["merge_risk"].setdefault("evidenceAxis", "limited evidence")
    pr_data["merge_risk"]["buildVerificationAxis"] = f"L{level}" if level >= 0 else pr_data["verification_label"]
    pr_data["merge_risk"]["confidenceAxis"] = pr_data["merge_risk"]["buildVerificationAxis"]
    if isinstance(pr_data.get("deterministic"), dict) and isinstance(pr_data["deterministic"].get("merge_risk"), dict):
        pr_data["deterministic"]["merge_risk"] = pr_data["merge_risk"]

# ── Declared-break reachability resolution ─────────────────────
# A declared-breaking changelog verdict (High) is reachability-BLIND on its own: the break
# may live in a sibling/sub-module the repo does not even import. Extract the affected import
# paths from the breaking bullets, grep the working tree, and either PROVE reachability (name
# the importing file) or DOWNGRADE when nothing imports the affected package.
import re as _dbr_re
import subprocess as _dbr_sub

# ── Behavioral-exposure classifier (deterministic, Go-first) ───────────────────
# Import-level reachability proves only that the affected PACKAGE is imported. For a
# behavioral break that is the WHOLE residual: it tells the developer nothing about
# whether their code touches the changed surface. This classifier refines import into
# SURFACE exposure: does production code reference a changelog-NAMED changed symbol
# (strongest), some exported symbol of the package (subsystem surface, the typical
# shape of an internal-trigger behavioral change), or only import it (lowest)? It
# NEVER asserts safety (internal behavior can change behind a stable API). Go-only
# for now; other ecosystems return 'unknown' so the renderer keeps import-level wording.
# NOTE: this code lives inside an UNQUOTED heredoc, so it must contain no literal
# backtick or bare-dollar character (bash would expand them). We build the backtick
# regex from chr(96) and avoid end-of-string anchors.
_BT = chr(96)
def _extract_named_symbols(text):
    named = set()
    for q, s in _dbr_re.findall(r"\b([a-z][A-Za-z0-9_]*)\.([A-Z][A-Za-z0-9_]{2,})", text or ""):
        named.add(s)
    for chunk in _dbr_re.findall(_BT + r"([^" + _BT + r"]+)" + _BT, text or ""):
        for s in _dbr_re.findall(r"\b([A-Z][A-Za-z0-9_]{2,})\b", chunk):
            named.add(s)
    return named

def _go_local_name(pkg, file_text):
    m = _dbr_re.search(r'^\s*([A-Za-z_]\w*)\s+"' + _dbr_re.escape(pkg) + r'"', file_text or "", _dbr_re.M)
    if m:
        return m.group(1)
    segs = [s for s in pkg.split("/") if s]
    if segs and len(segs) >= 2 and segs[-1][:1] == "v" and segs[-1][1:].isdigit():
        return segs[-2]
    return segs[-1] if segs else pkg

def _classify_behavioral_exposure(repo_root, paths, evidence, text, eco):
    out = {"surface_kind": "unknown", "surface_symbols": [], "named_symbols": [],
           "surface_evidence": [], "surface_by_path": {}}
    if eco != "gomod":
        return out
    named = _extract_named_symbols(text)
    out["named_symbols"] = sorted(named)[:12]
    by_path = {}
    for e in evidence:
        if e.get("is_test"):
            continue
        by_path.setdefault(e["path"], []).append(e["file"])
    rank = {"named": 3, "package": 2, "import_only": 1, "unknown": 0}
    best = "unknown"; seen_syms = []; surf_ev = []
    for p, files in by_path.items():
        refs = set(); ref_locs = []; local = None
        for rel in dict.fromkeys(files):
            try:
                with open(os.path.join(repo_root, rel), "r", errors="replace") as fh:
                    src = fh.read()
            except (IOError, OSError):
                continue
            ln = _go_local_name(p, src); local = local or ln
            for m in _dbr_re.finditer(_dbr_re.escape(ln) + r"\.([A-Z][A-Za-z0-9_]*)", src):
                sym = m.group(1); refs.add(sym)
                ref_locs.append((sym, rel, src.count(chr(10), 0, m.start()) + 1))
        if not refs:
            kind = "import_only"
        elif refs & named:
            kind = "named"
        else:
            kind = "package"
        out["surface_by_path"][p] = {"kind": kind, "local": local, "symbols": sorted(refs)[:12]}
        if rank[kind] > rank[best]:
            best = kind
        for sym, rel, line_no in ref_locs:
            is_named = sym in named
            if kind == "named" and not is_named:
                continue
            surf_ev.append({"path": p, "symbol": sym, "file": rel, "line": str(line_no), "named": is_named})
        seen_syms.extend(sorted(refs))
    seen = set(); ded = []
    for ev in surf_ev:
        k = (ev["path"], ev["symbol"])
        if k in seen:
            continue
        seen.add(k); ded.append(ev)
    ded.sort(key=lambda e: (e["path"] not in (text or ""), not e["named"]))
    out["surface_kind"] = best
    out["surface_symbols"] = sorted(set(seen_syms))[:20]
    out["surface_evidence"] = ded[:8]
    return out

def _resolve_declared_break_reachability(pr_data, deterministic, eco):
    mr = pr_data.get("merge_risk") or {}
    evidence_axis = (mr.get("evidenceAxis") or "").lower()
    sig = (deterministic or {}).get("changelogSignal") or {}
    # Only the changelog-DECLARED-break High path (merge-risk evidenceAxis
    # "declared breaking change (changelog), behavior unverified") may be downgraded here. A High
    # driven by an independently CONFIRMED signal — "break-reachable API change", "runtime support
    # drop", "failed deterministic signal" — must NOT enter this resolver (it would wrongly become
    # Medium). So gate strictly on the declared-breaking axis, NOT the broad changelog status.
    is_declared = mr.get("tag") == "High" and "declared breaking change" in evidence_axis
    if not is_declared:
        return
    bullets = sig.get("bullets") or []
    # STRONG markers only: a genuine break, not a deprecation/additive note. This keeps us from
    # extracting incidental package names (e.g. an EMPTY-type deprecation) as the affected path.
    strong_re = _dbr_re.compile(r"breaking[\s-]?change|no longer|cardinalit|migration[\s-]?required|removed\s|signature|incompatible|default[s]?\s+(?:changed|now|of|to)", _dbr_re.I)
    breaking_bullets = [b for b in bullets if strong_re.search(b or "")]
    text = " \n ".join(breaking_bullets) if breaking_bullets else ((deterministic or {}).get("changelogText") or "")
    # Extract module/import-style paths (domain + at least one path segment).
    raw_paths = set(_dbr_re.findall(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.[A-Za-z]{2,}(?:/[A-Za-z0-9_.-]+)+", text))
    # Trailing sentence punctuation can attach to a path captured from prose (e.g.
    # "...exporters/prometheus. Previously" -> "...prometheus."), which then fails the
    # import grep. Strip trailing non-path punctuation so reachability is not falsely lost.
    raw_paths = {p.rstrip(".,;:)]'\"") for p in raw_paths}
    # npm scoped packages and bare python modules are less reliably named in prose; focus on
    # path-like identifiers, which covers Go module paths and npm scoped/url-style packages.
    reason_text = (mr.get("reason") or "")
    # Sort so paths named in the driving verdict reason are tried first.
    paths = sorted((p for p in raw_paths if "/" in p), key=lambda p: (p not in reason_text, p))[:8]
    repo_root = os.environ.get("REPO_ROOT") or "."
    ext_by_eco = {"gomod": ["*.go"], "npm": ["*.ts", "*.tsx", "*.js", "*.jsx", "*.mjs"], "pip": ["*.py"]}
    includes = ext_by_eco.get(eco, ["*.go", "*.ts", "*.js", "*.py"])
    evidence = []
    prod_reached = False
    test_only = False
    for p in paths:
        cmd = ["grep", "-rnE", "--binary-files=without-match"]
        for inc in includes:
            cmd.append("--include=" + inc)
        cmd += ["--exclude-dir=vendor", "--exclude-dir=node_modules", "--exclude-dir=.git",
                "(\"|')" + _dbr_re.escape(p) + "(\"|')", repo_root]
        try:
            out = _dbr_sub.run(cmd, capture_output=True, text=True, timeout=45)
        except Exception:
            continue
        for line in (out.stdout or "").splitlines():
            parts = line.split(":", 2)
            if len(parts) < 2:
                continue
            fpath = parts[0]
            rel = os.path.relpath(fpath, repo_root)
            is_test = bool(_dbr_re.search(r"(_test\.[a-z]+\Z|\.test\.[a-z]+\Z|/tests?/|/__tests__/|\.spec\.[a-z]+\Z)", rel))
            # Reachability decision must see ALL matches; only the DISPLAYED evidence list is capped,
            # so a production import that appears after the 12th match still flips prod_reached.
            if not is_test:
                prod_reached = True
            if len(evidence) < 12:
                evidence.append({"path": p, "file": rel, "line": parts[1].strip(), "is_test": is_test})
    if evidence and not prod_reached:
        test_only = True
    # NOTE on confidence: this resolver only runs for a changelog-DECLARED breaking change that the
    # deterministic API-diff did NOT flag (a real removed/changed symbol would have been caught
    # upstream as a reachable hard break — a different, higher-confidence path). So everything here
    # is a BEHAVIORAL declaration (changed defaults, error/ordering semantics) that build, tests, and
    # API-diff cannot see. We can prove the package is IMPORTED, but never that our code triggers the
    # changed behavior. Therefore we never claim a confirmed break: import-reachable behavioral
    # declarations are a manual-REVIEW signal (Medium), not High.
    if prod_reached:
        reachability_kind = "import"
    elif test_only:
        reachability_kind = "test_only"
    elif paths:
        reachability_kind = "not_imported"
    else:
        reachability_kind = "unresolved"
    result = {
        "checked": bool(paths),
        "affected_paths": paths,
        "prod_reachable": prod_reached,
        "test_only": test_only,
        "reachability_kind": reachability_kind,
        "behavior_confirmed": False,
        "evidence": evidence[:12],
    }
    # Refine import-level reachability into SURFACE-level exposure tiers (deterministic).
    try:
        result.update(_classify_behavioral_exposure(repo_root, paths, evidence, text, eco))
    except Exception as _exp_e:
        print("  behavioral-exposure classification skipped:", str(_exp_e)[:120])
    pr_data["declared_break_reachability"] = result
    # Adjust the verdict using the resolved reachability.
    if not paths:
        return
    if prod_reached:
        sk = result.get("surface_kind", "unknown")
        surf_ev = result.get("surface_evidence", [])
        proof = next((e for e in evidence if (not e["is_test"]) and e["path"] in (mr.get("reason") or "")), None)
        if not proof:
            proof = next((e for e in evidence if not e["is_test"]), None)
        loc = (" (" + proof["path"] + ")") if proof else ""
        mr["tag"] = "Medium"
        if sk == "named":
            sev = next((e for e in surf_ev if e.get("named")), None) or (surf_ev[0] if surf_ev else None)
            symloc = (" — your code calls %s at %s:%s" % (sev["symbol"], sev["file"], sev["line"])) if sev else ""
            mr["reason"] = ("review required: the changelog declares a BEHAVIORAL breaking change to a symbol your "
                            "production code calls directly" + symloc + "; build, tests, and API-diff cannot confirm "
                            "whether the changed behavior affects your usage — verify against the release notes")
            mr["evidenceAxis"] = "declared behavioral change on a directly-called symbol, unverified by build/test/api-diff"
        elif sk == "package":
            sev = surf_ev[0] if surf_ev else None
            local = (result.get("surface_by_path", {}).get(sev["path"], {}).get("local") or sev["path"].split("/")[-1]) if sev else ""
            symloc = (" (e.g. %s.%s at %s:%s)" % (local, sev["symbol"], sev["file"], sev["line"])) if sev else ""
            mr["reason"] = ("review required: the changelog declares a BEHAVIORAL breaking change inside a package your "
                            "production code uses" + loc + symloc + "; the change is internal to the package, so whether it "
                            "affects you depends on your runtime data/configuration — build, tests, and API-diff cannot "
                            "confirm or rule it out; verify against the release notes")
            mr["evidenceAxis"] = "declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff"
        elif sk == "import_only":
            mr["reason"] = ("review required: your production code imports the affected package" + loc + " but does not "
                            "appear to reference its exported surface (possibly a blank or transitive import); the changelog "
                            "declares a BEHAVIORAL change whose impact we cannot confirm or rule out — lower-risk, but verify "
                            "against the release notes")
            mr["evidenceAxis"] = "declared behavioral change, package imported but exported surface not referenced in production"
        else:
            mr["reason"] = ("review required: the changelog declares a BEHAVIORAL breaking change and your "
                            "code imports the affected package" + loc + ", but build, tests, and API-diff "
                            "cannot confirm or rule out that your usage triggers it — not a confirmed break; "
                            "verify against the release notes")
            mr["evidenceAxis"] = "declared behavioral change, import-reachable but unverified by build/test/api-diff"
    elif test_only:
        mr["tag"] = "Medium"
        mr["reason"] = "declared breaking change is only reachable from test/CI code: " + ", ".join(paths)
        mr["evidenceAxis"] = "declared breaking change, reachable only from non-production code"
    else:
        mr["tag"] = "Medium"
        mr["reason"] = "declared breaking change is in " + ", ".join(paths) + ", which your code does not import (not reachable)"
        mr["evidenceAxis"] = "declared breaking change, not reachable (package not imported)"
    pr_data["merge_risk"] = mr
    if isinstance(pr_data.get("deterministic"), dict) and isinstance(pr_data["deterministic"].get("merge_risk"), dict):
        pr_data["deterministic"]["merge_risk"] = mr
try:
    _resolve_declared_break_reachability(pr_data, deterministic, eco)
except Exception as _dbr_e:
    print("  declared-break reachability resolution skipped:", str(_dbr_e)[:120])

# ── Structured per-signal evidence ─────────────────────────────
def _tail_text(value, limit=4000):
    value = value or ""
    return value[-limit:] if len(value) > limit else value

def _read_scratch(name):
    try:
        with open(os.path.join("$BC_SCRATCH_DIR", name)) as f:
            return f.read()
    except (IOError, OSError):
        return ""

def _read_scratch_int(name):
    raw = _read_scratch(name).strip()
    try:
        return int(raw) if raw not in ("", "null", "None") else None
    except ValueError:
        return None

def _status_from_exit(exit_code):
    if exit_code is None:
        return "skipped"
    return "ran_pass" if exit_code == 0 else "ran_fail"

def _step_detail(step_names, default=""):
    for st in steps:
        if st.get("step") in step_names:
            detail = st.get("detail") or st.get("status") or default
            return str(detail)
    return default

def _ev(signal, label, status, command="", stdout="", exit_code=None, summary="", na_reason=""):
    return {
        "signal": signal,
        "label": label,
        "status": status,
        "command": command or "",
        "stdout": _tail_text(stdout),
        "exit_code": exit_code,
        "summary": summary or "",
        "na_reason": na_reason if status in ("n/a", "skipped") else "",
    }

evidence = []
dep_cmd = _read_scratch(f"_bc_evidence_dep_command_{pr_num}.txt").strip()
build_cmd = _read_scratch(f"_bc_evidence_build_command_{pr_num}.txt").strip()
test_cmd = _read_scratch(f"_bc_evidence_test_command_{pr_num}.txt").strip()
smoke_cmd = _read_scratch(f"_bc_evidence_smoke_command_{pr_num}.txt").strip()
usage_raw = _read_scratch(f"_bc_usage_raw_{pr_num}.txt")
cli_stdout = _read_scratch(f"_bc_cli_output_{pr_num}.txt")
npm_audit_stdout = _read_scratch(f"_bc_npm_audit_output_{pr_num}.txt")
smoke_stdout = _read_scratch(f"_bc_smoke_output_{pr_num}.txt")
smoke_exit_recorded = _read_scratch_int(f"_bc_smoke_exit_{pr_num}.txt")

go_resolution = pr_data.get("go_resolution", {}) if isinstance(pr_data.get("go_resolution"), dict) else {}
if eco == "gomod":
    dep_cmd = go_resolution.get("command") or dep_cmd or "go mod tidy"
    dep_out = go_resolution.get("output_tail") or ""
    dep_exit = _read_scratch_int(f"_bc_go_resolution_exit_{pr_num}.txt")
    if dep_cmd:
        evidence.append(_ev("dependency_resolution", "Dependency resolution", _status_from_exit(dep_exit), dep_cmd, dep_out, dep_exit, _step_detail({"dependency_resolution"}, "dependency resolution")))
    else:
        evidence.append(_ev("dependency_resolution", "Dependency resolution", "n/a", "", "", None, "dependency resolution not applicable", "no Go dependency resolution command recorded"))
elif eco == "npm":
    dep_out = build_output.split("--- tsc ---", 1)[0]
    dep_exit = int("$PR_INSTALL_EXIT") if "$PR_INSTALL_EXIT" not in ("", "-1") else None
    evidence.append(_ev("dependency_resolution", "Dependency resolution", _status_from_exit(dep_exit), dep_cmd or "npm ci --ignore-scripts", dep_out, dep_exit, _step_detail({"dependency_resolution"}, "dependency resolution")))
elif eco == "pip":
    dep_out = build_output.split("--- import check ---", 1)[0]
    dep_exit = 0 if install_ok else (pr_data.get("build", {}).get("pr_exit") if pr_data.get("build", {}).get("pr_exit") != -1 else None)
    if dep_cmd:
        evidence.append(_ev("dependency_resolution", "Dependency resolution", _status_from_exit(dep_exit), dep_cmd, dep_out, dep_exit, _step_detail({"dependency_resolution"}, "dependency resolution")))
    else:
        evidence.append(_ev("dependency_resolution", "Dependency resolution", "n/a", "", dep_out, None, "no Python dependency manifest found", "no requirements.txt, pyproject.toml, or poetry.lock found"))

# Build/type-check/import-check signal
build_exit = None
if eco == "npm":
    build_exit = int("$PR_TSC_EXIT") if "$PR_TSC_EXIT" not in ("", "-1") else None
elif eco in ("gomod", "pip"):
    build_exit = pr_data.get("build", {}).get("pr_exit")
    build_exit = None if build_exit == -1 else build_exit
if build_cmd:
    evidence.append(_ev("build", "Build", _status_from_exit(build_exit), build_cmd, build_output, build_exit, _step_detail({"type_check"}, build_verdict)))
else:
    reason = "no tsconfig.json" if eco == "npm" else ("Go unavailable or build skipped" if eco == "gomod" else "Python import check not run")
    evidence.append(_ev("build", "Build", "n/a", "", build_output, build_exit, _step_detail({"type_check"}, "build not run"), reason))

# API diff and usage scan come from the deterministic pipeline and shell grep scan.
if eco in ("gomod", "npm", "pip"):
    if deterministic:
        api_changes = len(deterministic.get("api_changes_detail", deterministic.get("apiChanges", [])) or [])
        compatible = deterministic.get("verification", {}).get("compatible")
        status = "ran_fail" if compatible is False else "ran_pass"
        evidence.append(_ev("api_diff", "API diff", status, "node .github/actions/breakability-check/index.js --json", cli_stdout, 0, f"api_changes={api_changes}, compatible={compatible}"))
    else:
        evidence.append(_ev("api_diff", "API diff", "skipped", "node .github/actions/breakability-check/index.js --json", cli_stdout, None, "pipeline output unavailable", "pipeline skipped or produced no JSON"))
    usage_cmd = {"npm": "scan_usage_npm", "gomod": "scan_usage_go", "pip": "scan_usage_pip"}.get(eco, "")
    evidence.append(_ev("usage_scan", "Usage scan", "ran_pass", usage_cmd, usage_raw, 0, f"{len(files_importing)} importing file(s) found"))

# Vulnerability scan evidence: npm audit for npm, govulncheck for Go, none for pip.
if eco == "npm":
    if npm_audit_stdout:
        audit_status = "ran_fail" if ($AUDIT_CRITICAL > 0 or $AUDIT_HIGH > 0) else "ran_pass"
        evidence.append(_ev("vuln_scan", "Vulnerability scan", audit_status, "npm audit --json --production", npm_audit_stdout, 0, f"critical={pr_data['npm_audit']['critical']}, high={pr_data['npm_audit']['high']}"))
    else:
        evidence.append(_ev("vuln_scan", "Vulnerability scan", "skipped", "npm audit --json --production", "", None, "npm audit not run", "dependency installation failed or npm audit skipped"))
elif eco == "gomod":
    if vuln_status in ("skipped_disabled",):
        evidence.append(_ev("vuln_scan", "Vulnerability scan", "skipped", "govulncheck ./...", vuln_output, None, "govulncheck disabled", "govulncheck disabled by config"))
    elif vuln_status in ("not_installed",):
        evidence.append(_ev("vuln_scan", "Vulnerability scan", "n/a", "govulncheck ./...", vuln_output, None, "govulncheck unavailable", "govulncheck tool unavailable"))
    else:
        vuln_ev_status = "ran_pass" if vuln_status in ("ok", "ok_preexisting") else "ran_fail"
        evidence.append(_ev("vuln_scan", "Vulnerability scan", vuln_ev_status, "govulncheck ./...", vuln_output, None, vuln_status))
elif eco == "pip":
    evidence.append(_ev("vuln_scan", "Vulnerability scan", "n/a", "", "", None, "Python vulnerability scan not configured", "no Python vulnerability scanner configured"))

# Test and smoke evidence.
if test_ran_val:
    if eco == "gomod" and no_go_tests:
        evidence.append(_ev("tests", "Tests", "n/a", test_cmd or "go test ./...", test_output, test_exit_val, _step_detail({"test_suite"}, "no Go test files present"), "no Go test files present"))
    else:
        evidence.append(_ev("tests", "Tests", _status_from_exit(test_exit_val), test_cmd, test_output, test_exit_val, _step_detail({"test_suite"}, "tests ran")))
else:
    evidence.append(_ev("tests", "Tests", "skipped", test_cmd, test_output, None, _step_detail({"test_suite"}, "tests not triggered"), "not triggered"))

if eco == "npm":
    if smoke_ran_val:
        evidence.append(_ev("smoke", "Smoke probe", _status_from_exit(smoke_exit_recorded if smoke_exit_recorded is not None else smoke_exit_val), smoke_cmd, smoke_stdout, smoke_exit_recorded if smoke_exit_recorded is not None else smoke_exit_val, _step_detail({"smoke_probe"}, "smoke probe ran")))
    else:
        evidence.append(_ev("smoke", "Smoke probe", "skipped", smoke_cmd, smoke_stdout, None, _step_detail({"smoke_probe"}, "smoke probe not run"), "not triggered or no dist entrypoint"))

pr_data["evidence"] = evidence

pr_data["verification_steps"] = steps

data["prs"][pr_num] = pr_data

_tmp = results_file + ".tmp"
with open(_tmp, "w") as f:
    json.dump(data, f, indent=2)
os.rename(_tmp, results_file)

print(f"  ✓ PR #{pr_num} written to results")

# Cleanup temp files
for p in [det_path, files_path, build_out_path, test_out_path, new_errors_path]:
    try:
        os.remove(p)
    except (FileNotFoundError, OSError):
        pass
PYEOF

  cd "$REPO_ROOT"
done

# Clean up main worktree (kept alive for lazy baselines during PR processing)
git worktree remove "$MAIN_DIR" --force 2>/dev/null || { chmod -R u+w "$MAIN_DIR" 2>/dev/null; rm -rf "$MAIN_DIR" 2>/dev/null; } || true

# ── In batch mode, skip cross-PR / security / cleanup (merge script handles those) ──
if [[ -n "$BATCH_ID" ]]; then
  # Embed main baseline vuln scan into batch JSON so merge-results.sh can aggregate it
  RESULTS_FILE="$RESULTS_FILE" python3 <<'BATCHVULN'
import json, os
rf = os.environ["RESULTS_FILE"]
try:
    with open(rf) as f: d = json.load(f)
except Exception: d = {}
mb = {"status": "unknown", "findings": []}
try:
    if os.path.exists("/tmp/_bc_main_vuln_status.txt"):
        mb["status"] = open("/tmp/_bc_main_vuln_status.txt").read().strip() or "unknown"
    if os.path.exists("/tmp/_bc_main_vuln_findings.txt"):
        mb["findings"] = sorted(set(l.strip() for l in open("/tmp/_bc_main_vuln_findings.txt") if l.strip()))
except Exception as e:
    mb["error"] = str(e)
d["main_baseline_vuln"] = mb
with open(rf + ".tmp", "w") as f: json.dump(d, f, indent=2)
os.rename(rf + ".tmp", rf)
print(f"  [batch] main_baseline_vuln: status={mb['status']} findings={len(mb['findings'])}")
BATCHVULN
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

RESULTS_FILE="$RESULTS_FILE" python3 << 'CROSSDEPS'
import json, re, os
results_file = os.environ["RESULTS_FILE"]

KNOWN_DEPS = {
    ("flask", "jinja2"): ("flask depends on jinja2", "jinja2 first"),
    ("flask", "werkzeug"): ("flask depends on werkzeug", "werkzeug first"),
    ("requests", "urllib3"): ("requests depends on urllib3", "urllib3 first"),
    ("requests", "certifi"): ("requests depends on certifi", "certifi first"),
    ("express", "@types/express"): ("types follow express", "express first"),
    ("lodash", "@types/lodash"): ("types follow lodash", "lodash first"),
    ("jsonwebtoken", "@types/jsonwebtoken"): ("types follow jsonwebtoken", "jsonwebtoken first"),
    ("react", "react-dom"): ("react and react-dom must match", "merge together"),
    ("react", "@types/react"): ("types follow react", "react first"),
    ("react-dom", "@types/react-dom"): ("types follow react-dom", "react-dom first"),
    # K8s ecosystem: these packages must always be upgraded to the same version together
    ("k8s.io/client-go", "k8s.io/apimachinery"): ("K8s module coordination: k8s.io/apimachinery + k8s.io/client-go must match versions", "merge together"),
    ("k8s.io/client-go", "k8s.io/api"): ("K8s module coordination: k8s.io/api + k8s.io/client-go must match versions", "merge together"),
    ("k8s.io/apimachinery", "k8s.io/api"): ("K8s module coordination: k8s.io/apimachinery + k8s.io/api must match versions", "merge together"),
    # OpenTelemetry: core packages should be upgraded together
    ("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/sdk"): ("OTel coordination: core + SDK should match", "merge together"),
    ("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/trace"): ("OTel coordination: core + trace should match", "merge together"),
    ("go.opentelemetry.io/otel", "go.opentelemetry.io/otel/metric"): ("OTel coordination: core + metric should match", "merge together"),
    ("go.opentelemetry.io/otel/sdk", "go.opentelemetry.io/otel/trace"): ("OTel coordination: SDK + trace should match", "merge together"),
    ("go.opentelemetry.io/otel/sdk", "go.opentelemetry.io/otel/metric"): ("OTel coordination: SDK + metric should match", "merge together"),
    ("go.opentelemetry.io/otel/trace", "go.opentelemetry.io/otel/metric"): ("OTel coordination: trace + metric should match", "merge together"),
}
try:
    with open("/tmp/_bc_peer_groups.json") as f: pd = json.load(f)
    for i, a in enumerate(pd.get("nestjs_group", [])):
        for b in pd.get("nestjs_group", [])[i+1:]:
            KNOWN_DEPS.setdefault((a, b), (f"NestJS peer group: {a} + {b}", "merge together"))
    for i, a in enumerate(pd.get("react_group", [])):
        for b in pd.get("react_group", [])[i+1:]:
            KNOWN_DEPS.setdefault((a, b), (f"React peer group: {a} + {b}", "merge together"))
    for pn, pl in pd.get("peer_groups", {}).items():
        for peer in pl:
            key = tuple(sorted([pn.lower(), peer.lower()]))
            KNOWN_DEPS.setdefault(key, (f"{pn} peerDep on {peer}", "check compatibility"))
except FileNotFoundError:
    pass
except json.JSONDecodeError as e:
    import sys
    print(f"WARNING: corrupt peer groups JSON: {e}", file=sys.stderr)
    pass
with open(results_file) as f: data = json.load(f)
cross_deps = []
prs = data.get("prs", {})
pr_list = list(prs.items())
for i, (na, pa) in enumerate(pr_list):
    for nb, pb in pr_list[i+1:]:
        a, b = pa.get("package", "").lower(), pb.get("package", "").lower()
        for (da, db), (reason, order) in KNOWN_DEPS.items():
            if (a == da and b == db) or (a == db and b == da):
                cross_deps.append({"pr_a": int(na), "pr_b": int(nb), "reason": reason, "merge_order": order})
nestjs_prs = {}
for num, pr in prs.items():
    if pr.get("package", "").startswith("@nestjs/"):
        nestjs_prs.setdefault(pr.get("pkg_dir", "/"), []).append((num, pr["package"]))
for pkg_dir, entries in nestjs_prs.items():
    if len(entries) > 1:
        for i, (na, pa) in enumerate(entries):
            for nb, pb in entries[i+1:]:
                if not any((d["pr_a"]==int(na) and d["pr_b"]==int(nb)) or (d["pr_a"]==int(nb) and d["pr_b"]==int(na)) for d in cross_deps):
                    cross_deps.append({"pr_a": int(na), "pr_b": int(nb), "reason": f"NestJS in {pkg_dir}: {pa} + {pb} must upgrade together", "merge_order": "merge together"})
try:
    with open("/tmp/_bc_workspace_graph.json") as f: graph = json.load(f)
    for num, pr in prs.items():
        pd = pr.get("pkg_dir", "/")
        if pd.startswith("lib/"):
            pkg_name = next((n for n, i in graph.get("packages",{}).items() if i["path"]==pd), None)
            if pkg_name:
                consumers = graph.get("consumers",{}).get(pkg_name, [])
                if not consumers:
                    for k, v in graph.get("consumers",{}).items():
                        if k.lower()==pkg_name.lower(): consumers=v; break
                for c in consumers:
                    for nb, pb in prs.items():
                        if nb!=num and pb.get("pkg_dir")==c["path"] and pb.get("package")==pr.get("package"):
                            if not any((d["pr_a"]==int(num) and d["pr_b"]==int(nb)) or (d["pr_a"]==int(nb) and d["pr_b"]==int(num)) for d in cross_deps):
                                cross_deps.append({"pr_a": int(num), "pr_b": int(nb), "reason": f"Shared lib cascade: {pkg_name} ({pd}) consumed by {c['service']}", "merge_order": f"lib first, then {c['path']}"})
    data["workspace_graph"] = graph
    data["nestjs_skew"] = graph.get("nestjs_skew", [])
except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
    data["workspace_graph"] = {}
    data["nestjs_skew"] = []
data["cross_pr_deps"] = cross_deps
_tmp = results_file + ".tmp"
with open(_tmp, "w") as f: json.dump(data, f, indent=2)
os.rename(_tmp, results_file)
if cross_deps:
    for dep in cross_deps: print(f"  Found: PR #{dep['pr_a']} <-> #{dep['pr_b']} - {dep['reason']}")
else: print("  No cross-PR dependencies detected")
CROSSDEPS

# ── Security posture scan ────────────────────────────────────────────────────
echo ""
echo "════════════ SECURITY POSTURE ════════════"
python3 << SECURITYEOF
import json, subprocess, os

owner_repo = "$OWNER_REPO"

# Fetch Dependabot vulnerability alerts from GitHub API
# V9.8 iter6: use BREAKABILITY_PAT if provided (fine-grained PAT with Dependabot-alerts:read).
# The default GITHUB_TOKEN cannot list alerts without org-level security-events.
import os as _sec_os
_alerts_env = _sec_os.environ.copy()
if _sec_os.environ.get("BREAKABILITY_PAT"):
    _alerts_env["GH_TOKEN"] = _sec_os.environ["BREAKABILITY_PAT"]
    _alerts_env["GITHUB_TOKEN"] = _sec_os.environ["BREAKABILITY_PAT"]
try:
    result = subprocess.run(
        ["gh", "api", f"repos/{owner_repo}/dependabot/alerts",
         "--jq", '.[] | {number, state, security_advisory: {ghsa_id: .security_advisory.ghsa_id, cve_id: .security_advisory.cve_id, severity: .security_advisory.severity, summary: .security_advisory.summary}, security_vulnerability: {first_patched_version: .security_vulnerability.first_patched_version.identifier, vulnerable_version_range: .security_vulnerability.vulnerable_version_range}, dependency: {package: .dependency.package.name, ecosystem: .dependency.package.ecosystem, manifest_path: .dependency.manifest_path}}',
         "-X", "GET", "--paginate"],
        capture_output=True, text=True, timeout=60, env=_alerts_env
    )
    if result.returncode != 0:
        print("  Could not fetch Dependabot alerts (may need security permissions)")
        print(f"  stderr: {(result.stderr or '')[:200]}")
        alerts_raw = "[]"
    else:
        # gh --jq with paginate outputs one JSON object per line
        lines = [l.strip() for l in result.stdout.strip().split('\n') if l.strip()]
        alerts = [json.loads(l) for l in lines]
        alerts_raw = json.dumps(alerts)
except Exception as e:
    print(f"  Security scan error: {e}")
    alerts = []
    alerts_raw = "[]"

try:
    alerts = json.loads(alerts_raw) if isinstance(alerts_raw, str) else alerts
except:
    alerts = []

# Count open vulnerabilities by severity
open_alerts = [a for a in alerts if a.get("state") == "open"]
severity_counts = {}
for a in open_alerts:
    sev = a.get("security_advisory", {}).get("severity", "unknown")
    severity_counts[sev] = severity_counts.get(sev, 0) + 1

# Cross-reference: which open PRs fix which alerts?
with open("$RESULTS_FILE") as f:
    data = json.load(f)

prs = data.get("prs", {})
pr_cves = {}
total_cve_count = 0
for num, pr in prs.items():
    cves = pr.get("cves", [])
    if cves:
        pr_cves[num] = cves
        total_cve_count += len(cves)

# Match alerts to PRs by package name
fixes_by_pr = {}
for num, pr in prs.items():
    pkg = pr.get("package", "")
    matching_alerts = [a for a in open_alerts
                       if a.get("dependency", {}).get("package", "") == pkg]
    if matching_alerts:
        fixes_by_pr[num] = {
            "package": pkg,
            "alert_count": len(matching_alerts),
            "severities": [a.get("security_advisory", {}).get("severity", "unknown") for a in matching_alerts],
            "cve_ids": [a.get("security_advisory", {}).get("cve_id") or a.get("security_advisory", {}).get("ghsa_id", "") for a in matching_alerts]
        }

# V9.8 iter6 (B): Precise alert↔PR matching using first_patched_version.
# A PR "fixes" an alert when its target version is >= first_patched_version for that alert.
def _parse_semver(v):
    if not v: return None
    s = str(v).lstrip("v").lstrip("=").strip()
    # Strip pre-release / build metadata for simple comparison
    for sep in ("-", "+"):
        if sep in s: s = s.split(sep, 1)[0]
    parts = s.split(".")
    try:
        return tuple(int(p) for p in parts[:3]) + (0,) * (3 - min(3, len(parts)))
    except ValueError:
        return None

def _semver_gte(a, b):
    pa, pb = _parse_semver(a), _parse_semver(b)
    if pa is None or pb is None: return False
    return pa >= pb

cve_fixes = []           # [{pr, package, cve_ids[], severities[], fixed_by_version}]
orphan_alerts = []       # [{cve_id, package, severity, summary}]  — alerts with no PR
matched_alert_ids = set()

for a in open_alerts:
    alert_pkg = a.get("dependency", {}).get("package", "")
    fpv = a.get("security_vulnerability", {}).get("first_patched_version")
    sev = a.get("security_advisory", {}).get("severity", "unknown")
    cve = a.get("security_advisory", {}).get("cve_id") or a.get("security_advisory", {}).get("ghsa_id", "")
    summary = a.get("security_advisory", {}).get("summary", "")
    alert_num = a.get("number")
    # Find any PR that upgrades this package to a fixed version
    matched = False
    for num, pr in prs.items():
        pr_pkg = pr.get("package", "")
        pr_to = pr.get("to", "")
        bumped = pr.get("bumped_modules") or {}
        # Resulting version of the ALERT's package after this PR merges:
        #   • primary bump  -> pr_to (the PR's own package == alert package)
        #   • transitive    -> the version this PR raised the alert package to in
        #                       go.sum (captured as bumped_modules). Only PRs that
        #                       actually RAISED the module are credited, so we don't
        #                       over-credit every PR that merely pins it at a safe ver.
        if pr_pkg == alert_pkg:
            resulting_ver = pr_to
            via = "primary"
        elif alert_pkg in bumped:
            resulting_ver = bumped[alert_pkg]
            via = "transitive"
        else:
            continue
        # Gate on the ACTUAL fixed-in version vs the resulting version. A bump that
        # lands BELOW first_patched_version (e.g. otel/sdk 1.42 vs a CVE fixed in
        # 1.43) does NOT deliver the fix and must fall through to the orphan list.
        if fpv and _semver_gte(resulting_ver, fpv):
            cve_fixes.append({
                "pr": int(num) if str(num).isdigit() else num,
                "package": alert_pkg,
                "cve_id": cve,
                "severity": sev,
                "from_version": pr.get("from", ""),
                "to_version": resulting_ver,
                "first_patched_version": fpv,
                "via": via,
                "summary": summary[:200],
            })
            matched_alert_ids.add(alert_num)
            matched = True
    if not matched:
        orphan_alerts.append({
            "cve_id": cve,
            "package": alert_pkg,
            "severity": sev,
            "first_patched_version": fpv or "unknown",
            "summary": summary[:200],
        })

# Rank severities for sorting
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "moderate": 2, "low": 3, "unknown": 4}
cve_fixes.sort(key=lambda x: (_SEV_ORDER.get((x["severity"] or "").lower(), 4), x.get("pr", 9999)))
orphan_alerts.sort(key=lambda x: _SEV_ORDER.get((x["severity"] or "").lower(), 4))

security_posture = {
    "total_open_alerts": len(open_alerts),
    "severity_counts": severity_counts,
    "total_cves_in_prs": total_cve_count,
    "prs_fixing_alerts": fixes_by_pr,
    "prs_with_cves": pr_cves,
    "alerts_fixable_by_merging": sum(f["alert_count"] for f in fixes_by_pr.values()),
    "cve_fixes": cve_fixes,                 # NEW: precise version-matched CVE fixes
    "orphan_alerts": orphan_alerts,         # NEW: open alerts with no corresponding PR
}

# ── govulncheck aggregates (V9.7): main baseline + per-PR findings ──
import os as _os
_govuln_block = {"main_baseline": {"status": "unknown", "findings": []}, "prs_scanned": 0, "prs_with_new_vulns": 0, "total_new_findings": set()}
try:
    if _os.path.exists("/tmp/_bc_main_vuln_status.txt"):
        with open("/tmp/_bc_main_vuln_status.txt") as f:
            _govuln_block["main_baseline"]["status"] = f.read().strip() or "unknown"
    if _os.path.exists("/tmp/_bc_main_vuln_findings.txt"):
        with open("/tmp/_bc_main_vuln_findings.txt") as f:
            _govuln_block["main_baseline"]["findings"] = sorted(set(l.strip() for l in f.readlines() if l.strip()))
    # Aggregate per-PR new findings from the data dict we just built
    for _pn, _pr in data.get("prs", {}).items():
        _vs = _pr.get("vuln_status", "")
        if _vs in ("ok", "vulns_found", "ok_preexisting"):
            _govuln_block["prs_scanned"] += 1
        _new = _pr.get("vuln_new_findings", [])
        if _new:
            _govuln_block["prs_with_new_vulns"] += 1
            for _f in _new:
                _govuln_block["total_new_findings"].add(_f)
    _govuln_block["total_new_findings"] = sorted(_govuln_block["total_new_findings"])
except Exception as _e:
    _govuln_block["error"] = str(_e)

data["security_posture"] = security_posture
data["govulncheck"] = _govuln_block
_tmp = "$RESULTS_FILE" + ".tmp"
with open(_tmp, "w") as f:
    json.dump(data, f, indent=2)
os.rename(_tmp, "$RESULTS_FILE")

print(f"  Open vulnerability alerts: {len(open_alerts)}")
for sev, count in sorted(severity_counts.items(), key=lambda x: {'critical':0,'high':1,'medium':2,'low':3}.get(x[0],4)):
    print(f"    {sev}: {count}")
print(f"  PRs that fix known alerts: {len(fixes_by_pr)}")
print(f"  Alerts fixable by merging open PRs: {security_posture['alerts_fixable_by_merging']}")
if total_cve_count:
    print(f"  CVEs referenced in PR bodies: {total_cve_count}")
SECURITYEOF


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
