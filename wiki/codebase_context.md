# Codebase Context

## What This Tool Does

The breakability tool analyzes Dependabot PRs to determine if dependency version bumps will break the target codebase. It runs as a GitHub Actions workflow, builds/tests with the new dependency, and posts PR comments with merge recommendations (SAFE/REVIEW/BLOCKED).

## Target Repository

**CSC-Security-sandbox/vcp-fresh-breakability** — Go monorepo with go.work

### Workspace Modules
- `root` (github.com/vcp-vsa-control-Plane/vsa-control-plane)
- `automations/tstctl`
- `cicd`
- `core` — business logic
- `database` — DB layer, directly imports pgx/v5
- `hyperscaler` — cloud provider integrations
- `lib` — shared library
- `vcm-proxy` — proxy service, complex dependency tree (k8s, gorm, temporal)
- `vcp-core` — core VCP functionality

### Key Dependencies Under Analysis
- `github.com/jackc/pgx/v5` — PostgreSQL driver (PRs 9, 23, 32 — coordinated upgrade)
- `golang.org/x/net` — networking library
- Various GitHub Actions version bumps
- Multiple Go module indirect dependencies

### Infrastructure
- Self-hosted EC2 runners (ARM64)
- Missing: gcc (breaks CGO/race detector), govulncheck
- GitHub token: needs security_events scope for Dependabot alert correlation

## Key Source Files

### Pipeline (CI/CD)
- `.github/workflows/breakability-reusable.yml` — main workflow, handles build/test/comment posting
- `scripts/ai/generate_ai_comments.py` — AI-powered comment generation (798 lines), has `_validate_comment()` with 13 golden feature checks
- `scripts/breakability_analyst.py` — template fallback renderer (thin, no AI analysis)
- `prompts/breakability-prompt.md` — 1,257-line domain knowledge prompt for AI agent

### Evaluation (Harness)
- `harness/run_gate.py` — deterministic acceptance gate (corpus scoring + pipeline/security checks)
- `harness/corpus.json` — 15 verified test cases with ground truth labels
- `harness/golden_predictions.json` — 7 pinned predictions for regression guard
- `harness/evaluator/` — v15 persona instructions for LLM evaluation sub-agents
- `harness/generator/` — v15 generator instructions
- `harness/v15_loop.sh` — v15 multi-agent loop orchestrator

### Security
- `scripts/security_posture_scan.py` — fetches Dependabot alerts via BREAKABILITY_PAT
- `scripts/rendering/merge_plan.py` — renders merge plan; when alerts_unavailable=true, shows single line about missing permission

### Verdict Logic
- `scripts/verdict_contract.py` — typed policy layer for verdict derivation
- `scripts/breakability_eval.py` — corpus scorer (CorpusCase, Scorer classes)

## PR Comment Architecture

### Full AI Mode
1. `generate_ai_comments.py` loads `breakability-prompt.md` (1,257 lines of domain knowledge)
2. For each PR, builds a per-PR prompt with build data, reachability, changelog, security
3. AI agent generates detailed comment (150+ lines) with: signal table, bash verification blocks, reachability analysis, behavioral assessment, changelog review, security correlation
4. `_validate_comment()` checks 13 golden features before accepting the comment
5. Comment posted to PR with "Model: claude-*" footer

### Template Fallback Mode
1. `breakability_analyst.py` or `post-fallback-comments.sh` runs instead
2. Fills structured data into a thin markdown template
3. No changelog analysis, no behavioral assessment, no security correlation
4. Comment posted with "Model: template-fallback" footer
5. Much shorter, missing most analysis sections
