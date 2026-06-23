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

## Known Issues (from validation)

1. **Probe contract mismatch:** Writes `behavioral_grade`, renderer expects `deterministic.probe`
2. **AI workflow incomplete:** `reconcile_adjudication.py` needs `--verdicts` file, but workflow doesn't generate one
3. **AI contract mismatch:** Writes `ai_adjudication`, renderer expects `ai_verdict`
