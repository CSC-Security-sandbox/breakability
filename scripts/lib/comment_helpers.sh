#!/usr/bin/env bash
# Comment helper functions for post-fallback-comments.sh.
# Sourced by post-fallback-comments.sh — do not run directly.

gh_pr_comment() {  # gh_pr_comment <pr> <body>
  local pr="$1" body="$2"
  if [[ "$DRY_RUN" == "1" ]]; then
    printf '%s\n' "$body" > "$DRY_RUN_DIR/pr-${pr}.md"
    echo "  [dry-run] wrote comment -> $DRY_RUN_DIR/pr-${pr}.md"
    return 0
  fi
  gh pr comment "$pr" --body "$body" 2>/dev/null
}
gh_delete_comment() {  # gh_delete_comment <owner> <repo> <comment_id>
  if [[ "$DRY_RUN" == "1" ]]; then return 0; fi
  gh api -X DELETE "repos/$1/$2/issues/comments/$3" 2>/dev/null || true
}

get_verdict_v2() {
  local _pr_number="${1:-}" _BC_V2_PR _BC_V2_RESULTS
  _BC_V2_PR="$_pr_number"
  _BC_V2_RESULTS="$RESULTS_FILE"
  export _BC_V2_PR _BC_V2_RESULTS
  python3 - <<'PYEOF'
import json
import os
import re
import shlex

SIGNALS = ("resolve", "build", "test", "api_diff", "usage", "vuln", "changelog")
SIGNAL_STATES = {"POSITIVE", "NEGATIVE", "NONE", "UNAVAILABLE", "N_A"}
DEFAULTS = {
    "V2_OK": "0",
    "V2_VERDICT": "REVIEW",
    "V2_SEVERITY": "medium",
    "V2_CONF": "L0",
    "V2_PRIO": "P2",
    "V2_REASON": "verdict map unavailable — manual review",
    "V2_BREAK_GRADE": "MEDIUM_BREAKING",
    "V2_RESIDUAL_SUMMARY": "",
    "V2_RESIDUAL_CHECK": "",
    "V2_RESIDUAL_CHANGELOG": "",
    "V2_RESIDUAL_REACH": "",
}

def emit(values):
    for key in (
        "V2_OK",
        "V2_VERDICT",
        "V2_SEVERITY",
        "V2_CONF",
        "V2_PRIO",
        "V2_REASON",
        "V2_BREAK_GRADE",
        "V2_RESIDUAL_SUMMARY",
        "V2_RESIDUAL_CHECK",
        "V2_RESIDUAL_CHANGELOG",
        "V2_RESIDUAL_REACH",
    ):
        value = "" if values.get(key) is None else str(values.get(key, ""))
        value = value.replace("\r", "\n")
        print(f"{key}={shlex.quote(value)}")
    signal_values = values.get("_signals", {})
    for signal in SIGNALS:
        state = signal_values.get(signal, "UNAVAILABLE")
        if state not in SIGNAL_STATES:
            state = "UNAVAILABLE"
        print(f"V2_SIG_{signal}={shlex.quote(state)}")

def fail():
    emit(DEFAULTS)

def clean_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    return str(value).replace("\r\n", "\n").replace("\r", "\n")

try:
    pr_number = os.environ.get("_BC_V2_PR", "")
    results_path = os.environ.get("_BC_V2_RESULTS", "")
    with open(results_path, encoding="utf-8") as fh:
        data = json.load(fh)
    verdict_v2 = ((data.get("prs") or {}).get(pr_number) or {}).get("verdict_v2")
    if not isinstance(verdict_v2, dict):
        fail()
        raise SystemExit(0)

    verdict = verdict_v2.get("verdict")
    confidence = verdict_v2.get("confidence")
    priority = verdict_v2.get("priority")
    if verdict not in {"SAFE", "REVIEW", "BLOCKED"}:
        fail()
        raise SystemExit(0)
    if not isinstance(confidence, str) or not re.fullmatch(r"L[0-5]", confidence):
        fail()
        raise SystemExit(0)
    if not isinstance(priority, str) or not re.fullmatch(r"P[0-3]", priority):
        fail()
        raise SystemExit(0)

    residual = verdict_v2.get("residual") or {}
    if not isinstance(residual, dict):
        residual = {}
    reachability = residual.get("reachability") or {}
    if not isinstance(reachability, dict):
        reachability = {}
    evidence_state = verdict_v2.get("evidenceState") or {}
    if not isinstance(evidence_state, dict):
        evidence_state = {}

    severity = verdict_v2.get("severity")
    if severity not in {"none", "low", "medium", "high"}:
        # Fail-safe derivation if the bundle predates the severity field.
        severity = {"BLOCKED": "high", "SAFE": "low", "REVIEW": "medium"}.get(verdict, "medium")

    breakability_grade = verdict_v2.get("breakability_grade", "MEDIUM_BREAKING")

    values = {
        "V2_OK": "1",
        "V2_VERDICT": verdict,
        "V2_SEVERITY": severity,
        "V2_CONF": confidence,
        "V2_PRIO": priority,
        "V2_REASON": clean_text(verdict_v2.get("reason")),
        "V2_BREAK_GRADE": breakability_grade,
        "V2_RESIDUAL_SUMMARY": clean_text(residual.get("summary")),
        "V2_RESIDUAL_CHECK": clean_text(residual.get("check")),
        "V2_RESIDUAL_CHANGELOG": clean_text(residual.get("changelogLine")),
        "V2_RESIDUAL_REACH": clean_text(reachability.get("path") or reachability.get("kind")),
        "_signals": {signal: str(evidence_state.get(signal, "UNAVAILABLE")) for signal in SIGNALS},
    }
    emit(values)
except Exception:
    fail()
PYEOF
}

get_behavioral_grade() {
  local _pr_number="${1:-}"
  _BC_BG_PR="$_pr_number" _BC_BG_RESULTS="$RESULTS_FILE" python3 - <<'PYEOF'
import json, os, shlex

KEYS = ("BG_OK", "BG_GRADE", "BG_SOURCE", "BG_RATIONALE", "BG_GUIDANCE",
        "BG_EVIDENCE", "BG_CALLSITE", "BG_CHANGED", "BG_CONFIDENCE")
DEFAULTS = {k: "" for k in KEYS}
DEFAULTS["BG_OK"] = "0"

def emit(v):
    for k in KEYS:
        val = "" if v.get(k) is None else str(v.get(k, "")).replace("\r", " ")
        print(f"{k}={shlex.quote(val)}")

try:
    pr = os.environ.get("_BC_BG_PR", "")
    with open(os.environ.get("_BC_BG_RESULTS", ""), encoding="utf-8") as fh:
        data = json.load(fh)
    g = ((data.get("prs") or {}).get(pr) or {}).get("behavioral_grade")
    if not isinstance(g, dict) or str(g.get("grade", "")).lower() not in ("none", "low", "medium", "high"):
        emit(DEFAULTS); raise SystemExit(0)
    emit({
        "BG_OK": "1",
        "BG_GRADE": str(g.get("grade", "")).lower(),
        "BG_SOURCE": str(g.get("source", "")),
        "BG_RATIONALE": str(g.get("rationale", "")),
        "BG_GUIDANCE": str(g.get("guidance", "")),
        "BG_EVIDENCE": str(g.get("evidence", "")),
        "BG_CALLSITE": str(g.get("call_site", "")),
        "BG_CHANGED": str(g.get("behavior_changed", "")),
        "BG_CONFIDENCE": str(g.get("confidence", "")),
    })
except Exception:
    emit(DEFAULTS)
PYEOF
}

v2_signal_label() {
  case "${1:-UNAVAILABLE}" in
    POSITIVE) printf '⚠️ concern' ;;
    NEGATIVE) printf '✅ checked-clean' ;;
    NONE) printf '· not observed' ;;
    UNAVAILABLE) printf '⚪ not available' ;;
    N_A) printf '– n/a' ;;
    *) printf '⚪ not available' ;;
  esac
}

build_go_changelog_block() {
  local _pkg="$1" _from="$2" _to="$3" _gh_path _releases _changelog _content _signals
  [[ -z "$_pkg" || -z "$_from" || -z "$_to" ]] && return 0
  _gh_path=$(echo "$_pkg" | grep -oE '^github\.com/[^/]+/[^/]+' || echo "")
  if [[ -z "$_gh_path" ]]; then
    _gh_path=$(echo "$_pkg" | sed -n 's|^golang.org/x/\([^/]*\)|github.com/golang/\1|p')
  fi
  if [[ -z "$_gh_path" ]]; then
    _gh_path=$(echo "$_pkg" | sed -n 's|^go.opentelemetry.io/.*|github.com/open-telemetry/opentelemetry-go|p' | head -1)
  fi
  [[ -z "$_gh_path" ]] && return 0

  unset GH_TOKEN
  _releases=$(gh api "repos/${_gh_path#github.com/}/releases?per_page=100" --jq '[.[] | {tag_name,name,body: ((.body // "")[0:4000])}]' 2>/dev/null || echo '[]')
  _changelog=""
  for _candidate in CHANGELOG.md CHANGES.md HISTORY.md RELEASES.md; do
    unset GH_TOKEN
    _content=$(gh api "repos/${_gh_path#github.com/}/contents/${_candidate}" --jq '.content // ""' 2>/dev/null | python3 -c 'import base64,sys; data=sys.stdin.read().strip(); print(base64.b64decode(data).decode("utf-8","replace") if data else "")' 2>/dev/null | head -c 24000 || true)
    if [[ -n "$_content" ]]; then
      _changelog="$_content"
      break
    fi
  done

  _signals=$(_BC_RELEASES="$_releases" _BC_CHANGELOG="$_changelog" _BC_FROM="$_from" _BC_TO="$_to" _BC_GH_PATH="$_gh_path" python3 -c '
import json, os, re
releases = json.loads(os.environ.get("_BC_RELEASES", "[]") or "[]")
changelog = os.environ.get("_BC_CHANGELOG", "") or ""
from_v = os.environ.get("_BC_FROM", "")
to_v = os.environ.get("_BC_TO", "")
gh_path = os.environ.get("_BC_GH_PATH", "")

def norm(v):
    v = (v or "").strip().lstrip("v")
    m = re.search(r"(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)", v)
    return m.group(1) if m else v
def _clip(x, n=220):
    x = (x or "").strip()
    if len(x) <= n:
        return x
    head = x[:n].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return (head or x[:n]) + "…"
def tup(v):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", norm(v))
    return tuple(map(int, m.groups())) if m else None
lo, hi = tup(from_v), tup(to_v)
def in_range(tag):
    tv = tup(tag)
    if not tv or not lo or not hi:
        return norm(to_v) in norm(tag)
    return lo < tv <= hi
patterns = re.compile(r"\b(BREAKING|removed?|incompatible|migration|required|default(?:s| value)? change|deprecated|renamed|deleted|no longer|behavior change|API change)\b", re.I)
items = []
for rel in releases:
    tag = rel.get("tag_name") or rel.get("name") or ""
    if not in_range(tag):
        continue
    text = "\n".join([str(rel.get("name") or tag), str(rel.get("body") or "")])
    for line in text.splitlines():
        line = line.strip(" -*\t")
        if line and patterns.search(line):
            items.append((tag, _clip(line, 220)))
            break
if changelog:
    for line in changelog.splitlines():
        line = line.strip(" -*\t")
        if line and patterns.search(line):
            items.append(("CHANGELOG", _clip(line, 220)))
            if len(items) >= 10:
                break
seen=[]
for tag,line in items:
    val=(tag,line)
    if val not in seen:
        seen.append(val)
if seen:
    print("### Changelog signals")
    print(f"Source: GitHub releases/CHANGELOG for `{gh_path}` between `{from_v}` → `{to_v}`")
    for tag,line in seen[:10]:
        print(f"- `{tag}`: {line}")
else:
    print("### Changelog signals")
    print(f"Source: [{gh_path} compare](https://{gh_path}/compare/v{from_v}...v{to_v})")
    print("- No deterministic breaking-change markers found in fetched Releases/CHANGELOG (checked for BREAKING, removed APIs, incompatible/default-value changes).")
' 2>/dev/null || true)
  [[ -n "$_signals" ]] && printf '
%s
' "$_signals"
}

# G6: render "### Changelog signals" from the PERSISTED deterministic changelog analysis
# (deterministic.changelogSignal / changelogText) so the below-the-fold list and the verdict
# residual are ONE source of truth. Emits nothing when no persisted analysis exists, letting
# the caller fall back to the live GitHub re-fetch for legacy records. Arg 1 = PR_FIELDS JSON.
build_changelog_block_persisted() {
  local _bcp
  _bcp=$(_BC_PRF="$1" python3 - <<'PYEOF' 2>/dev/null
import json, os, re
def _clip(x, n=220):
    x = (x or '').strip()
    if len(x) <= n:
        return x
    head = x[:n].rsplit(' ', 1)[0].rstrip(' ,.;:-')
    return (head or x[:n]) + '…'
data = json.loads(os.environ.get('_BC_PRF', '') or '{}')
det = data.get('deterministic') or {}
sig = det.get('changelogSignal') or {}
status = sig.get('status')
text = det.get('changelogText') or ''
# No persisted analysis at all -> let the caller fall back to the live re-fetch.
if not status and not text:
    raise SystemExit(0)
clean, seen = [], set()
neg = re.compile(r"\b(no|not|without|non[-\s]?breaking|does not|did not)\b.{0,80}\b(api change|breaking|incompatible|removed|behavior change)s?\b|\b(api change|breaking change)s?\b.{0,80}\b(no|not|without|none)\b", re.I)
for b in (sig.get('bullets') or []):
    if not isinstance(b, str):
        continue
    s = re.sub(r'\s+', ' ', b.replace('\r', ' ').replace('\n', ' ')).strip(' -*\t')
    if not s or s.startswith('#') or neg.search(s):  # drop pure markdown headers and negated no-change bullets
        continue
    s = _clip(s, 220)
    k = s.lower()
    if k in seen:
        continue
    seen.add(k)
    clean.append(s)
    if len(clean) >= 10:
        break
print("### Changelog signals")
print("Source: deterministic changelog analysis (same source as the verdict)")
if clean:
    for s in clean:
        print(f"- {s}")
elif status and status != 'breaking':
    print("- No breaking-change markers found in the analyzed changelog.")
else:
    snippet = _clip(re.sub(r'\s+', ' ', text), 220)
    print(f"- {snippet}" if snippet else "- Changelog analyzed; see release notes for details.")
PYEOF
)
  # Lead with a blank line so the block never fuses to preceding inline text (markdown heading).
  [[ -n "$_bcp" ]] && printf '\n\n%s\n' "$_bcp"
}
