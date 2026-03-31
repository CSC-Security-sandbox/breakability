#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# build-check.sh — Deterministic JSON producer for breakability analysis
#
# Runs TS pipeline CLI + ecosystem-specific builds for each Dependabot PR,
# produces /tmp/build-results.json with structured analysis data.
# ──────────────────────────────────────────────────────────────────────────────
set -u

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
WORKTREE_BASE="/tmp/worktree"

# ── Private Registry Configuration ───────────────────────────────────────────
# Reads .github/breakability-config.yml and sets up .npmrc for private registries.
# This lets npm ci resolve private scoped packages without falling back to file: links.
BC_CONFIG="${REPO_ROOT}/.github/breakability-config.yml"
PRIVATE_REGISTRY_CONFIGURED=false
BC_MODE="advisory"

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

  python3 << SETUPREG
import json, os, sys

config = json.loads('''$config_json''')
target_dir = "$target_dir"

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
    # Simple fallback: just run the command without timeout
    timeout() { shift; "$@"; }
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
retry_cmd() {
  local max_attempts="$1"
  local base_delay="$2"
  shift 2
  local attempt=1
  local rc=0

  while [[ $attempt -le $max_attempts ]]; do
    if "$@"; then
      return 0
    fi
    rc=$?
    # Exit 124 = timeout(1) killed the process — retrying would just triple the wall time
    if [[ $rc -eq 124 ]]; then return $rc; fi
    if [[ $attempt -lt $max_attempts ]]; then
      local delay=$((base_delay * (2 ** (attempt - 1))))
      echo "  ⚠️  Command failed (attempt $attempt/$max_attempts), retrying in ${delay}s..." >&2
      sleep "$delay"
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
    echo "minor"
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
  # Check if only used in _test.go files
  local non_test_count
  non_test_count=$(grep -rn "\"$pkg" --include="*.go" . 2>/dev/null | grep -v "_test.go" | grep -v vendor/ | wc -l || echo "0")
  if [[ "$non_test_count" -eq 0 ]]; then
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
      if grep -q "// indirect" go.mod 2>/dev/null && grep "$pkg" go.mod | grep -q "// indirect"; then
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
  # Also scan for @types/X → X
  local scan_name="$pkg"
  [[ "$pkg" == @types/* ]] && scan_name="${pkg#@types/}"

  grep -rn "from ['\"]${scan_name}['\"/]\\|require(['\"]${scan_name}['\"/]" \
    --include="*.ts" --include="*.tsx" --include="*.js" --include="*.mjs" \
    src/ lib/ test/ 2>/dev/null | head -50 || true
}

scan_usage_go() {
  local pkg="$1"
  grep -rn "\"${pkg}" --include="*.go" . 2>/dev/null | grep -v vendor/ | head -50 || true
}

scan_usage_pip() {
  local pkg="$1"
  local import_name
  import_name=$(map_import_name "$pkg")
  grep -rn "from ${import_name} import\\|import ${import_name}" \
    --include="*.py" . 2>/dev/null | head -50 || true
}

format_usage_files() {
  # Takes grep output (file:line:content), outputs JSON array of "file:line"
  local input="$1"
  if [[ -z "$input" ]]; then
    echo "[]"
    return
  fi
  echo "$input" | awk -F: '{print "\"" $1 ":" $2 "\""}' | sort -u | \
    python3 -c "import sys,json; lines=sys.stdin.read().strip().split('\n'); print(json.dumps([l.strip('\"') for l in lines if l]))" 2>/dev/null || echo "[]"
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

check_cascade_impact() {
  local pkg_dir="$1"
  python3 -c "
import json
try:
    with open('/tmp/_bc_workspace_graph.json') as f: g = json.load(f)
    pn = next((n for n, i in g.get('packages',{}).items() if i['path']=='$pkg_dir'), None)
    if not pn: pn = next((n for n, i in g.get('packages',{}).items() if i['path'].lower()=='$pkg_dir'.lower()), None)
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
  
  python3 << REWRITEEOF
import json, os, glob

build_dir = "$build_dir"
worktree = "$worktree"
pkg_path = os.path.join(build_dir, "package.json")

with open(pkg_path) as f:
    pkg = json.load(f)

changed = False
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
                    changed = True
                    print(f"  rewrite: {name} -> file:{rel}")
                    break
            except:
                pass

if changed:
    with open(pkg_path, "w") as f:
        json.dump(pkg, f, indent=2)
    print(f"  {sum(1 for d in ('dependencies','devDependencies') for _ in [] )} deps rewritten")
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
  --json number,title,headRefName,body,labels --limit 500)

PR_COUNT=$(echo "$PR_JSON" | jq length)
echo "Found $PR_COUNT open Dependabot PRs"

# Apply PR_FILTER if set (comma-separated list of PR numbers to analyze)
if [[ -n "${PR_FILTER:-}" ]]; then
  echo "PR_FILTER set: $PR_FILTER"
  FILTERED_JSON=$(echo "$PR_JSON" | python3 -c "
import json, sys
prs = json.load(sys.stdin)
allowed = set('${PR_FILTER}'.replace(' ', '').split(','))
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

# ── Fetch all branches ───────────────────────────────────────────────────────
echo ""
echo "Fetching remote branches..."
git fetch --all --prune --quiet 2>/dev/null || true

# ── Baseline builds on main ──────────────────────────────────────────────────
echo ""
echo "════════════ BASELINE BUILDS (main) ════════════"

MAIN_DIR="${WORKTREE_BASE}-main"
rm -rf "$MAIN_DIR" 2>/dev/null || true
git worktree add "$MAIN_DIR" origin/main --quiet 2>/dev/null || \
  git worktree add "$MAIN_DIR" main --quiet 2>/dev/null || \
  cp -r "$REPO_ROOT" "$MAIN_DIR"

main_npm_exit="-1"
main_npm_install_exit="-1"
main_npm_tsc_exit="-1"
main_npm_output=""
main_go_exit="-1"
main_go_output=""
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

# Go baseline — detect go.work (multi-module workspace) or go.mod (single module)
if [[ -f "$MAIN_DIR/go.work" ]]; then
  echo "  go: workspace (go.work) detected, syncing..."
  main_go_output=$(cd "$MAIN_DIR" && {
    # Bug fix: && ensures go build is skipped if go work sync fails (Bug 5).
    # _BUILD_RC captures go build exit so go vet warnings don't clobber it (Bug 3).
    _BUILD_RC=0
    retry_cmd 3 5 go work sync && {
      timeout $TIMEOUT go build -o /dev/null ./... || _BUILD_RC=$?
      if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
      exit $_BUILD_RC
    }
  } 2>&1)
  main_go_exit=$?
  echo "  go baseline (workspace): exit=$main_go_exit"
elif [[ -f "$MAIN_DIR/go.mod" ]]; then
  echo "  go: verifying + building..."
  # Supply chain check first, then build + vet
  main_go_output=$(cd "$MAIN_DIR" && {
    go mod verify 2>&1 || echo "WARNING: go mod verify failed"
    # _BUILD_RC captures go build exit so go vet warnings don't clobber it (Bug 3).
    _BUILD_RC=0
    retry_cmd 3 5 go mod tidy && {
      timeout $TIMEOUT go build -o /dev/null ./... || _BUILD_RC=$?
      if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
      exit $_BUILD_RC
    }
  } 2>&1)
  main_go_exit=$?
  echo "  go baseline: exit=$main_go_exit"
fi

# Go baseline test — run with -race so pre-existing race failures don't look like PR regressions
if [[ "$main_go_exit" -eq 0 ]]; then
  echo "  go: running baseline tests with -race (comparison baseline for PR test runs)..."
  main_go_test_output=$(cd "$MAIN_DIR" && timeout 300 go test -race -timeout 5m ./... 2>&1)
  main_go_test_exit=$?
  echo "  go baseline test: exit=$main_go_test_exit"
fi

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
        retry_cmd 3 5 "$_PY_PIP_MAIN" install poetry --quiet 2>&1
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
    except:
        return ""

data["main_build"] = {
    "npm": {"exit": $main_npm_exit, "output_tail": read_output("/tmp/_bc_main_npm.txt")},
    "go": {"exit": $main_go_exit, "test_exit": $main_go_test_exit, "output_tail": read_output("/tmp/_bc_main_go.txt"), "test_output_tail": read_output("/tmp/_bc_main_go_test.txt")},
    "pip": {"exit": $main_pip_exit, "output_tail": read_output("/tmp/_bc_main_pip.txt")}
}

with open("$RESULTS_FILE", "w") as f:
    json.dump(data, f, indent=2)
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
    python3 << SKIPEOF
import json
results_file = "$RESULTS_FILE"
pr_num = "$PR_NUM"
pr_title = _skip_title = """$_SKIP_TITLE""".replace('\\\\', '\\\\').strip()
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
    "skip_reason": "breakability:skip label"
}
with open(results_file, "w") as f:
    json.dump(data, f, indent=2)
print(f"  ✓ PR #{pr_num} written (skipped)")
SKIPEOF
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


  # Skip non-Dependabot PRs (safety guard — label filter should catch these)
  if [[ "$PR_BRANCH" != dependabot/* ]]; then
    echo "  ⏭️  SKIP — not a Dependabot branch: $PR_BRANCH"
    continue
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

  if [[ "$PR_TITLE" =~ Bump[[:space:]]+(.+)[[:space:]]+from[[:space:]]+([^ ]+)[[:space:]]+to[[:space:]]+([^ ]+) ]]; then
    PKG="${BASH_REMATCH[1]}"
    FROM_VER="${BASH_REMATCH[2]}"
    TO_VER="${BASH_REMATCH[3]}"
  elif [[ "$PR_TITLE" =~ Bump[[:space:]]+(.+)[[:space:]]+and[[:space:]]+(.*) ]]; then
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
    gomod)   DEP_TYPE=$(detect_dep_type_go "$PKG") ;;
    pip)     DEP_TYPE="production" ;;
    actions) DEP_TYPE="dev" ;;
    docker)  DEP_TYPE="production" ;;
    maven)   DEP_TYPE="production" ;;

  esac
  echo "  dep_type: $DEP_TYPE"

  # Dep relation
  DEP_RELATION=$(detect_dep_relation "$ECOSYSTEM" "$PKG")
  echo "  dep_relation: $DEP_RELATION"

  # Security / CVEs
  CVES=$(extract_cves "$PR_BODY")
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
    gomod) USAGE_RAW=$(scan_usage_go "$PKG") ;;
    pip)   USAGE_RAW=$(scan_usage_pip "$PKG") ;;
  esac
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
        gomod) EXTRA_RAW=$(scan_usage_go "$EXTRA_PKG") ;;
        pip)   EXTRA_RAW=$(scan_usage_pip "$EXTRA_PKG") ;;
      esac
      EXTRA_FILES=$(format_usage_files "$EXTRA_RAW")
      EXTRA_COUNT=$(echo "$EXTRA_FILES" | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "0")
      echo "  additional pkg $EXTRA_PKG: $EXTRA_COUNT import sites"
      # Merge into ADDITIONAL_IMPORTS as {"package": "...", "files": [...]}
      ADDITIONAL_IMPORTS=$(python3 -c "
import json, sys
existing = json.loads('$ADDITIONAL_IMPORTS')
files = json.loads('''$EXTRA_FILES''')
existing.append({'package': '$EXTRA_PKG', 'files': files, 'count': len(files)})
print(json.dumps(existing))
" 2>/dev/null || echo "$ADDITIONAL_IMPORTS")
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
    with open('/tmp/build-results.json') as f: data = json.load(f)
    nestjs = pg.get('nestjs_group', [])
    pkg = '$PKG'
    if pkg in nestjs:
        others = [f'#{n} ({p["package"]})' for n, p in data.get('prs',{}).items() if p.get('package','').startswith('@nestjs/') and p['package'] != pkg]
        if others: print('NestJS peer group: upgrade ' + pkg + ' with: ' + ', '.join(others[:5]))
except: pass
" 2>/dev/null || true)
    [[ -n "$NESTJS_PEER_WARNING" ]] && echo "  $NESTJS_PEER_WARNING"
  fi


  # ── Run TS pipeline CLI (for npm/gomod/pip) ────────────────────
  DETERMINISTIC="{}"
  if [[ "$ECOSYSTEM" == "npm" || "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "pip" ]] && [[ -n "$PKG" && -n "$FROM_VER" && -n "$TO_VER" ]]; then
    echo "  running TS pipeline..."
    CLI_ECO="$ECOSYSTEM"

    # CLI sends logs to stdout mixed with JSON.  Capture all stdout,
    # then extract only the JSON portion (from first '{' to end).
    CLI_OUTPUT_FILE="/tmp/_bc_cli_${PR_NUM}.raw"
    CLI_JSON_FILE="/tmp/_bc_cli_${PR_NUM}.json"
    timeout 180 node "$CLI_PATH" \
      -p "$PKG" -f "$FROM_VER" -t "$TO_VER" \
      -r "$REPO_ROOT" -e "$CLI_ECO" -d "$DEP_TYPE" \
      --json > "$CLI_OUTPUT_FILE" 2>/dev/null || true

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
  'confidence': data.get('confidence', 'UNVERIFIED'),
  'adapter': data.get('adapterUsed', 'unknown'),
  'security': data.get('securityUpdate', None)
}
print(json.dumps(result))
" 2>/dev/null || echo "{}")
      echo "  pipeline: classification=$(echo "$DETERMINISTIC" | python3 -c "import json,sys; print(json.load(sys.stdin).get('classification','?'))" 2>/dev/null || echo "?")"
    else
      echo "  pipeline: failed to parse CLI output"
      DETERMINISTIC="{}"
    fi
    rm -f "$CLI_OUTPUT_FILE" "$CLI_JSON_FILE"
  else
    echo "  pipeline: skipped ($ECOSYSTEM)"
  fi

  # ── Build check on PR branch ───────────────────────────────────
  BUILD_EXIT="-1"
  BUILD_OUTPUT=""
  BUILD_VERDICT="skip"
  PR_WORKTREE="${WORKTREE_BASE}-${PR_NUM}"
  AUDIT_CRITICAL=0
  AUDIT_HIGH=0

  if [[ "$BUILD_VERDICT" == "conflict" ]]; then
    echo "  Skipping build — PR has merge conflicts"
  elif [[ "$ECOSYSTEM" == "npm" || "$ECOSYSTEM" == "gomod" || "$ECOSYSTEM" == "pip" ]]; then
    rm -rf "$PR_WORKTREE" 2>/dev/null || true
    git worktree add "$PR_WORKTREE" "origin/$PR_BRANCH" --quiet 2>/dev/null || {
      echo "  worktree: failed to create for $PR_BRANCH"
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
              if [[ $? -eq 0 ]]; then
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
              if [[ $? -eq 0 ]]; then
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
            AUDIT_CRITICAL=$(echo "$AUDIT_JSON" | jq -r '.metadata.vulnerabilities.critical // 0' 2>/dev/null || echo 0)
            AUDIT_HIGH=$(echo "$AUDIT_JSON" | jq -r '.metadata.vulnerabilities.high // 0' 2>/dev/null || echo 0)
            [[ "$AUDIT_CRITICAL" -gt 0 || "$AUDIT_HIGH" -gt 0 ]] && echo "  npm audit: ${AUDIT_CRITICAL} critical, ${AUDIT_HIGH} high"
          fi

          if [[ "$PR_INSTALL_EXIT" -eq 0 && -f "$BUILD_DIR/tsconfig.json" ]]; then
            TSC_OUT=$(cd "$BUILD_DIR" && timeout $TIMEOUT npx tsc --noEmit 2>&1)
            PR_TSC_EXIT=$?
            BUILD_EXIT=$PR_TSC_EXIT
            BUILD_OUTPUT="$BUILD_OUTPUT
--- tsc ---
$TSC_OUT"
          fi
          ;;
        gomod)
          # Check for go.work workspace file (multi-module monorepo)
          if [[ -f "$PR_WORKTREE/go.work" ]]; then
            echo "  build: go.work workspace detected — go work sync + mod verify + build + vet..."
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
              # Bug fix: && ensures build is skipped if go work sync fails.
              # _BUILD_RC captures go build exit so go vet warnings don't clobber BUILD_EXIT.
              _BUILD_RC=0
              retry_cmd 3 5 go work sync && {
                echo "--- go mod verify (workspace modules) ---"
                grep -E '^use ' go.work | awk '{print $2}' | while IFS= read -r _ws_mod; do
                  if [[ -n "$_ws_mod" ]]; then
                    echo "  verifying module: $_ws_mod"
                    (cd "$_ws_mod" && timeout 30 go mod verify 2>&1) || echo "  ⚠️  go mod verify FAILED for $_ws_mod"
                  fi
                done
                timeout $TIMEOUT go build -o /dev/null ./... || _BUILD_RC=$?
                if [[ $_BUILD_RC -eq 0 ]]; then go vet ./... 2>&1 || true; fi
                exit $_BUILD_RC
              }
            } 2>&1)
            BUILD_EXIT=$?
            [[ "$BUILD_EXIT" -eq 0 ]] && INSTALL_OK="true"
          else
            echo "  build: go mod verify + tidy + build + vet..."
            GO_VERIFY_OUT=""
            GO_VERIFY_EXIT=0
            GO_VERIFY_OUT=$(cd "$PR_WORKTREE" && timeout 30 go mod verify 2>&1) || GO_VERIFY_EXIT=$?
            if [[ "$GO_VERIFY_EXIT" -ne 0 ]]; then
              echo "  ⚠ go mod verify FAILED (possible supply chain issue)"
            fi
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && { retry_cmd 3 5 go mod tidy && timeout $TIMEOUT go build -o /dev/null ./...; } 2>&1)
            BUILD_EXIT=$?
            # Run go vet if build passed
            GO_VET_OUT=""
            if [[ "$BUILD_EXIT" -eq 0 ]]; then
              INSTALL_OK="true"
              GO_VET_OUT=$(cd "$PR_WORKTREE" && timeout 60 go vet ./... 2>&1) || true
              if [[ -n "$GO_VET_OUT" ]]; then
                echo "  go vet warnings found"
                BUILD_OUTPUT="$BUILD_OUTPUT
--- go vet ---
$GO_VET_OUT"
              fi
            fi
            # Append verify output
            if [[ "$GO_VERIFY_EXIT" -ne 0 ]]; then
              BUILD_OUTPUT="--- go mod verify (FAILED) ---
$GO_VERIFY_OUT
$BUILD_OUTPUT"
            fi
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
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_PR" install -r requirements.txt --quiet 2>&1)
          elif [[ -f "$PR_WORKTREE/pyproject.toml" ]]; then
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_PR" install -e . --quiet 2>&1)
          elif [[ -f "$PR_WORKTREE/poetry.lock" ]]; then
            BUILD_OUTPUT=$(cd "$PR_WORKTREE" && {
              retry_cmd 3 5 "$_PY_PIP_PR" install poetry --quiet 2>&1
              retry_cmd 3 5 "$_PY_PYTHON_PR" -m poetry install --quiet 2>&1
            })
          else
            BUILD_OUTPUT="No requirements.txt, pyproject.toml, or poetry.lock found"
          fi
          BUILD_EXIT=$?
          [[ "$BUILD_EXIT" -eq 0 ]] && INSTALL_OK="true"
          if [[ "$BUILD_EXIT" -eq 0 && -n "$PKG" ]]; then
            IMPORT_NAME=$(map_import_name "$PKG")
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
        main_dir_install_exit=$(cat "/tmp/_bc_main_npm_install_${dir_key}.txt" 2>/dev/null || echo "-1")
        main_dir_tsc_exit=$(cat "/tmp/_bc_main_npm_tsc_${dir_key}.txt" 2>/dev/null || echo "-1")
        main_npm_output=$(cat "/tmp/_bc_main_npm_out_${dir_key}.txt" 2>/dev/null || echo "")
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
            echo "$main_npm_output" | grep -oE 'error TS[0-9]+:.*' | sed "s|${WORKTREE_BASE}[^/]*/|./|g" | sort -u > "$MAIN_ERRORS_FILE" 2>/dev/null || true
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
          BUILD_VERDICT="pass"
        fi
      else
        MAIN_EXIT="-1"
        case "$ECOSYSTEM" in
          gomod) MAIN_EXIT=$main_go_exit ;;
          pip)   MAIN_EXIT=$main_pip_exit ;;
        esac

        if [[ "$BUILD_EXIT" -eq 0 ]]; then
          BUILD_VERDICT="pass"
        elif [[ "$MAIN_EXIT" -ne 0 && "$MAIN_EXIT" -ne -1 ]]; then
          # Both fail — check for new errors vs baseline
          if [[ "$ECOSYSTEM" == "gomod" ]]; then
            MAIN_ERR_FILE="/tmp/_bc_main_go_errors.txt"
            PR_ERR_FILE="/tmp/_bc_pr_go_errors_${PR_NUM}.txt"
            echo "$main_go_output" | grep -E '^.*\.go:[0-9]+' | sed "s|${WORKTREE_BASE}[^/]*/|./|g" | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
            echo "$BUILD_OUTPUT" | grep -E '^.*\.go:[0-9]+' | sed "s|${WORKTREE_BASE}[^/]*/|./|g" | sort -u > "$PR_ERR_FILE" 2>/dev/null || true
            NEW_ERRORS=$(comm -23 "$PR_ERR_FILE" "$MAIN_ERR_FILE" 2>/dev/null | head -10)
            rm -f "$MAIN_ERR_FILE" "$PR_ERR_FILE"
          elif [[ "$ECOSYSTEM" == "pip" ]]; then
            # Extract pip error lines (normalised) and diff against baseline
            MAIN_ERR_FILE="/tmp/_bc_main_pip_errors.txt"
            PR_ERR_FILE="/tmp/_bc_pr_pip_errors_${PR_NUM}.txt"
            echo "$main_pip_output" | grep -iE 'error|could not|no matching|conflict' | sort -u > "$MAIN_ERR_FILE" 2>/dev/null || true
            echo "$BUILD_OUTPUT" | grep -iE 'error|could not|no matching|conflict' | sort -u > "$PR_ERR_FILE" 2>/dev/null || true
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
      git worktree remove "$PR_WORKTREE" --force 2>/dev/null || rm -rf "$PR_WORKTREE"
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
      git worktree remove "$PR_WORKTREE" --force 2>/dev/null || rm -rf "$PR_WORKTREE"
    fi
  elif [[ "$ECOSYSTEM" == "docker" ]]; then
    echo "  build: Docker — extracting Dockerfile metadata"
    DOCKERFILE_PATH=""
    [[ "$PKG_DIR" != "/" && -f "$PKG_DIR/Dockerfile" ]] && DOCKERFILE_PATH="$PKG_DIR/Dockerfile"
    if [[ -n "$DOCKERFILE_PATH" ]]; then
      DOCKER_BASE=$(grep -m1 "^FROM" "$DOCKERFILE_PATH" 2>/dev/null | sed 's/^FROM //;s/ .*//')
      DOCKER_CMD=$(grep -E "^(CMD|ENTRYPOINT)" "$DOCKERFILE_PATH" 2>/dev/null | tail -1)
      echo "  docker: base=$DOCKER_BASE cmd=$DOCKER_CMD"
      BUILD_OUTPUT="Dockerfile: $DOCKERFILE_PATH Base: $DOCKER_BASE CMD: $DOCKER_CMD"
    fi
    BUILD_VERDICT="skip"
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
  if [[ "$BUILD_VERDICT" == "pass" ]]; then
    if [[ "$DEP_TYPE" == "production" ]]; then
      RUN_TESTS="true"
    elif [[ "$BUMP" == "major" && "$DEP_TYPE" == "dev" ]]; then
      RUN_TESTS="true"
    elif [[ "$PKG" == "vitest" || "$PKG" == "jest" || "$PKG" == "mocha" ]]; then
      RUN_TESTS="true"
    fi
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
          cd "$TEST_BUILD_DIR" && retry_cmd 3 5 timeout $TIMEOUT npm ci --ignore-scripts 2>/dev/null || true
          # Use --testPathPattern for scoped test execution in monorepos
          if [[ "$PKG_DIR" != "/" && -f "$TEST_BUILD_DIR/package.json" ]]; then
            # Try scoped tests first (faster), fall back to full test
            echo "  test: npm test in ${TEST_BUILD_DIR#$PR_WORKTREE/}..."
            TEST_OUTPUT=$(cd "$TEST_BUILD_DIR" && timeout 180 npm test -- --passWithNoTests 2>&1)
            TEST_EXIT=$?
          else
            TEST_OUTPUT=$(cd "$TEST_BUILD_DIR" && timeout 180 npm test 2>&1)
            TEST_EXIT=$?
          fi
          TEST_RAN="true"
          # ── Smoke probe: catch DI container / runtime failures ──
          # After tests, compile and try to require the built output. Catches:
          # - NestJS DI container failures (missing providers)
          # - Circular dependency issues
          # - Runtime-only import failures
          # We need to build first because dist/ is .gitignored in most projects.
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
            SMOKE_OUT=$(cd "$TEST_BUILD_DIR" && timeout 10 node -e "
              try { require('./dist/main'); process.exit(0); }
              catch(e) { console.error(e.message); process.exit(1); }
            " 2>&1)
            SMOKE_EXIT=$?
            SMOKE_RAN="true"
            echo "  smoke: exit=$SMOKE_EXIT"
          elif [[ -f "$TEST_BUILD_DIR/dist/index.js" ]]; then
            echo "  smoke: node require('./dist/index') ..."
            SMOKE_OUT=$(cd "$TEST_BUILD_DIR" && timeout 10 node -e "
              try { require('./dist/index'); process.exit(0); }
              catch(e) { console.error(e.message); process.exit(1); }
            " 2>&1)
            SMOKE_EXIT=$?
            SMOKE_RAN="true"
            echo "  smoke: exit=$SMOKE_EXIT"
          fi
          ;;
        gomod)
          TEST_OUTPUT=$(cd "$PR_WORKTREE" && timeout 300 go test -race -timeout 5m ./... 2>&1)
          TEST_EXIT=$?
          TEST_RAN="true"
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
          if [[ -f "$PR_WORKTREE/requirements.txt" ]]; then
            cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_TEST" install -r requirements.txt --quiet 2>/dev/null || true
          elif [[ -f "$PR_WORKTREE/pyproject.toml" ]]; then
            cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_TEST" install -e . --quiet 2>/dev/null || true
          elif [[ -f "$PR_WORKTREE/poetry.lock" ]]; then
            cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PIP_TEST" install poetry --quiet 2>/dev/null || true
            cd "$PR_WORKTREE" && retry_cmd 3 5 "$_PY_PYTHON_TEST" -m poetry install --quiet 2>/dev/null || true
          fi
          TEST_OUTPUT=$(cd "$PR_WORKTREE" && timeout 180 "$_PY_PYTHON_TEST" -m pytest 2>&1)
          TEST_EXIT=$?
          TEST_RAN="true"
          [[ -n "$_PY_VENV_TEST" ]] && rm -rf "$_PY_VENV_TEST" 2>/dev/null || true
          ;;
      esac
      echo "  test: exit=$TEST_EXIT"
      git worktree remove "$PR_WORKTREE" --force 2>/dev/null || rm -rf "$PR_WORKTREE"
    fi
  fi

  # ── Write PR data to JSON ──────────────────────────────────────
  # Write build and test output to temp files for safe JSON encoding
  echo "$BUILD_OUTPUT" | tail -n 50 > "/tmp/_bc_build_out_${PR_NUM}.txt"
  echo "$TEST_OUTPUT" | tail -n 30 > "/tmp/_bc_test_out_${PR_NUM}.txt"
  echo "$NEW_ERRORS" > "/tmp/_bc_new_errors_${PR_NUM}.txt"
  echo "$DETERMINISTIC" > "/tmp/_bc_det_${PR_NUM}.json"
  echo "$FILES_IMPORTING" > "/tmp/_bc_files_${PR_NUM}.json"

  # Determine main exit for this ecosystem
  MAIN_EXIT_FOR_ECO=-1
  case "$ECOSYSTEM" in
    npm)   MAIN_EXIT_FOR_ECO=$main_npm_exit ;;
    gomod) MAIN_EXIT_FOR_ECO=$main_go_exit ;;
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

  python3 << PYEOF
import json, os

results_file = "$RESULTS_FILE"
pr_num = "$PR_NUM"

with open(results_file) as f:
    data = json.load(f)

# Read deterministic output
det_path = f"/tmp/_bc_det_{pr_num}.json"
try:
    with open(det_path) as f:
        det_raw = f.read().strip()
    deterministic = json.loads(det_raw) if det_raw and det_raw != '{}' else {}
except:
    deterministic = {}

# Read cascade_impact
cascade_str = """$CASCADE_IMPACT"""
try:
    cascade_impact = json.loads(cascade_str) if cascade_str.strip() else []
except:
    cascade_impact = []


# Read files_importing
files_path = f"/tmp/_bc_files_{pr_num}.json"
try:
    with open(files_path) as f:
        files_importing = json.loads(f.read().strip())
except:
    files_importing = []

# Read additional_imports for multi-package PRs
try:
    additional_imports = json.loads('''$ADDITIONAL_IMPORTS''')
except:
    additional_imports = []

# Read build output
build_out_path = f"/tmp/_bc_build_out_{pr_num}.txt"
try:
    with open(build_out_path) as f:
        build_output = f.read()
except:
    build_output = ""

# Read test output
test_out_path = f"/tmp/_bc_test_out_{pr_num}.txt"
try:
    with open(test_out_path) as f:
        test_output = f.read()
except:
    test_output = ""

# Read new errors (errors on PR branch not present on main)
new_errors_path = f"/tmp/_bc_new_errors_{pr_num}.txt"
try:
    with open(new_errors_path) as f:
        new_errors_raw = f.read().strip()
    new_errors = [e for e in new_errors_raw.split('\n') if e.strip()] if new_errors_raw else []
except:
    new_errors = []

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
# Append project-specific patterns from .github/breakability-config.yml
extra_raw = """$EXTRA_INFRA_PATTERNS"""
for line in extra_raw.strip().split('\n'):
    line = line.strip()
    if line and line not in INFRA_ERROR_PATTERNS:
        INFRA_ERROR_PATTERNS.append(line)
if new_errors:
    real_errors = [e for e in new_errors if not any(p in e for p in INFRA_ERROR_PATTERNS)]
    infra_filtered = len(new_errors) - len(real_errors)
    new_errors = real_errors

# Parse CVEs
cves_raw = "$CVES"
cves = [c.strip() for c in cves_raw.split(",") if c.strip()] if cves_raw else []

# Test values
test_ran = True if "$TEST_RAN" == "true" else False
test_exit_raw = "$TEST_EXIT"
test_exit = int(test_exit_raw) if test_exit_raw not in ("null", "") else None

# If all "new" errors were infra artifacts, downgrade verdict to pre_existing
build_verdict = "$BUILD_VERDICT"
if build_verdict == "pre_existing_plus_new" and not new_errors:
    build_verdict = "pre_existing"

pr_data = {
    "package": "$PKG",
    "from": "$FROM_VER",
    "to": "$TO_VER",
    "ecosystem": "$ECOSYSTEM",
    "bump": "$BUMP",
    "dep_type": "$DEP_TYPE",
    "dep_relation": "$DEP_RELATION",
    "cves": cves,
    "deterministic": deterministic,
    "build": {
        "main_exit": $MAIN_EXIT_FOR_ECO,
        "pr_exit": $BUILD_EXIT,
        "verdict": build_verdict,
        "output_tail": build_output,
        "new_errors": new_errors,
        "install_method": "${INSTALL_METHOD:-ci}",
        "error_class": "${ERROR_CLASS:-}"

    },
    "test": {
        "ran": test_ran,
        "exit": test_exit,
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
    "nestjs_peer_warning": "$NESTJS_PEER_WARNING",
    "install_ok": True if "$INSTALL_OK" == "true" else False,
    "additional_packages": "$ADDITIONAL_PACKAGES",
    "mergeable_status": "$MERGEABLE_STATUS",
    "npm_audit": {
        "critical": $AUDIT_CRITICAL,
        "high": $AUDIT_HIGH
    }
}

# ── Ownership classification ─────────────────────────────────
# Tells reviewers WHO fixes this and whether THEIR code is affected.
eco = "$ECOSYSTEM"
pkg = "$PKG"
dep_type = "$DEP_TYPE"
dep_rel = "$DEP_RELATION"
pkg_dir = "$PKG_DIR"
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

# Actions and Docker PRs have no build step — mark as N/A, not L0
if eco in ("actions", "docker"):
    level = -1  # sentinel for N/A
    steps = [{"step": "not_applicable", "status": "skip", "detail": f"{eco} dependency — no build verification"}]
else:
    install_ok = pr_data.get("install_ok", False)
    build_verdict = "$BUILD_VERDICT"
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
        if eco in ("gomod", "pip"):
            # go build / pip import check IS the type-check equivalent
            if build_verdict in ("pass", "pre_existing"):
                level = 2
                steps.append({"step": "type_check", "status": "pass"})
            else:
                steps.append({"step": "type_check", "status": "fail"})
        elif tsc_ran:
            if tsc_passed:
                # tsc actually passed — genuine L2
                level = 2
                steps.append({"step": "type_check", "status": "pass"})
            elif build_verdict == "pre_existing":
                # tsc failed on both branches with same errors — NOT a real pass
                # Stay at L1, mark type_check as "pre_existing" (inconclusive)
                level = 1  # DO NOT promote to L2
                steps.append({"step": "type_check", "status": "pre_existing", "detail": "same tsc errors on main — inconclusive"})
            else:
                steps.append({"step": "type_check", "status": "fail"})
        else:
            steps.append({"step": "type_check", "status": "skip", "detail": "no tsconfig.json"})
            if build_verdict in ("pass",):
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
        # For Go: surface pre-existing test failure explicitly so agent/humans can
        # tell "tests fail on this PR" from "tests were already broken on main".
        main_go_test_exit_raw = "$main_go_test_exit"
        main_go_test_exit_val = int(main_go_test_exit_raw) if main_go_test_exit_raw not in ("-1", "") else -1
        if test_ran_val and test_exit_val is not None:
            if test_exit_val == 0:
                level = max(level, 4)
                steps.append({"step": "test_suite", "status": "pass"})
            else:
                # When both main and PR tests fail for Go, the failure is pre-existing
                is_preexisting_test = (
                    eco == "gomod" and main_go_test_exit_val > 0 and test_exit_val > 0
                )
                if is_preexisting_test:
                    steps.append({"step": "test_suite", "status": "pre_existing",
                                  "detail": f"exit={test_exit_val} — same failure on main (exit={main_go_test_exit_val})"})
                else:
                    steps.append({"step": "test_suite", "status": "fail", "detail": f"exit={test_exit_val}"})
        else:
            steps.append({"step": "test_suite", "status": "skip", "detail": "not triggered"})

        # L5: Fully verified (tests pass + no new errors + symbols ok + smoke ok)
        if (test_ran_val and test_exit_val == 0 and
            build_verdict in ("pass",) and
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

pr_data["verification_level"] = level
pr_data["verification_label"] = LEVEL_LABELS.get(level, f"L{level}")
pr_data["verification_steps"] = steps

data["prs"][pr_num] = pr_data

with open(results_file, "w") as f:
    json.dump(data, f, indent=2)

print(f"  ✓ PR #{pr_num} written to results")

# Cleanup temp files
for p in [det_path, files_path, build_out_path, test_out_path, new_errors_path]:
    try:
        os.remove(p)
    except:
        pass
PYEOF

  cd "$REPO_ROOT"
done

# Clean up main worktree (kept alive for lazy baselines during PR processing)
git worktree remove "$MAIN_DIR" --force 2>/dev/null || rm -rf "$MAIN_DIR"

# ── In batch mode, skip cross-PR / security / cleanup (merge script handles those) ──
if [[ -n "$BATCH_ID" ]]; then
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

python3 << 'CROSSDEPS'
import json, re

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
except: pass
with open("/tmp/build-results.json") as f: data = json.load(f)
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
except:
    data["workspace_graph"] = {}
    data["nestjs_skew"] = []
data["cross_pr_deps"] = cross_deps
with open("/tmp/build-results.json", "w") as f: json.dump(data, f, indent=2)
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
try:
    result = subprocess.run(
        ["gh", "api", f"repos/{owner_repo}/dependabot/alerts",
         "--jq", '.[] | {number, state, security_advisory: {ghsa_id: .security_advisory.ghsa_id, cve_id: .security_advisory.cve_id, severity: .security_advisory.severity, summary: .security_advisory.summary}, dependency: {package: .dependency.package.name, ecosystem: .dependency.package.ecosystem, manifest_path: .dependency.manifest_path}}',
         "-X", "GET", "--paginate"],
        capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        print("  Could not fetch Dependabot alerts (may need security permissions)")
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
with open("/tmp/build-results.json") as f:
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

security_posture = {
    "total_open_alerts": len(open_alerts),
    "severity_counts": severity_counts,
    "total_cves_in_prs": total_cve_count,
    "prs_fixing_alerts": fixes_by_pr,
    "prs_with_cves": pr_cves,
    "alerts_fixable_by_merging": sum(f["alert_count"] for f in fixes_by_pr.values())
}

data["security_posture"] = security_posture
with open("/tmp/build-results.json", "w") as f:
    json.dump(data, f, indent=2)

print(f"  Open vulnerability alerts: {len(open_alerts)}")
for sev, count in sorted(severity_counts.items(), key=lambda x: {'critical':0,'high':1,'medium':2,'low':3}.get(x[0],4)):
    print(f"    {sev}: {count}")
print(f"  PRs that fix known alerts: {len(fixes_by_pr)}")
print(f"  Alerts fixable by merging open PRs: {security_posture['alerts_fixable_by_merging']}")
if total_cve_count:
    print(f"  CVEs referenced in PR bodies: {total_cve_count}")
SECURITYEOF


# ── Comment cleanup ──────────────────────────────────────────────────────────
echo ""
echo "════════════ COMMENT CLEANUP ════════════"

DELETED_COUNT=0
for i in $(seq 0 $(( PR_COUNT - 1 )) ); do
  PR_NUM=$(echo "$PR_JSON" | jq -r ".[$i].number")

  # Find and delete old breakability comments
  COMMENT_IDS=$(gh api "repos/$OWNER/$REPO/issues/$PR_NUM/comments" \
    --jq '.[] | select(.body | contains("<!-- breakability-check -->") or contains("<!-- breakability-agent -->")) | .id' \
    2>/dev/null || true)

  for CID in $COMMENT_IDS; do
    gh api -X DELETE "repos/$OWNER/$REPO/issues/comments/$CID" 2>/dev/null || true
    DELETED_COUNT=$((DELETED_COUNT + 1))
  done
done
echo "  Deleted $DELETED_COUNT old comments"

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
