#!/usr/bin/env bash
# Hermetic behavioral-probe self-test (ground-truth corpus, tier-0).
#
# Proves the EXECUTING differential probe end-to-end: the cursor-agent must build a
# real dependency at two real versions, RUN code that exercises the declared
# call-observable change, diff the named dimension, and the driver must commit a
# graded proof contract -- AND map the change to OUR specific usage (not just detect
# that the dependency changed).
#
# The "dependency" is a synthetic two-version Go module served from a local file
# module-proxy. Both versions export the SAME signature `func Format(int) string`;
# v1.1.0 changes Format's OUTPUT (groups digits: 123456789 -> 123,456,789) -- a
# behavioral, call-observable break that build/test/apidiff are all blind to
# (compiles clean at both versions, signature identical).
#
# Two corpus cases in one run:
#   9001  EXPOSED   call site Format(123456789) -> output differs -> expect HIGH.
#   9002  NOT-EXPOSED call site Format(5)       -> output "5" both versions
#                    (no grouping under 1000)   -> expect LOW/NONE.
# 9002 is the negative control: it fails an agent that mechanically grades any
# observed dependency change as HIGH without mapping it to our actual call.
#
# INDEPENDENT EXECUTION PROOF: Format() writes a per-version sentinel file into
# $BREAKDEP_AUDIT_DIR when actually called. After the probe we assert BOTH version
# sentinels exist -- proof the agent built AND RAN both versions, not just read the
# changelog. The changelog bullet deliberately does NOT reveal the exact old/new
# values, so a model cannot pass by echoing prose.
#
# Hermetic: no public network (file:// proxy); the only external call is the agent
# model call (needs CURSOR_API_KEY). Deterministic, repeatable.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
GODEP="example.com/breakdep"
WORK="$(mktemp -d "${RUNNER_TEMP:-/tmp}/probe-selftest.XXXXXX")"
# Go's module cache is written read-only; make it deletable before cleanup.
trap 'chmod -R u+w "$WORK" 2>/dev/null || true; rm -rf "$WORK"' EXIT

PROXY="$WORK/proxy/$GODEP/@v"
SRC="$WORK/src"
CON="$WORK/consumer"
AUDIT="$WORK/audit"
mkdir -p "$PROXY" "$SRC" "$CON" "$AUDIT"

log() { echo "[probe-selftest] $*"; }

# ── 1. synthesize the two dependency versions (with an execution sentinel) ───
mk_version() {
  local ver="$1" body="$2"
  local d="$SRC/${GODEP}@${ver}"
  mkdir -p "$d"
  cat > "$d/go.mod" <<EOF
module example.com/breakdep

go 1.21
EOF
  cat > "$d/format.go" <<EOF
// Package breakdep formats integers. v1.1.0 changed Format's output behavior.
package breakdep

import (
	"os"
	"path/filepath"
	"strconv"
)

// audit drops a per-version sentinel when Format actually runs, so a harness that
// merely compiles (or an agent that only reasons) cannot fake execution.
func audit() {
	d := os.Getenv("BREAKDEP_AUDIT_DIR")
	if d == "" {
		return
	}
	_ = os.MkdirAll(d, 0o755)
	_ = os.WriteFile(filepath.Join(d, "${ver}.called"), []byte("${ver}"), 0o644)
}

// Format renders n as a string. Signature is identical across versions; only the
// returned FORMAT changed in v1.1.0 (digit grouping).
func Format(n int) string {
	audit()
$body
}
EOF
}

mk_version v1.0.0 '	return strconv.Itoa(n)'
mk_version v1.1.0 '	s := strconv.Itoa(n)
	neg := ""
	if len(s) > 0 && s[0] == '"'"'-'"'"' {
		neg, s = "-", s[1:]
	}
	out := ""
	for i := range s {
		if i > 0 && (len(s)-i)%3 == 0 {
			out += ","
		}
		out += string(s[i])
	}
	return neg + out'

# ── 2. pack into a local file module-proxy ──────────────────────────────────
for ver in v1.0.0 v1.1.0; do
  d="$SRC/${GODEP}@${ver}"
  cp "$d/go.mod" "$PROXY/${ver}.mod"
  printf '{"Version":"%s","Time":"2024-01-01T00:00:00Z"}\n' "$ver" > "$PROXY/${ver}.info"
  ( cd "$SRC" && zip -qr "$PROXY/${ver}.zip" "${GODEP}@${ver}" )
done
printf 'v1.0.0\nv1.1.0\n' > "$PROXY/list"

export GOPROXY="file://$WORK/proxy"
export GOSUMDB=off
export GONOSUMDB='*'
export GOFLAGS=-mod=mod
export GOTOOLCHAIN=local
export GOMODCACHE="$WORK/gomodcache"   # hermetic: probe fetches from our proxy only
unset GOPRIVATE GONOSUMCHECK 2>/dev/null || true

# ── 3. consumer fixture (the "project repo" usage is mapped against) ─────────
cat > "$CON/go.mod" <<EOF
module consumer

go 1.21

require example.com/breakdep v1.0.0
EOF
cat > "$CON/main.go" <<'EOF'
package main

import (
	"fmt"

	"example.com/breakdep"
)

func main() {
	// our exposed call site (large number -> grouping changes the output)
	fmt.Println(breakdep.Format(123456789))
}
EOF
cat > "$CON/small.go" <<'EOF'
package main

import "example.com/breakdep"

// our NON-exposed call site: output of a value < 1000 is identical across versions.
func small() string {
	return breakdep.Format(5)
}
EOF
( cd "$CON" && go mod download example.com/breakdep@v1.0.0 >/dev/null 2>&1 || true )
# Must be a clean git repo: the probe's porcelain safety guard fails CLOSED otherwise.
( cd "$CON" && git init -q && git add -A && git -c user.email=ci@local -c user.name=ci commit -qm fixture )

# Sanity (no audit dir set -> these runs do NOT write sentinels): the diff is real.
log "ground-truth diff (v1.0.0 vs v1.1.0):"
( cd "$CON" && go get example.com/breakdep@v1.0.0 >/dev/null 2>&1 && echo "  from: $(go run . 2>&1)" )
( cd "$CON" && go get example.com/breakdep@v1.1.0 >/dev/null 2>&1 && echo "  to:   $(go run . 2>&1)" )
( cd "$CON" && go get example.com/breakdep@v1.0.0 >/dev/null 2>&1 \
    && git add -A && git -c user.email=ci@local -c user.name=ci commit -qm pin-from --allow-empty )
# Clear any sentinels the sanity runs might have produced; the probe must create them.
rm -f "$AUDIT"/*.called 2>/dev/null || true

# ── 4. craft the two residual PR records the probe consumes ──────────────────
# NOTE: the bullet does NOT reveal the exact old/new output -- the agent must read
# the dependency source at both versions and execute to learn it.
RESULTS="$WORK/build-results.json"
mk_pr() {  # $1=num $2=file $3=line $4=symbol
  cat <<EOF
    "$1": {
      "package": "example.com/breakdep",
      "ecosystem": "gomod",
      "from": "v1.0.0",
      "to": "v1.1.0",
      "deterministic": {
        "changelogText": "v1.1.0: Format now emits grouped decimal output for integers (digit grouping). The function signature is unchanged.",
        "changelogSignal": {
          "status": "breaking",
          "bullets": ["Format now emits grouped decimal output for integers (digit grouping); signature unchanged"]
        },
        "usages": [
          {"file": "$2", "line": $3, "symbol": "$4", "usageType": "call", "context": "production"}
        ]
      },
      "declared_break_reachability": {
        "reachability_kind": "import",
        "prod_reachable": true,
        "surface_evidence": [
          {"named": true, "is_test": false, "file": "$2", "line": $3, "symbol": "$4", "path": "example.com/breakdep"}
        ],
        "evidence": [
          {"is_test": false, "file": "$2", "line": 3, "import_path": "example.com/breakdep"}
        ]
      }
    }
EOF
}
{
  echo '{'
  echo '  "prs": {'
  mk_pr 9001 main.go 11 Format
  echo '    ,'
  mk_pr 9002 small.go 7 Format
  echo '  }'
  echo '}'
} > "$RESULTS"
python3 -c "import json;json.load(open('$RESULTS'))"  # validate JSON

# ── 5. run the EXECUTING probe (real agent) ─────────────────────────────────
log "running differential probe against the synthetic call-observable break ..."
DP_RESULTS="$RESULTS" \
DP_REPO_ROOT="$CON" \
DP_PROMPT="$REPO_DIR/.github/differential-probe-prompt.md" \
DP_REASON_PROMPT="$REPO_DIR/.github/differential-reasoning-prompt.md" \
DP_MAX_PRS=2 \
DP_CACHE_DIR="$WORK/dp-cache" \
BREAKDEP_AUDIT_DIR="$AUDIT" \
  python3 "$REPO_DIR/.github/scripts/differential-probe.py" || true

# ── 6. assert the probe actually EXECUTED and proved/mapped the break ────────
log "execution sentinels:"; ls -1 "$AUDIT" 2>/dev/null || true
log "probe verdicts:"
AUDIT_DIR="$AUDIT" python3 - "$RESULTS" <<'PYEOF'
import json, os, sys
data = json.load(open(sys.argv[1]))
prs = data["prs"]
audit = os.environ["AUDIT_DIR"]
fail = []

# (A) independent execution proof: both versions' Format() actually RAN.
for ver in ("v1.0.0", "v1.1.0"):
    if not os.path.exists(os.path.join(audit, f"{ver}.called")):
        fail.append(f"no execution sentinel for {ver} -- the probe did not build+RUN "
                    f"this version (agent may have only reasoned over the changelog)")

# (B) EXPOSED case 9001 -> executing probe, behaviour changed, our usage exposed, HIGH.
g1 = prs["9001"].get("behavioral_grade") or {}
print("9001 (exposed):", json.dumps(g1, indent=2))
if g1.get("source") != "probe":
    fail.append(f"9001 source={g1.get('source')!r}, expected 'probe' (executing path did not run)")
if g1.get("grade") != "high":
    fail.append(f"9001 grade={g1.get('grade')!r}, expected 'high'")
if g1.get("behavior_changed") is not True:
    fail.append(f"9001 behavior_changed={g1.get('behavior_changed')!r}, expected true")
if g1.get("our_usage_exposed") is not True:
    fail.append(f"9001 our_usage_exposed={g1.get('our_usage_exposed')!r}, expected true")
ofrom, oto = str(g1.get("observed_from","")).strip(), str(g1.get("observed_to","")).strip()
if not (ofrom and oto) or ofrom == oto:
    fail.append(f"9001 observed_from/to not a proven diff: {ofrom!r} -> {oto!r}")
elif "," not in oto or "," in ofrom:
    fail.append(f"9001 observed values don't show the grouping change: {ofrom!r} -> {oto!r}")
if g1.get("cached") is True:
    fail.append("9001 cached=true -- the agent did not actually run this time")
model = str(g1.get("model",""))
if os.path.basename(model.split()[0]) not in ("agent", "cursor-agent", "copilot") if model.split() else True:
    fail.append(f"9001 model={model!r} -- not a real agent backend (stub?)")

# (C) NEGATIVE CONTROL 9002 -> our usage avoids the change -> LOW/NONE (not HIGH).
g2 = prs["9002"].get("behavioral_grade") or {}
print("9002 (negative control):", json.dumps(g2, indent=2))
if g2.get("source") != "probe":
    fail.append(f"9002 source={g2.get('source')!r}, expected 'probe'")
if g2.get("grade") not in ("low", "none"):
    fail.append(f"9002 grade={g2.get('grade')!r}, expected low/none (Format(5) output is "
                f"identical across versions; an always-high agent fails here)")

if fail:
    print("\n[probe-selftest] FAIL:")
    for f in fail:
        print("  - " + f)
    sys.exit(1)
print(f"\n[probe-selftest] PASS: executing probe ran both versions live "
      f"(sentinels present), proved the exposed break HIGH ({ofrom!r} -> {oto!r}), "
      f"and correctly graded the non-exposed call {g2.get('grade')!r}.")
PYEOF
