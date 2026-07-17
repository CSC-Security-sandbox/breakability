#!/usr/bin/env bash
# Go ecosystem functions for breakability analysis.
# Sourced by build-check.sh — do not run directly.

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
neg = re.compile(r"\b(no|not|without|non[-\s]?breaking|does not|did not)\b.{0,80}\b(api change|breaking|incompatible|removed|behavior change)s?\b|\b(api change|breaking change)s?\b.{0,80}\b(no|not|without|none)\b", re.I)
lines = []
for rel in releases:
    tag = rel.get("tag_name") or rel.get("name") or ""
    if not in_range(tag): continue
    text = "\n".join([str(rel.get("name") or tag), str(rel.get("body") or "")])
    for line in text.splitlines():
        line = line.strip(" -*\t")
        if line and pat.search(line) and not neg.search(line):
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
            if s and pat.search(s) and not neg.search(s):
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

# Find all go.mod files to identify module boundaries
mod_roots = []
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('vendor', '.git', 'node_modules')]
    if 'go.mod' in fnames:
        mod_roots.append(os.path.normpath(root))

# Sort by depth (deepest first) for longest-prefix matching
mod_roots.sort(key=lambda x: -x.count('/'))
if not mod_roots:
    print('FALLBACK')
    sys.exit(0)

if not files:
    for mod in sorted(mod_roots):
        print(f'{mod}|./...')
    sys.exit(0)

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

mod_roots = []
for root, dirs, fnames in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ('vendor', '.git', 'node_modules')]
    if 'go.mod' in fnames:
        mod_roots.append(os.path.normpath(root))
mod_roots.sort(key=lambda x: -x.count('/'))
if not mod_roots:
    print('FALLBACK')
    sys.exit(0)

if not files:
    for mod in sorted(mod_roots):
        print(f'{mod}|./...')
    sys.exit(0)

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
  if [[ "${BREAKABILITY_GOVULNCHECK:-1}" != "1" ]]; then
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
    print('FALLBACK')
    sys.exit(0)

if not files:
    for mod in sorted(mod_roots):
        print(f'{mod}|./...')
    sys.exit(0)

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
    -e 's|@v[0-9][0-9.]*[^/:]*/|@VERSION/|g' \
    -e 's|\(\.go\):[0-9]*:[0-9]*:|\1:0:0:|g'
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
