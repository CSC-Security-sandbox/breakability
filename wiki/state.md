# Loop State

## Current: ITERATION 1 (VCP) — FAIL (2.0/10), PENDING GENERATOR
- Target repo: CSC-Security-sandbox/vcp-fresh-breakability (17 PRs, Go monorepo)
- Deterministic gate: 4.5/10, REJECTED
- Evaluator score: 2.0/10, FAIL (threshold 8.5)
- Score floor: Security (2/10) — verdict-label chaos on all 6 critical-CVE PRs, ALERTS_BLIND 10th occurrence
- 11 critical findings (C1-C11), 5 improvements (I1-I5)
- Next persona: generator
- Updated: 2026-07-21

## Score Breakdown
- End-User (Sam): 3/10 — verdict header contradicts ground truth on 4/6 BLOCKED PRs, PR#54 MERGE IMMEDIATELY, PR#8 broken
- Security (Jordan): 2/10 — ALERTS_BLIND 10th occurrence, verdict chaos on all 6 P0 PRs, zero dynamic vuln scanning
- Pipeline (Riley): 5/10 — 24% builds never ran, 0% L3+, CVE-floor reason drops probe evidence, changelog 82% missing
- Accuracy (Alex): 3/10 — 4/17 comments contradict verdict_v2, PR#54 MERGE IMMEDIATELY on BLOCKED/P0, PR#8 broken artifact

## Top Blockers for Generator
1. **REGENERATE ALL 17 VCP COMMENTS** — most critical single action. ndm iter 1-7 fixes never applied to VCP comments (P0)
2. **CVE-floor reason drops probe evidence** — same_behavior=False branch not enriched (P1, C5)
3. **merge_risk.tag not escalated on CVE floor** — all 6 BLOCKED PRs show Medium (P1, C9)
4. **reconciliation_note not rendered** — PR#8 HIGH confidence with PACKAGE-MISMATCH caveat hidden (P1, C6)
5. **govulncheck recommended despite ban** — PRs 10,53,54 instruct installation (P1, C11)
6. **Actions PRs cite Node.js in Go-only repo** — 5 PRs with irrelevant boilerplate (P1, C10)
7. **ALERTS_BLIND** — 10th occurrence, infrastructure fix committed but unverified (P1, C4)

## Confirmed Working (from ndm loop, should propagate on regeneration)
- All ndm iter-5 fixes: changelog, CVE applicability, ISSUE_NUMBER, footer model name, PR#29, Actions verlevel
- All ndm iter-6 fixes: API_DIFF, test row pre_existing label, CVE floor BLOCKED, merge plan severity
- All ndm iter-7 fixes: API_DIFF None guard, BLOCKED evidence, confidence column, SAFE+pre_existing, merge_risk visible, cross-PR deps, footer date, merge plan CVE column
- hard_fix_floor pre_existing guard
- _enforce_verdict_floor() — works when called (verified by Jordan)
- Fallback comment generator with full signal data

## Reviewer Error Log
- Riley claimed 8/17 PRs have test.ran=true — actual is 7/17 (PR#11 has ran=False). Minor, doesn't affect conclusions.
- Jordan's ndm iter 1 "13 mismatches" was partially wrong (6 verified). VCP iter 1 claims are accurate.
