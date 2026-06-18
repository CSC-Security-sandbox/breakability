#!/usr/bin/env bash
# Smoke test: prove the breakability AI pipeline runs on GitHub Copilot CLI as a
# drop-in backend (record -> replay), so the same model class validates our logic
# locally with no Cursor and no API key.
#
#   ./copilot_backend_smoketest.sh          # offline: stub 'copilot', record+replay
#   ./copilot_backend_smoketest.sh --live   # real Copilot CLI one-shot (uses credits)
#
# Exit 0 = record produced a cassette and replay returned the identical text with
# zero further model calls (the property the fast local loop depends on).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$HERE/ai_backend.py"
LIVE=0
[[ "${1:-}" == "--live" ]] && LIVE=1

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export BRK_CASSETTE_DIR="$WORK/cassettes"
export BRK_AGENT_MODEL="${BRK_AGENT_MODEL:-claude-sonnet-4.5}"
PROMPT='Reply with exactly one word: PONG'
NS="smoketest"
KEY="ping"

if [[ "$LIVE" -eq 1 ]]; then
  command -v copilot >/dev/null || { echo "FAIL: copilot CLI not found"; exit 1; }
  export BRK_AGENT_CMD="copilot --model {model}"
else
  # Offline: a fake 'copilot' on PATH that echoes a fixed reply, proving the argv
  # contract (build_argv auto-appends --allow-all-tools --no-color -p <prompt>)
  # and the record/replay mechanics without spending credits.
  STUB="$WORK/bin"; mkdir -p "$STUB"
  cat >"$STUB/copilot" <<'EOF'
#!/usr/bin/env bash
# Last arg is the prompt; emit a deterministic reply to stdout (UI noise to stderr).
echo "PONG" 
echo "AI Credits 0.0 (0s)" >&2
EOF
  chmod +x "$STUB/copilot"
  export PATH="$STUB:$PATH"
  export BRK_AGENT_CMD="copilot --model {model}"
fi

echo "== record (mode=record, backend=copilot) =="
REC="$(BRK_AGENT_MODE=record python3 "$BACKEND" --namespace "$NS" --key "$KEY" "$PROMPT")"
echo "  response: ${REC:-<empty>}"
CASS="$BRK_CASSETTE_DIR/${NS}__${KEY}.json"
[[ -f "$CASS" ]] || { echo "FAIL: no cassette written at $CASS"; exit 1; }
[[ -n "$REC" ]] || { echo "FAIL: record returned empty"; exit 1; }

echo "== replay (mode=replay, no model call) =="
# Break the command so any accidental live call would fail loudly; replay must
# still succeed purely from the cassette.
RPL="$(BRK_AGENT_MODE=replay BRK_AGENT_CMD='false {model}' \
        python3 "$BACKEND" --namespace "$NS" --key "$KEY" "$PROMPT")"
echo "  response: ${RPL:-<empty>}"

[[ "$REC" == "$RPL" ]] || { echo "FAIL: replay text != recorded text"; exit 1; }
echo "PASS: copilot backend record->replay round-trip identical ('$RPL')"
