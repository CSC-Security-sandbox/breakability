# Iteration Log

## ndm-fresh-breakability run (2026-07-17)
- CI run: 29556448596, 41 PRs, 4 batches all success
- Finalize: success, AI comments generated (not fallback), comments posted
- Pre-loop gate (manual): 8.0/10, one overclaim (PR#44)
- Loop iter 1 gate: 2.0/10 (stale wiki + wrong --repo path caused oscillation)
- Restarting with clean state and TARGET_REPO fix

## Iteration 1 — FAIL (2.0/10)
- Deterministic gate: 7.0/10, REJECTED (PR#44 overclaim, ALERTS_BLIND)
- Sub-agent reviews: Sam 4/10, Jordan 2/10, Riley 5/10, Alex 2/10
- Consolidated: 2.0/10, FAIL (threshold 8.5)
- Reviewer errors: None this round (Riley's prior behavioral_grade error not repeated)
- 7 critical findings (C1-C7), 5 improvements (I1-I5)
- New finding: PR36/PR45 phantom REVIEW (PHANTOM_REVIEW) — comment body inverts merge_risk.tag
- Count correction: WRONG_DEDUP is 8 PRs (4 pairs), not 7
- Top blockers: (1) hard_fix_floor false blocks, (2) CVE wiring gap, (3) PR#44 overclaim x3
- Next: generator must fix C1-C7 in priority order
- Handoff: persona → generator

## Iteration 2 — FAIL (2.0/10)
- Deterministic gate: 9.0/10, ACCEPTED (overridden by evaluation to FAIL)
- Sub-agent reviews: Sam 2/10, Jordan 3/10, Riley 4/10, Alex 1/10
- Consolidated: 2.0/10, FAIL (threshold 8.5)
- Reviewer errors: None. All 4 reviewers' claims verified against build-results.json.
- Gate vs reality gap: Gate scored re-derived JSON (verdict_generation=7). All 41 comment files unchanged (mtime 2026-07-17 14:28:25). Generator fixed code logic but never regenerated comments.
- Data-layer fixes CONFIRMED WORKING: hard_fix_floor guard, CVE version-range filter, PR#44 symbol override
- NEW issues: STALE_COMMENTS, CVE_COUNT_ZERO, DUAL_MERGE_RISK, GO_PROBE_FABRICATED
- REPEATED: ALERTS_BLIND (x5), SEC_POSTURE_ZEROS (x3), UNTESTED_SAFE (x3), OVERCLAIM (x4)
- Critical lesson: Fixing verdict logic without regenerating comments produces invisible fixes.
- Handoff: persona → generator

## Iteration 3 — FAIL (1.0/10)
- Deterministic gate: 9.0/10, ACCEPTED (overridden by evaluation to FAIL)
- Sub-agent reviews: Sam 1/10, Jordan 2/10, Riley 4/10 (adjusted→3), Alex 1/10
- Consolidated: 1.0/10, FAIL (threshold 8.5)
- Score declined from 2.0→1.0 (end-user 2→1, accuracy 2→1, pipeline 4→3, security 3→2)
- ROOT CAUSE: STALE_COMMENTS — 3rd consecutive iteration. 12 code fixes across 3 iterations, ZERO deployed to comments.
- ESCALATED: REACHABILITY_OVERCLAIM from P2 to P1 (second confirmed instance)
- REPEATED: STALE_COMMENTS (x3), FALSE_BLOCK (x3), WRONG_DEDUP (x3), VERDICT_MISMATCH (x3), PHANTOM_REVIEW (x3), ALERTS_BLIND (x6), SEC_POSTURE_ZEROS (x4), OVERCLAIM (x4), GO_PROBE (x2)
- ACTION: REGENERATE ALL 41 COMMENTS — the ONLY priority
- Handoff: persona → generator

## Iteration 5 — FAIL (2.0/10)
- Deterministic gate: 9.0/10, ACCEPTED (overridden by evaluation to FAIL)
- Sub-agent reviews: Sam 3/10, Jordan 2/10, Riley 3/10, Alex 4/10
- Consolidated: 2.0/10, FAIL (threshold 8.5)
- Reviewer errors: Sam claimed "changelog null for all 41 PRs" — WRONG (36/41 have changelogSignal). Underlying concern valid but count overstated.
- **STALE_COMMENTS FIXED** — all 41 comments regenerated, mtime 2026-07-20 15:21
- Score improved from 1.0→2.0. Significant verified progress:
  - Verdict-header accuracy: 30/41 → 40/41
  - Reachability scoping: 39/41 → 41/41
  - Dedup advice: 0/8 correct → 8/8 correct
  - UNTESTED_SAFE annotation: invisible → 9/9 visible
- BUT comment regeneration revealed 2 NEW P0 defects hidden by staleness:
  1. **CHANGELOG_FABRICATION (P0)**: 15/41 comments wrong. 4 PRs with status=breaking show "No breaking changes" (self-contradicts verdict reason). 11 missing-status PRs assert safety with no data.
  2. **CVE_OVERCLAIM (P0)**: 10/12 CVE-bearing PRs falsely claim "remediates" when base version outside vulnerable range. Verdict engine has this right but comment renderer doesn't check.
- NEW P1 findings: ISSUE_NUMBER placeholder (41/41), merge plan security empty, PR#29 verdict bug, pipeline provenance mismatch
- ESCALATED: ACTIONS_VERLEVEL_BUG from P3 to P1 (5-PR pattern, was PR9-only)
- REPEATED: ALERTS_BLIND (x7), GO_PROBE (x3)
- 9 critical findings (C1-C9), 8 improvements (I1-I8)
- Priority action: (1) Fix changelog rendering to read status field, (2) Fix CVE applicability check in comment renderer, (3) Fix ISSUE_NUMBER placeholder, (4) Fix footer, then regenerate all 41 comments
- Handoff: persona → generator

## Iteration 6 — FAIL (2.0/10)
- Deterministic gate: 7.5/10, ACCEPTED
- Sub-agent reviews: Sam 6/10, Jordan 2/10, Riley 4/10, Alex 6/10
- Consolidated: 2.0/10, FAIL (threshold 8.5)
- Score floor: Security (2/10) — merge plan security fix in dead code + ALERTS_BLIND 8th occurrence
- Reviewer errors: None material. Riley's main_test_exit=-1 vs actual None is trivial.
- **All iter-5 fixes verified HOLDING** — changelog, CVE applicability, ISSUE_NUMBER, footer, PR#29, Actions verlevel. Zero regressions. Real, durable progress.
- Per-PR comments improved significantly (Sam 3→6, Alex 4→6). Pipeline partially improved (Riley 3→4).
- BUT 8 NEW critical findings discovered, in two major areas:
  1. **Per-PR comment renderer sibling bugs**: API_DIFF_FABRICATION (14 PRs, P0), PR43_VERDICT_FABRICATION (P0), ACTIONS_NPM_COMMANDS (5 PRs, P1), TEST_ROW_PRE_EXISTING (15 PRs, P1)
  2. **Merge-plan renderer bugs**: MERGE_PLAN_SEVERITY_FABRICATION (P0), MERGE_PLAN_DEAD_CODE (P0, invalidates iter-5 "FIXED"), MERGE_PLAN_CVE_OVERCLAIM (P0)
  3. **Verdict policy**: CVE_FLOOR_VERDICT_INCONSISTENCY (P1)
- REPEATED: ALERTS_BLIND (x8), GO_PROBE_FABRICATED (x4), MERGE_PLAN_SECURITY_EMPTY re-opened (x2)
- KEY LESSON: Iter-5 merge-plan security fix was applied to wrong file. The workflow calls generate_ai_merge_plan.py, not rendering/merge_plan.py. Fixes MUST target the file wired into the production workflow.
- 11 critical findings (C1-C11), 7 improvements (I1-I7)
- Priority action: (1) Fix 4 per-PR comment renderer bugs, (2) Fix 3 merge-plan renderer bugs, (3) Fix 2 verdict/policy inconsistencies, (4) Regenerate all 41 comments
- Handoff: persona → generator

## Iteration 7 — Generator (2026-07-20)
- Persona: generator
- Fixes applied: 9/11 critical findings (C1-C9)
- Files modified: generate_ai_comments.py, verdict_contract.py, merge_plan.py, generate_ai_merge_plan.py
- Gate: 7.5/10, ACCEPTED (FALSE_GREEN=0, FALSE_BLOCK=0, OVERCLAIMS=0)
- Tests: 223/223 pass, 0 regressions
- Verdict distribution: SAFE=14, REVIEW=18, BLOCKED=9
- C2 partial: verdict stays REVIEW per corpus (reason enriched with probe evidence)
- Not fixed: C10 (ALERTS_BLIND, 8th occurrence), C11 (GO_PROBE_FABRICATED, 4th occurrence)
- Commit: 7485439 on branch cleanup
- Handoff: persona → evaluator

## Iteration 1 (ndm, previous) — Evaluator — FAIL (2.0/10)
- Deterministic gate: 7.5/10, ACCEPTED
- Sub-agent reviews: Sam 4/10, Jordan 2/10, Riley 5/10, Alex 4/10
- Consolidated: 2.0/10, FAIL (threshold 8.5)
- Score floor: Security (2/10) — live PR#109/110 show REVIEW while local says BLOCKED, ALERTS_BLIND 9th occurrence, merge plan CVE column empty
- Reviewer errors: Jordan claimed 13 live/local mismatches (6 verified, core finding correct — PARTIAL ERROR)
- All iter-6/7 fixes verified HOLDING — no regressions detected
- 7 NEW critical findings in per-PR comment renderer
- REPEATED: ALERTS_BLIND (x9), GO_PROBE_FABRICATED (x5)
- KEY LESSON: Data-layer fixes are invisible if _fallback_comment() never reads the fixed fields.
- Handoff: persona → generator

## Iteration 1 (VCP) — Evaluator — FAIL (2.0/10)
- Target: CSC-Security-sandbox/vcp-fresh-breakability (17 PRs, Go monorepo)
- CI run: 29805118237, timestamp 2026-07-21T05:50:29Z
- Deterministic gate: 4.5/10, REJECTED
- Sub-agent reviews: Sam 3/10, Jordan 2/10, Riley 5/10, Alex 3/10
- Consolidated: 2.0/10, FAIL (threshold 8.5)
- Score floor: Security (2/10) — verdict-label chaos on all 6 critical-CVE PRs, ALERTS_BLIND 10th occurrence
- Reviewer errors: Riley claimed 8/17 test.ran=true (actual 7/17, minor)
- 11 critical findings (C1-C11), 5 improvements (I1-I5)
- KEY NEW findings:
  1. VERDICT_HEADER_MISMATCH — 4/6 BLOCKED PRs show "REVIEW RISK" (P0, C1)
  2. PR#54 "MERGE IMMEDIATELY" on BLOCKED/P0 verdict (P0, C2)
  3. PR#8 broken artifact — leaked LLM narration, fabricated URLs (P0, C3)
  4. CVE-floor reason drops probe evidence for same_behavior=False (P1, C5)
  5. merge_risk.tag not escalated on CVE floor (P1, C9)
  6. PR#8 PACKAGE-MISMATCH caveat hidden (P1, C6)
  7. govulncheck recommended despite permanent ban (P1, C11)
  8. Actions PRs cite Node.js in Go-only repo (P1, C10)
- REPEATED: ALERTS_BLIND (x10), VERDICT_MISMATCH (x5)
- ROOT CAUSE: VCP comments were generated in CI run 29805118237, which ran BEFORE or WITHOUT many ndm iter 1-7 fixes propagating to VCP comments. This is the STALE_COMMENTS pattern recurring on a new target.
- CRITICAL ACTION: Regenerate all 17 VCP comments using current codebase (post-iter-7 fixes). Then fix C5 (reason enrichment for same_behavior=False), C9 (merge_risk escalation), C6 (reconciliation_note rendering), C11 (govulncheck deny list).
- Handoff: persona → generator
