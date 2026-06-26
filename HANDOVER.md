# BREAKABILITY PROJECT — AGENT HANDOVER
_Last updated: 2026-06-25 11:36 IST_

This document gives a new agent everything needed to resume work on the
Breakability dependency-upgrade analysis system. Read it top to bottom before
touching code.

---

## 1. WHAT THE PRODUCT IS

A hybrid **deterministic + AI** system that analyzes **Dependabot PRs** (Node.js
and Go ecosystems) and produces a decisive verdict per PR so developers don't
have to manually review every upgrade.

**Goal:** cut 80–85% of developer review work. Every PR should land in one of:
- **SAFE** → auto-merge
- **REVIEW** → human looks (worst case; minimize these)
- **BUILD_FAILS / BLOCKED** → do not merge

It runs on Dependabot PRs in GitHub Actions, posts a rich analysis comment on
each PR, and produces a merge plan.

**Evidence layers (7):** build, tests, API diff, changelog signal, reachability
(import/callsite), behavioral probe (runtime shape diff), AI arbiter. A strict
precedence hierarchy (fail-safe) collapses them into one verdict.

---

## 2. CODEBASE LOCATIONS (local: `/Users/hpoornac/code/`)

| Repo dir | GitHub remote | Role |
|---|---|---|
| `breakability/` | `CSC-Security-sandbox/breakability` | **Central source of truth** for all scripts |
| `ndm-fresh-breakability/` | `CSC-Security-sandbox/ndm-fresh-breakability` | **Primary test/deploy repo** (Node.js). PR #67 lives here |
| `vcp-fresh-fix/` | `CSC-Security-sandbox/vcp-fresh-breakability` | Secondary test/deploy repo (Go) |
| `opencode-docker/` | — | Holds the Ralph-style **v10 review loop** (`v10_loop_copilot.sh`) |
| `brk-*` (many dirs) | various | Per-axis worktrees/experiments (callgraph, cve, reach, probe, etc.). Not primary. |

**Gold standard source repos (DO NOT deploy to, reference only):**
- `CSC-Security-sandbox/ndm-breakability-test` — **the correct gold standard** (Node)
- `CSC-Security-sandbox/vcp-vsa-breakability-test` — older/complex format (NOT the target)

### Key scripts (in `<repo>/.github/scripts/`)
The deployed copies live in `ndm-fresh-breakability/.github/scripts/`. The
authoritative copies live in `breakability/`. **Changes must be synced** between
them (centralization was an ongoing effort — see checkpoint 022).

Pipeline-critical scripts:
- `build-check.sh` (234 KB) — deterministic analysis. Writes `data["prs"][pr_num]`. Populates `deterministic.usages`, `deterministic.files_importing`, build/test/api-diff/changelog/cve.
- `verdict_contract.py` — authoritative verdict (`verdict_v2`). Reads `results` array, writes `prs` dict.
- `differential-probe.py` (61 KB) — behavioral probe (npm runtime-shape diff). Reads/writes `data["prs"]`. Sets `behavioral_grade`. Runs in `DP_DETERMINISTIC_ONLY=true` mode in CI.
- `policy_lowering.py` — enriches/lowers verdicts. Reads `prs` OR `results`.
- `breakability_analyst.py` (56 KB) — **renders the PR comment** (12 sections + footer). `render_pr_comment()` at line ~1214; `main()` at ~1244.

---

## 3. RUNNERS & INFRA

- **GitHub Actions self-hosted runner (NDM):** `runner-ndm-ip-172-31-67-67` — online, on AWS EC2 (172.31.67.67). This is what executes the NDM workflow.
- **AWS:** instances were `c5.4xlarge`. Earlier in session there were 3 runners (~$1.36/hr total): 2 VCP (idle since June 23, recommended terminating one to save ~$16/day) + 1 NDM (active).
  - ⚠️ **AWS CLI session is EXPIRED.** Run `aws login` / reauth (SSO) before any `aws ec2 ...` calls. Could not enumerate live instances this session.
- **Workflow trigger:** `.github/workflows/breakability-agent.yml` — schedule is **disabled** (commented cron). Only `workflow_dispatch` (manual). Trigger with:
  ```bash
  cd ~/code/ndm-fresh-breakability && gh workflow run breakability-agent.yml --ref main
  ```
- **E2E runtime:** ~35 min. Discovers PRs, splits into 4 batches (parallel deterministic), then a single `merge-and-analyze` job runs probe + AI + analyst.
- **Pipeline order in `merge-and-analyze`:**
  1. `build-check.sh`
  2. `verdict_contract.py /tmp/build-results.json --write`
  3. `differential-probe.py`  (env `DP_DETERMINISTIC_ONLY=true`, `DP_RESULTS=/tmp/build-results.json`)
  4. `breakability_analyst.py /tmp/build-results.json`
  5. comment posting

---

## 4. GOLD STANDARD (what "done" looks like)

**CORRECT gold standard = `ndm-breakability-test` PR #208:**
https://github.com/CSC-Security-sandbox/ndm-breakability-test/pull/208#issuecomment-4737308189

- ~42 lines, simple, **content-rich** (real breaking changes, migration notes, behavioral verdict).
- The point is **substance, not section count.**

⚠️ **Do NOT chase the `vcp-vsa-breakability-test` PR #208 format** (408 lines, 56
backticks). That was a wrong target earlier in the session and caused
over-structuring.

Other references stored in session files:
- `files/GOLD_STANDARDS.md`, `files/pr1-gold-standard.md`, `files/pr208-gold-standard.md`, `files/sample-output/comments/*.md`

**Our current PR #67 comment** (pre-fix, the one user keeps citing):
https://github.com/CSC-Security-sandbox/ndm-fresh-breakability/pull/67#issuecomment-4788675570

---

## 5. CURRENT CRITICAL ISSUE (in flight RIGHT NOW)

**User complaint:** PR #67 comment shows NO AI layer — changelog "NOT AVAILABLE",
behavioral probe "NOT RUN", AI arbiter "NOT-APPLICABLE". 248 lines of structure,
zero substance.

### Root cause found this session: **SCHEMA MISMATCH**
- Writers (`build-check.sh`, `verdict_contract.py`, `differential-probe.py`) write to `data["prs"][pr_num]` (**dict keyed by PR number**).
- `breakability_analyst.py` read **only** `data.get("results", [])` (**array**).
- → Probe/changelog/AI data never reached the renderer → all "NOT RUN" stubs.

Logs confirmed probe DID run: `[differential-probe] PR 67: ... uuid 11.1.1->14.0.1`
and `committed behavioral grades for 31 ... PR(s)` — but data landed in `prs`,
which the analyst ignored.

### Fix deployed this session — commit `ae2ab6faf`
In `breakability_analyst.py` `main()`, now tries **both** schemas:
```python
prs_dict = data.get("prs", {})
results_array = data.get("results", [])
if prs_dict:
    results = []
    for pr_num_str, pr_data in prs_dict.items():
        if isinstance(pr_data, dict):
            pr_data.setdefault("pr_num", pr_num_str)
            results.append(pr_data)
elif results_array:
    results = results_array
else:
    # error
```

### Status as of 11:36 IST
- ✅ Fix committed + pushed (`ae2ab6faf`).
- 🔄 **Validation CI run `28149685023` is IN PROGRESS** (manually triggered 11:16 IST, ~35 min → ETA ~11:50 IST).
- ❗ User is looking at the OLD comment from completed run `28147777734` (finished 10:59, before the fix). The new run hasn't posted yet.

### NEXT STEPS (do this when run 28149685023 completes)
1. `gh run view 28149685023 --json status,conclusion`
2. Re-fetch PR #67 comment; confirm:
   - `### 🔬 Behavioral Probe` shows **SAME/DIFFERENT** (not "NOT RUN")
   - `### 📋 Changelog Analysis` shows real bullets (not "NOT AVAILABLE")
   - AI arbiter has reasoning where applicable
3. If still empty: verify the analyst is actually reading `prs` (add a one-line stderr `len(prs_dict)` debug), and confirm `differential-probe.py` ran BEFORE analyst and persisted to the same `/tmp/build-results.json`.
4. **Separate possible bug:** changelog "NOT AVAILABLE" may be its own issue (changelog fetch/`changelogSignal` not populated for uuid) — verify independently from the probe fix.
5. Spot-check 3–5 other PRs (66 jwks-rsa, 65/64 @types/node) for probe content.
6. Clean up any temp monitor scripts in `/tmp`.

---

## 6. RELEVANT SCHEMA NOTES (gotchas)

- `build-results.json` has BOTH `prs` (dict, authoritative) and historically a `results` array (legacy). Scripts are inconsistent about which they read. When adding code, **prefer `prs` and fall back to `results`** (the pattern `policy_lowering.py` already uses: `results.get("prs") or results.get("pr_results") or results.get("results")`).
- Reachability key-name mismatch (historical bug source):
  - Renderer: `det.get("import_files")`
  - Normalizer: `det.get("files_importing")`
  - These are different keys — be careful.
- `_normalize_reachability()` (analyst ~line 1037): reachability is driven **solely** by `usages` (changed-symbol callsites), NOT `import_files`. An empty `usages` list means NOT REACHED even if the package is imported. This was the PR #67 uuid fix (iter 8.7–8.9).
- Probe candidate filter (`differential-probe.py`): `is_npm_probe_candidate(pr)` requires `ecosystem == "npm"` and non-empty package/from/to. `is_residual(pr)` requires `declared_break_reachability.reachability_kind == "import"` and `prod_reachable`. In `DP_DETERMINISTIC_ONLY` mode only npm candidates are graded.

---

## 7. THE REVIEW LOOP (Ralph-style "v10")

- Location: `~/code/opencode-docker/v10_loop_copilot.sh` (and session-built variants like `~/code/breakability/v10_unified_loop.sh`).
- Pattern: review → fix → CI → re-review. 3 reviewers (adversarial/rootcause, code-review, end-user) score independently; gate = min_score ≥ target; a fix agent applies changes; persistent ledger at `/tmp/v10_ledger.md` prevents re-reporting resolved issues.
- Model: `claude-opus-4.8`.
- **Known problems with the loop (why it missed the AI-layer gap):**
  1. Reviewers scored **"sections present"** not **"content quality/substance"** → empty stubs passed.
  2. LLM scoring frequently returned **empty SCORE values** (parse errors) → gate never reliably converged.
  3. `MAX_ITERS` (3) stopped it early.
  4. **`gh copilot` invocation flags were wrong** in the session-built loops: it does **not** accept `-m` / `--model`. Use the correct Copilot CLI invocation. Verify flags before relaunching.
- **Recommendation for next agent:** if you relaunch the loop, fix the scoring rubric to assert on CONTENT (e.g., behavioral probe != "NOT RUN", changelog has ≥1 bullet, AI arbiter populated for REVIEW PRs) and fix the CLI flags first.

---

## 8. SESSION CHECKPOINTS (full list)

In `~/.copilot/session-state/3bf7c685-5c9f-4bf6-9c80-06f53938dd10/checkpoints/`
(`index.md` has the index). Most relevant recent ones:

- 028 — PR #67 uuid bug fixed (claimed gold standard reached) ← **but AI-layer gap remained, see §5**
- 027 — Iteration 8.7 OR-operator fix (still failing at that point)
- 026 — Iterations 8.4–8.5 recommendation fixes
- 025 — Iteration 8.2 Phase 3 callsite wiring
- 024 — review cycle found 11 critical issues
- 023 — continuous loop with active monitoring
- 022 — P0 blockers fixed + scripts centralized
- 021 — rich comment renderer implementation started
- 020 — fresh repos scripts + AI workflow fixed
- 019 — E2E fresh repos scripts fixed
- 018 — E2E test working on fresh NDM
- 017 — runners launched, gh cli missing
- 015 — NDM E2E complete, gold standard
- 014 — NDM full corpus run, format fix
- 010–013 — Node/TS breakability, npm api-diff, changelog/probe signals, false-positive repair
- 005 — call-graph prover built
- 001–004 — single authoritative verdict, AI backend/replay cassettes/M8, copilot backend full-corpus MVP gating, PR#11 structural-break fix

Earlier history is summarized in the session summary block (iterations 8.5–8.9,
AWS runner analysis, gold-standard confusion correction).

---

## 9. KEY FACTS / CREDENTIALS / ENV

- GitHub org: `CSC-Security-sandbox`. Use `gh` CLI (authenticated).
- Local dev env vars (from project guide): `GHVSA_PAT=$(gh auth token)`, `DB_PASSWORD`, `VSA_NODE_PASSWORD` (these are for the unrelated VCP control-plane guide; not needed for breakability CI).
- AWS: SSO/login expired — reauth before EC2 ops.
- `build-results.json` lives at `/tmp/build-results.json` on the runner during a run (not committed).
- Time pressure noted by user: Opus/GPT model access was expected to be cut "today" — prioritize landing the AI-layer fix and validating PR #67.

---

## 10. ONE-LINE RESUME FOR NEXT AGENT

> Schema-mismatch fix (`ae2ab6faf`) is deployed; validation run **28149685023**
> is running. When it finishes, confirm PR #67 now shows real Behavioral Probe +
> Changelog + AI content (not "NOT RUN"); if changelog still "NOT AVAILABLE",
> debug `changelogSignal` population separately. Then spot-check other PRs and
> declare done.
