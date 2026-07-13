#!/usr/bin/env bash
# Ecosystem and dependency detection functions.
# Sourced by build-check.sh — do not run directly.

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
