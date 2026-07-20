#!/bin/bash
# v15 Multi-Agent Evaluation Loop
#
# Two main agents (Opus 4.6) each with sub-agent reviewers/workers (Sonnet 5 via copilot).
# Deterministic gate as HARD FLOOR. LLM evaluation for content/pipeline/security quality.
# JSON handoff between evaluator and generator. Wiki for iteration history.
#
# Architecture:
#   1. Evaluator phase: deterministic gate → 4 persona sub-agents → Opus cross-check
#   2. Generator phase: Opus plans fixes → Sonnet sub-agents execute → Opus verifies
#   3. CI trigger → wait → next iteration
#
# Model flexibility: falls back to alternative models if primary is unavailable.
set -uo pipefail

HARNESS_DIR="$(cd "$(dirname "$0")" && pwd)"
CODE_DIR="${CODE_DIR:-$(cd "$HARNESS_DIR/.." && pwd)}"
RESULTS="${RESULTS:-/tmp/build-results.json}"
CORPUS="${CORPUS:-$HARNESS_DIR/corpus.json}"
GOLDEN="${GOLDEN:-$HARNESS_DIR/golden_predictions.json}"
EVAL_DIR="${EVAL_DIR:-/tmp/brk_eval}"
WIKI_DIR="${WIKI_DIR:-$CODE_DIR/wiki}"
STATE_FILE="$EVAL_DIR/loop_state.json"
MAX_ITERS="${MAX_ITERS:-5}"
PASS_THRESHOLD="${PASS_THRESHOLD:-8.5}"

# Model configuration — falls back gracefully
MAIN_MODEL="${MAIN_MODEL:-claude-opus-4-6}"
SUB_MODEL="${SUB_MODEL:-claude-sonnet-5}"
FALLBACK_SUB_MODEL="${FALLBACK_SUB_MODEL:-claude-haiku-4-5-20251001}"
SUB_CLI="${SUB_CLI:-copilot}"

# CI configuration
REPO="${REPO:-}"
BRANCH="${BRANCH:-}"
TARGET_REPO="${TARGET_REPO:-$CODE_DIR}"

log() { echo "$(date '+%H:%M:%S') [v15:iter${ITER:-0}] $*"; }

# ── Model availability check ──────────────────────────────────────
detect_models() {
    log "Checking model availability..."

    # Test sub-agent model via copilot CLI
    if copilot -p "Reply with OK" --model "$SUB_MODEL" --effort low --no-ask-user >/dev/null 2>&1; then
        log "Sub-agent model: $SUB_MODEL ✓ (copilot CLI)"
    elif copilot -p "Reply with OK" --model "$FALLBACK_SUB_MODEL" --effort low --no-ask-user >/dev/null 2>&1; then
        log "Sub-agent model: $SUB_MODEL unavailable, falling back to $FALLBACK_SUB_MODEL"
        SUB_MODEL="$FALLBACK_SUB_MODEL"
    else
        log "ERROR: No sub-agent model available (tried $SUB_MODEL, $FALLBACK_SUB_MODEL)"
        exit 1
    fi

    # Test main model (claude CLI)
    if claude --print --model "$MAIN_MODEL" "Reply with OK" >/dev/null 2>&1; then
        log "Main agent model: $MAIN_MODEL ✓"
    else
        log "WARNING: Main model $MAIN_MODEL may not be available"
    fi
}

# ── Initialize workspace ──────────────────────────────────────────
init_workspace() {
    mkdir -p "$EVAL_DIR/eval" "$EVAL_DIR/eval/tasks"
    mkdir -p "$WIKI_DIR"

    # Only create loop_state.json if not resuming
    if [[ ! -f "$STATE_FILE" ]]; then
        python3 -c "
import json
json.dump({
    'persona': 'evaluator',
    'iteration': 0,
    'status': 'pending',
    'gate': {},
    'evaluation': {},
    'generation': {},
    'ci_run': {}
}, open('$STATE_FILE', 'w'), indent=2)
"
    else
        log "Resuming from existing loop_state.json"
    fi

    # Wiki files persist in CWD — only create if truly missing
    [[ -f "$WIKI_DIR/state.md" ]] || echo "# Loop State
Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Status: initializing" > "$WIKI_DIR/state.md"

    [[ -f "$WIKI_DIR/log.md" ]] || echo "# Iteration Log" > "$WIKI_DIR/log.md"
    [[ -f "$WIKI_DIR/known_issues.md" ]] || echo "# Known Issues" > "$WIKI_DIR/known_issues.md"
    [[ -f "$WIKI_DIR/fixes_tried.md" ]] || echo "# Fixes Tried" > "$WIKI_DIR/fixes_tried.md"

    cp "$RESULTS" "$EVAL_DIR/build-results.json" 2>/dev/null || true

    # Symlink wiki into eval dir so sub-agents can access it
    if [[ "$WIKI_DIR" != "$EVAL_DIR/wiki" ]]; then
        ln -sfn "$WIKI_DIR" "$EVAL_DIR/wiki" 2>/dev/null || true
    fi

    log "Workspace: $EVAL_DIR | Wiki: $WIKI_DIR"
}

# ── Deterministic gate ────────────────────────────────────────────
run_deterministic_gate() {
    log "Running deterministic gate..."
    local gate_out
    gate_out=$(python3 "$HARNESS_DIR/run_gate.py" "$EVAL_DIR/build-results.json" "$CORPUS" \
        --repo "$TARGET_REPO" --golden "$GOLDEN" 2>&1) || true
    echo "$gate_out"

    GATE_SCORE=$(echo "$gate_out" | awk -F': ' '/^SCORE:/{print $2}')
    GATE_ACCEPTED=$(echo "$gate_out" | awk -F': ' '/^ACCEPTED:/{print $2}')
    GATE_FINDINGS=$(echo "$gate_out" | sed -n '/^FINDINGS:/,/^END_FINDINGS/p' | grep '^\- ' || true)

    # Write findings to a temp file to avoid shell quoting issues
    echo "$GATE_FINDINGS" > "$EVAL_DIR/gate_findings.txt"

    _STATE_FILE="$STATE_FILE" _GATE_SCORE="${GATE_SCORE:-0}" _GATE_ACCEPTED="${GATE_ACCEPTED:-False}" _FINDINGS_FILE="$EVAL_DIR/gate_findings.txt" \
    python3 << 'PYEOF'
import json, os
state_file = os.environ["_STATE_FILE"]
score = os.environ["_GATE_SCORE"]
accepted = os.environ["_GATE_ACCEPTED"]
findings_file = os.environ["_FINDINGS_FILE"]
findings = open(findings_file).read().strip() if os.path.exists(findings_file) else ""
s = json.load(open(state_file))
s['gate'] = {
    'deterministic_score': float(score),
    'deterministic_accepted': accepted == 'True',
    'findings': findings
}
json.dump(s, open(state_file, 'w'), indent=2)
PYEOF
    log "Gate: score=$GATE_SCORE accepted=$GATE_ACCEPTED"
}

# ── Decide reasoning effort per persona ───────────────────────────
decide_effort() {
    local persona="$1"
    # Main agent decides effort based on complexity signals from gate
    python3 -c "
import json
state = json.load(open('$STATE_FILE'))
gate = state.get('gate', {})
score = gate.get('deterministic_score', 10)
findings = gate.get('findings', '')

efforts = {
    'enduser_developer': 'high',
    'security_analyst': 'high',
    'pipeline_inspector': 'medium',
    'content_accuracy': 'xhigh'
}

# Escalate effort if gate found issues in this persona's domain
if '$persona' == 'security_analyst' and 'SECURITY' in findings:
    efforts['security_analyst'] = 'max'
if '$persona' == 'pipeline_inspector' and 'PIPELINE' in findings:
    efforts['pipeline_inspector'] = 'high'
if '$persona' == 'enduser_developer' and score < 5:
    efforts['enduser_developer'] = 'xhigh'
if '$persona' == 'content_accuracy' and 'OVERCLAIM' in findings or 'INVENTED' in findings:
    efforts['content_accuracy'] = 'max'

print(efforts['$persona'])
"
}

# ── Run evaluator sub-agent ───────────────────────────────────────
run_sub_evaluator() {
    local persona="$1"
    local effort
    effort=$(decide_effort "$persona")
    log "  Sub-evaluator: $persona (effort=$effort, model=$SUB_MODEL)"

    local persona_prompt
    persona_prompt=$(cat "$HARNESS_DIR/evaluator/${persona}.md")

    local wiki_context=""
    [[ -f "$WIKI_DIR/known_issues.md" ]] && wiki_context="$wiki_context
## Known Issues (from previous iterations and user feedback)
$(cat "$WIKI_DIR/known_issues.md")
"
    [[ -f "$WIKI_DIR/codebase_context.md" ]] && wiki_context="$wiki_context
## Codebase Context
$(cat "$WIKI_DIR/codebase_context.md")
"
    [[ -f "$WIKI_DIR/fixes_tried.md" ]] && wiki_context="$wiki_context
## What Has Been Tried Before
$(cat "$WIKI_DIR/fixes_tried.md")
"

    (cd "$EVAL_DIR" && copilot -p "$(cat <<PROMPT
$persona_prompt

## Data Available in Your Working Directory

- \`build-results.json\` — structured build/test/reachability data for all PRs (GROUND TRUTH)
- \`wiki/state.md\` — current loop state and scores
- \`wiki/known_issues.md\` — issues from previous iterations AND user feedback
- \`wiki/codebase_context.md\` — what the tool does, target repo structure, key files
- \`wiki/fixes_tried.md\` — what was already attempted and results
- \`wiki/log.md\` — iteration history

$wiki_context

Read build-results.json FIRST. Every claim you make must reference specific fields from this file.

This is iteration $ITER of the v15 evaluation loop.

The user has flagged MULTIPLE TIMES across sessions that the evaluator is too soft. Do NOT go easy. If something is broken, say it's broken. If data is missing, that's a P0/P1 gap, not an "area for improvement."

Write your review to: \`eval/${persona}_review.md\`
PROMPT
)" --model "$SUB_MODEL" --effort "$effort" --yolo --no-ask-user 2>&1 | tail -5)

    if [[ -f "$EVAL_DIR/eval/${persona}_review.md" ]]; then
        log "  ✓ $persona review written"
    else
        log "  ✗ $persona review NOT written — creating stub"
        echo "# ${persona} Review — Iteration $ITER
## ERROR: Sub-agent did not produce a review.
## Score: 1/10" > "$EVAL_DIR/eval/${persona}_review.md"
    fi
}

# ── Run main evaluator (Opus cross-check) ─────────────────────────
run_main_evaluator() {
    log "Running main evaluator (Opus cross-check)..."

    local evaluator_prompt
    evaluator_prompt=$(cat "$HARNESS_DIR/evaluator/main_evaluator.md")

    (cd "$EVAL_DIR" && claude -p "You are the main evaluator for iteration $ITER of the breakability v15 loop.

Deterministic gate result: score=$GATE_SCORE accepted=$GATE_ACCEPTED
Gate findings:
$GATE_FINDINGS

Read ALL 4 sub-agent reviews in the eval/ directory:
- eval/enduser_developer_review.md (Sam — developer perspective)
- eval/security_analyst_review.md (Jordan — security)
- eval/pipeline_inspector_review.md (Riley — pipeline execution)
- eval/content_accuracy_review.md (Alex — fact-checking)

Cross-check every finding against build-results.json (ground truth).
Read wiki/ for repeated issues.

Write your consolidated evaluation to: eval/consolidated_evaluation.md
Update loop_state.json: set persona to generator, populate evaluation scores.
Update wiki/state.md, wiki/known_issues.md, wiki/log.md." \
        --model "$MAIN_MODEL" \
        --append-system-prompt "$evaluator_prompt" \
        --allowedTools "Read,Write,Edit,Bash" \
        --dangerously-skip-permissions 2>&1 | tail -20)

    log "Main evaluator complete"
}

# ── Run generator phase ───────────────────────────────────────────
run_generator() {
    log "Running generator phase..."

    local generator_prompt
    generator_prompt=$(cat "$HARNESS_DIR/generator/main_generator.md")

    # Read structured findings from loop_state.json (primary handoff)
    local findings_json
    findings_json=$(python3 -c "
import json, sys
s = json.load(open('$STATE_FILE'))
findings = s.get('evaluation', {}).get('critical_findings', [])
if findings:
    print(json.dumps(findings, indent=2))
else:
    print('NO_STRUCTURED_FINDINGS')
")

    # Build generator prompt from structured JSON or fall back to markdown
    local gen_prompt
    if [[ "$findings_json" != "NO_STRUCTURED_FINDINGS" ]]; then
        gen_prompt="## Structured Findings from Evaluator (loop_state.json)

\`\`\`json
$findings_json
\`\`\`

Apply each fix in the \`required_fix\` field. The file, function, line, and code_snippet are exact.
Check \`positive_controls\` after each fix — those cases MUST NOT regress.
Check \`previous_fix_attempt\` — do NOT repeat failed approaches."
    else
        # Fallback: read markdown evaluation + fix plan
        local gen_input="$EVAL_DIR/eval/consolidated_evaluation.md"
        local fix_plan=""
        [[ -f "$EVAL_DIR/eval/fix_plan.md" ]] && fix_plan="$(cat "$EVAL_DIR/eval/fix_plan.md")"
        gen_prompt="$(cat "$gen_input")

---

The above is the consolidated evaluation. Below is the fix plan.
Follow this plan precisely — do NOT improvise or deviate.

$fix_plan"
    fi

    gen_prompt="$gen_prompt

---

EXECUTION INSTRUCTIONS:

Wiki fixes_tried: $EVAL_DIR/wiki/fixes_tried.md
Wiki known_issues: $EVAL_DIR/wiki/known_issues.md
Loop state: $EVAL_DIR/loop_state.json

Iteration: $ITER
Code dir: $CODE_DIR
Sub-agent model: $SUB_MODEL

DO NOT use govulncheck — CVE data comes from Dependabot alerts via PAT only.

For each finding in the structured JSON (or fix plan):
1. Make the code change exactly as specified
2. Add unit tests covering the fix AND positive controls
3. Run: python3 -m pytest <relevant_test_file> -v
4. If tests pass, move to the next fix
5. If tests fail, fix the issue — do NOT skip or revert

After ALL fixes:
1. Re-compute verdicts: python3 scripts/core/verdict_contract.py /tmp/brk_eval/build-results.json --write
2. Run deterministic gate: python3 harness/run_gate.py /tmp/brk_eval/build-results.json harness/corpus.json --repo $TARGET_REPO
3. Commit all changes with a descriptive message
4. Update $EVAL_DIR/wiki/fixes_tried.md with what you did and outcomes

Update $EVAL_DIR/loop_state.json: set persona to evaluator."

    (cd "$CODE_DIR" && claude -p "$gen_prompt" \
        --model "$MAIN_MODEL" \
        --append-system-prompt "$generator_prompt" \
        --allowedTools "Read,Write,Edit,Bash" \
        --dangerously-skip-permissions 2>&1 | tail -20)

    log "Generator complete"
}

# ── Extract evaluation score ──────────────────────────────────────
get_eval_score() {
    python3 -c "
import json
try:
    s = json.load(open('$STATE_FILE'))
    score = s.get('evaluation', {}).get('overall_score', 0)
    print(float(score))
except:
    print(0.0)
"
}

# ── Trigger CI and wait ───────────────────────────────────────────
trigger_ci() {
    if [[ -z "$REPO" ]] || [[ -z "$BRANCH" ]]; then
        log "No REPO/BRANCH configured — skipping CI trigger"
        return 1
    fi

    log "Triggering CI run on $REPO @ $BRANCH (pr_filter=${PR_FILTER:-all})..."
    local ci_args=(-f skip_agent=false -f batch_count="${CI_BATCH_COUNT:-4}")
    [[ -n "${PR_FILTER:-}" ]] && ci_args+=(-f pr_filter="$PR_FILTER")
    gh workflow run breakability.yml --repo "$REPO" --ref "$BRANCH" \
        "${ci_args[@]}" 2>&1 || {
        log "CI trigger failed"
        return 1
    }

    log "Waiting for CI to start..."
    sleep 15

    local run_id
    run_id=$(gh run list --repo "$REPO" --branch "$BRANCH" \
        --workflow breakability.yml --limit 1 --json databaseId --jq '.[0].databaseId' 2>/dev/null)

    if [[ -z "$run_id" ]]; then
        log "Could not find CI run"
        return 1
    fi

    log "Monitoring CI run $run_id..."
    gh run watch "$run_id" --repo "$REPO" --exit-status 2>&1 || true

    log "Downloading artifacts from run $run_id..."
    rm -rf "$EVAL_DIR/ci-artifacts"
    gh run download "$run_id" --repo "$REPO" --name build-results --dir "$EVAL_DIR/ci-artifacts" 2>&1 || true
    gh run download "$run_id" --repo "$REPO" --name pr-comments --dir "$EVAL_DIR/ci-artifacts/comments" 2>&1 || true

    if [[ -f "$EVAL_DIR/ci-artifacts/build-results.json" ]]; then
        cp "$EVAL_DIR/ci-artifacts/build-results.json" "$EVAL_DIR/build-results.json"
        log "Updated build-results.json from CI run $run_id"
    fi

    # Copy fresh CI-generated comments into eval dir for evaluator
    if ls "$EVAL_DIR/ci-artifacts/comments/"*.md >/dev/null 2>&1; then
        mkdir -p "$EVAL_DIR/eval/current_comments"
        cp "$EVAL_DIR/ci-artifacts/comments/"*.md "$EVAL_DIR/eval/current_comments/" 2>/dev/null || true
        log "Updated $(ls "$EVAL_DIR/ci-artifacts/comments/"*.md 2>/dev/null | wc -l | tr -d ' ') comment files from CI"
    fi

    python3 -c "
import json
s = json.load(open('$STATE_FILE'))
s['ci_run'] = {'run_id': $run_id, 'status': 'completed'}
json.dump(s, open('$STATE_FILE', 'w'), indent=2)
"
    return 0
}

# ══════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ══════════════════════════════════════════════════════════════════

detect_models
init_workspace

PREV_COMMIT=$(cd "$CODE_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "none")

# Check if resuming from generator phase
RESUME_FROM_GENERATOR=false
START_ITER=1
if [[ -f "$STATE_FILE" ]]; then
    RESUME_PERSONA=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('persona','evaluator'))")
    RESUME_ITER=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('iteration', 1))")
    if [[ "$RESUME_PERSONA" == "generator" ]]; then
        RESUME_FROM_GENERATOR=true
        START_ITER="$RESUME_ITER"
        log "Resuming from GENERATOR phase, iteration $START_ITER"
    fi
fi

for ITER in $(seq "$START_ITER" "$MAX_ITERS"); do
    log "═══════════════════════════════════════"
    log "  ITERATION $ITER / $MAX_ITERS"
    log "═══════════════════════════════════════"

    # Skip evaluator if resuming from generator
    if [[ "$RESUME_FROM_GENERATOR" == "true" ]]; then
        RESUME_FROM_GENERATOR=false
        GATE_SCORE=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('gate',{}).get('deterministic_score',0))")
        GATE_ACCEPTED=$(python3 -c "import json; print(json.load(open('$STATE_FILE')).get('gate',{}).get('deterministic_accepted',False))")
        GATE_FINDINGS=""
        EVAL_SCORE=$(get_eval_score)
        log "Skipping evaluator (resuming) — gate=$GATE_SCORE, eval=$EVAL_SCORE"
    else

    # Update iteration state
    python3 -c "
import json
s = json.load(open('$STATE_FILE'))
s['iteration'] = $ITER
s['persona'] = 'evaluator'
s['status'] = 'evaluating'
json.dump(s, open('$STATE_FILE', 'w'), indent=2)
"

    # ── EVALUATOR PHASE ───────────────────────────────────────
    log "── EVALUATOR PHASE ──"

    # 1. Deterministic gate (HARD FLOOR)
    run_deterministic_gate

    if [[ "$GATE_ACCEPTED" == "True" ]]; then
        log "Deterministic gate PASSED — running LLM evaluation for quality"
    else
        log "Deterministic gate FAILED — LLM evaluation will add detail but gate findings are blockers"
    fi

    # 2. Sub-agent reviews (parallel)
    log "Spawning 4 sub-agent reviewers..."
    for persona in enduser_developer security_analyst pipeline_inspector content_accuracy; do
        run_sub_evaluator "$persona" &
    done
    wait
    log "All sub-agent reviews complete"

    # 3. Main evaluator cross-check (Opus)
    run_main_evaluator

    # 4. Check evaluation score
    EVAL_SCORE=$(get_eval_score)
    log "Evaluation score: $EVAL_SCORE (threshold: $PASS_THRESHOLD)"

    PASSES=$(echo "$EVAL_SCORE >= $PASS_THRESHOLD" | bc -l 2>/dev/null || echo 0)
    if [[ "$PASSES" == "1" ]] && [[ "$GATE_ACCEPTED" == "True" ]]; then
        log "╔══════════════════════════════════════╗"
        log "║  EVALUATION PASSED — score=$EVAL_SCORE  ║"
        log "╚══════════════════════════════════════╝"
        break
    fi

    if [[ "$ITER" -eq "$MAX_ITERS" ]]; then
        log "Max iterations reached without passing. Final score: $EVAL_SCORE"
        break
    fi

    fi  # end of evaluator phase (skipped when resuming from generator)

    # ── GENERATOR PHASE ───────────────────────────────────────
    log "── GENERATOR PHASE ──"

    python3 -c "
import json
s = json.load(open('$STATE_FILE'))
s['status'] = 'generating'
json.dump(s, open('$STATE_FILE', 'w'), indent=2)
"

    run_generator

    # Check if code changed
    NEW_COMMIT=$(cd "$CODE_DIR" && git rev-parse --short HEAD 2>/dev/null || echo "none")
    if [[ "$NEW_COMMIT" != "$PREV_COMMIT" ]]; then
        log "Generator committed changes ($PREV_COMMIT → $NEW_COMMIT)"

        # Trigger CI if configured
        if trigger_ci; then
            log "CI completed — new results available"
        else
            log "Skipping CI — will re-evaluate with current results"
        fi
    else
        log "Generator made no code changes"
    fi

    PREV_COMMIT="$NEW_COMMIT"

    # Update state for next iteration
    python3 -c "
import json
s = json.load(open('$STATE_FILE'))
s['persona'] = 'evaluator'
s['status'] = 'pending'
json.dump(s, open('$STATE_FILE', 'w'), indent=2)
"
done

log "═══════════════════════════════════════"
log "  LOOP COMPLETE"
log "═══════════════════════════════════════"
log "Final state:"
cat "$WIKI_DIR/state.md"
echo ""
log "Gate result saved to: $EVAL_DIR/gate-result.json"
log "Evaluation saved to: $EVAL_DIR/eval/consolidated_evaluation.md"
log "Wiki at: $WIKI_DIR/"
