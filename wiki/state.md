# Loop State

## Current: ITERATION 3 (VCP Loop) — FAIL (2.0/10), PENDING GENERATOR
- Target repo: CSC-Security-sandbox/vcp-fresh-breakability (17 PRs, Go monorepo)
- CI run: 29912265391 (completed), build data from 29823501769 (timestamp 2026-07-21T11:16:48Z), verdict_generation=6
- Deterministic gate: 0.0/10, ACCEPTED (25 false_none from stale corpus + 2 pipeline findings)
- Evaluator score: 2.0/10, FAIL (threshold 8.5)
- Score floor: Accuracy (2/10) — NEW regressions from fix attempts + 3 unfixed P0 fabrications
- **SCORE DROPPED from 4.0→2.0** — fix attempt introduced more problems than it solved
- 11 critical findings (C1-C11), 4 improvements (I1-I4)
- Next persona: generator
- Updated: 2026-07-22

## Score Breakdown
- End-User (Sam): 5/10 — Up from 4 (3 fixes verified: PR#7 disk label, PR#22 npm, PR#9 API overclaim). Still has merge-encouraging on 4/6 BLOCKED + list regression.
- Security (Jordan): 3/10 — Down from 5 (merge-encouraging persists on highest-CVE PRs). One reviewer error (PR#54). Data layer solid.
- Pipeline (Riley): 4/10 — Down from 6 (PR#41 holdout, 0/17 L3+). 5/6 disk diagnosis fixed. PR#8 confidence fixed.
- Accuracy (Alex): 2/10 — Down from 4. NEW regression: line-number sanitizer corrupts real build output (5 PRs). List renumbering regressed from correct→broken (13/15 PRs). 3 prior P0 fabrications unchanged (Dial, cascade, changelog HIGH).

## KEY REGRESSION: Fix-attempt collateral damage
The eval iter-2 generator fixes introduced TWO new regressions:
1. **Line-number sanitizer** (C3 fix) now strips REAL line numbers from verbatim build output in code blocks — strictly worse than the fabrication it was meant to fix
2. **List renumbering** (C2 fix) now renders every item as "1." on loose lists — demonstrably regressed from correct 1,2,3 output in prior generation

This is the core pattern: unit tests pass on synthetic fixtures but fail on real AI-generated output with loose lists, code blocks, and complex formatting.

## REAL PROGRESS (credit where due)
- **VERDICT_HEADER_MISMATCH: FIXED AND HOLDING** — All 17 verdict headers match contract.
- **ALERTS_BLIND: FIXED AND HOLDING** — alerts_unavailable=false, 156 alerts, per-PR CVE data populated.
- **govulncheck purged** — 0 references across all 17 comments.
- **CVE data quality** — IDs, CVSS scores, advisory URLs all correct.
- **Reachability file paths** — files_importing paths accurate on all checked PRs.
- **Pre-existing failures correctly attributed** — go.work version skew diagnosed as infrastructure.
- **PR#7 disk-space diagnosis** — FIXED (was "Go unavailable"). Verified.
- **PR#22 npm references** — FIXED (was npm ci/npm test in YAML). Verified.
- **PR#9 API changes overclaim** — FIXED ("48 API changes (8 breaking, 40 additions)"). Verified.
- **PR#8 confidence** — FIXED (LOW for never-ran build). Verified.
- **5/6 disk space diagnosis** — FIXED (PR#7,23,42,52 correct; PR#41 holdout).

## Top Blockers for Generator
1. **Line-number sanitizer corrupts real build output** — NEW REGRESSION. 5 PRs. (P0, C1)
2. **Merge-encouraging language on BLOCKED PRs** — 5th occurrence. 4/6 BLOCKED PRs. (P0, C2)
3. **List renumbering regression** — 3rd occurrence. 13/15 AI comments. (P0, C3)
4. **Fabricated Dial citation in PR#54** — 2nd occurrence. Unchanged. (P0, C4)
5. **Fabricated cascade claim in PR#54** — 2nd occurrence. Unchanged. (P0, C5)
6. **PR#22 changelog HIGH confidence** — 2nd occurrence. Missed format variant. (P1, C6)
7. **PR#53 garbled sentence** — NEW. Collateral from C2 fix. (P1, C7)
8. **PR#52 dual Merge Risk reasons** — Both fabricated. (P1, C8)
9. **PR#23 duplicated reason text** — NEW. (P1, C9)
10. **PR#41 wrong infra diagnosis** — Partial fix holdout. (P1, C10)
11. **PR#4 SHA-pinning factually wrong** — NEW. (P2, C11)

## Confirmed Working
- Verdict header accuracy: 17/17 (all match contract)
- ALERTS_BLIND fixed — stable across 3+ runs
- govulncheck purged — 0 references
- CVE data: IDs, CVSS, advisory URLs all correct
- merge_risk.tag escalation on CVE floor — all 6 BLOCKED show High
- Reachability file paths accurate
- Pre-existing failure attribution correct
- Template-fallback labeling accurate (2/17 correctly labeled)
- AI layer running (15/17 AI-generated, 2 fallback)
- oom_override=false for all 17
- PR#7 disk-space diagnosis — VERIFIED
- PR#22 npm removed — VERIFIED
- PR#9 API changes correct — VERIFIED

## Reviewer Error Log
- Iteration 1 (ndm): Riley claimed 8/17 test.ran=true (actual 7/17, minor)
- Iteration 2 (ndm): No errors
- Iteration 3 (VCP): Riley said ACTIONS_WRONG_ECOSYSTEM doesn't reproduce — WRONG
- Iteration 1 (VCP): Sam C4 fabricated temp filename — REVIEWER ERROR. Alex O3 scope — REVIEWER ERROR.
- Iteration 2 (VCP): No reviewer errors.
- **Iteration 3 (VCP Loop): Jordan REVIEWER ERROR on PR#54 — claimed "Strongly recommended" text, grep shows zero hits. Jordan also quoted pre-fix text for PR#53. Jordan described list bug as missing items; actual pattern is all-items-as-1. Core findings valid.**
