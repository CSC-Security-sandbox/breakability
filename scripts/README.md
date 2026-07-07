# Breakability Analysis Scripts

**Single Source of Truth** - All analysis scripts centralized here, then synced to deployment repos.

## Directory Structure

```
scripts/
├── verdict_contract.py          # Authoritative verdict computation
├── differential-probe.py         # Behavioral probe (npm runtime + Go dynamic)
├── reconcile_adjudication.py    # AI arbiter reconciliation
├── breakability_analyst.py      # Rich comment renderer (13 sections)
├── build-check.sh               # Deterministic layer orchestrator
├── evidence_contract.py         # Typed policy engine
├── policy_lowering.py           # Policy decision mapper
└── [45+ analysis scripts]
```

## Sync to Deployment Repos

```bash
# Sync to both NDM and VCP
./sync-to-deployments.sh both

# Sync to NDM only
./sync-to-deployments.sh ndm

# Sync to VCP only
./sync-to-deployments.sh vcp
```

## Version Control Workflow

1. **Make changes** in `breakability/scripts/` (single source of truth)
2. **Test locally** with validation scripts
3. **Commit** to breakability repo
4. **Sync** to deployment repos with `sync-to-deployments.sh`
5. **Commit** deployment repos
6. **Trigger** workflow runs to verify

## Key Contract Points

### verdict_contract.py
- **Input:** `build-results.json` with `.build`, `.test`, `.deterministic` data
- **Output:** Enriches with `.verdict_v2` field
- **CLI:** `python3 verdict_contract.py <file> --write`

### differential-probe.py
- **Input:** `build-results.json` with npm package metadata
- **Output:** Currently writes `.behavioral_grade` (NEEDS FIX: should write `.deterministic.probe`)
- **CLI:** Uses env vars `DP_RESULTS`, `DP_DETERMINISTIC_ONLY`, `DP_MAX_PRS`

### reconcile_adjudication.py
- **Input:** `build-results.json` + optional `--verdicts` file
- **Output:** Writes `.ai_adjudication` (NEEDS FIX: renderer reads `.ai_verdict`)
- **CLI:** `python3 reconcile_adjudication.py <file> --write [--verdicts <file>]`

### breakability_analyst.py
- **Input:** `build-results.json` with all enriched fields
- **Output:** Markdown comments (13 sections)
- **CLI:** `python3 breakability_analyst.py <file>`

## Pipeline Script Roles

### Active in Reusable Workflow (`breakability-reusable.yml`)

| Script | Workflow Step | Role |
|--------|-------------|------|
| `build-check.sh` | Run deterministic analysis | Per-PR build/test/reachability/API diff |
| `merge-results.sh` | Merge batch results | Combines per-batch JSON into single results file |
| `verdict_contract.py` | Generate/reconcile verdicts | Authoritative verdict (SAFE/REVIEW/BLOCKED) — called pre-probe and post-probe |
| `differential-probe.py` | Run behavioral probe | Runtime SHA256 + export comparison between old/new versions |
| `generate_ai_comments.py` | Generate AI comments | AI-powered PR comment generation with validation gate + template fallback |
| `breakability_analyst.py` | Fallback to template renderer | Template-based PR comments when AI is unavailable |
| `generate_ai_merge_plan.py` | Update merge plan | AI-enriched merge plan with template fallback |

### Auxiliary Scripts (not called by reusable workflow)

| Script | Status | Notes |
|--------|--------|-------|
| `reconcile_adjudication.py` | Auxiliary | AI arbiter reconciliation — verdict reconciliation is handled by `verdict_contract.py --write` in the reusable workflow. Kept for composite action path and future use. |
| `generate_ai_verdicts.py` | Auxiliary | Standalone AI verdict generation — verdicts come from `verdict_contract.py` in the reusable workflow. Kept for composite action path. |

### Support Libraries

| Script | Role |
|--------|------|
| `ai_backend.py` | Unified AI backend (live/replay/record modes) |
| `evidence_contract.py` | Evidence bundle validation dataclasses |
| `policy_lowering.py` | Policy decision engine for verdict computation |
| `ecosystem_adapters.py` | Multi-ecosystem support (npm, gomod, pip, actions, docker, maven) |

## Known Issues (from validation)

1. ~~**Probe contract mismatch:** Writes `behavioral_grade`, renderer expects `deterministic.probe`~~ (resolved — normalizer handles both)
2. **AI workflow incomplete:** `reconcile_adjudication.py` needs `--verdicts` file, but workflow doesn't generate one (intentional — not used in reusable workflow)
3. ~~**AI contract mismatch:** Writes `ai_adjudication`, renderer expects `ai_verdict`~~ (resolved — normalizer handles both)
