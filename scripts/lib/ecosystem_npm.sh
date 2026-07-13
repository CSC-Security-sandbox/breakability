#!/usr/bin/env bash
# npm ecosystem functions for breakability analysis.
# Sourced by build-check.sh — do not run directly.

# ── npm changelog fetch (release bodies + scoped changelog sections) ──────────
# Resolves npm package metadata to a GitHub repository (including scoped
# monorepo directories), pulls release bodies/tags in (from,to] plus
# CHANGELOG/CHANGES/HISTORY/RELEASES sections, and emits nothing on failure so
# release-notes evidence remains UNAVAILABLE rather than falsely clean.
fetch_npm_changelog_text() {
  local _pkg="$1" _from="$2" _to="$3"
  [[ -z "$_pkg" || -z "$_from" || -z "$_to" ]] && return 0
  python3 "$BRK_SCRIPTS/npm_changelog.py" fetch \
    --package "$_pkg" --from-version "$_from" --to-version "$_to" 2>/dev/null || true
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

# ── Monorepo: workspace dependency graph ──────────────────────
build_workspace_dep_graph() {
  local repo_dir="${1:-.}"
  python3 - "$repo_dir" << 'GRAPHEOF'
import json, os, glob, sys, re
repo = sys.argv[1]
_private_scopes = [s.strip().lower() for s in os.environ.get("BREAKABILITY_PRIVATE_SCOPES", "").split(",") if s.strip()]
def _is_internal_dep(n):
    n = (n or "").lower()
    return any(scope in n for scope in _private_scopes) if _private_scopes else False
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
    internal_deps = [d for d in deps if _is_internal_dep(d)]
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
  # Topologically order internal libs (matched by private_registries scopes from config)
  # from the workspace graph, deps first. Emits "relpath\tname" lines.
  local order
  order=$(_BC_WT="$worktree" python3 - << 'TOPOEOF'
import json, os, sys
try:
    g = json.load(open("/tmp/_bc_workspace_graph.json"))
except Exception:
    sys.exit(0)
pkgs = g.get("packages", {})
_private_scopes = [s.strip().lower() for s in os.environ.get("BREAKABILITY_PRIVATE_SCOPES", "").split(",") if s.strip()]
def is_internal(n):
    n = (n or "").lower()
    return any(scope in n for scope in _private_scopes) if _private_scopes else False
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
