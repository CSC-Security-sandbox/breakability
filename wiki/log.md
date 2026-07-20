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
- Gate vs reality gap: Gate scored re-derived JSON (verdict_generation=7). All 41 comment files are unchanged (mtime 2026-07-17 14:28:25). Generator fixed code logic but never regenerated comments.
- Data-layer fixes CONFIRMED WORKING: hard_fix_floor guard, CVE version-range filter, PR#44 symbol override (top-level only)
- Data-layer fixes CONFIRMED NOT DEPLOYED: dedup pkg_dir compare, merge_risk.tag enforcement, verdict header enforcement — code may work but comments are unchanged
- NEW issues found: (1) STALE_COMMENTS — the overarching blocker, (2) CVE_COUNT_ZERO — "0 CVE(s)" in P0 reason with 26 real CVE IDs, (3) DUAL_MERGE_RISK — two contradictory merge_risk values for PR#44, (4) GO_PROBE_FABRICATED — Go "not found" in env where Go builds work
- REPEATED issues: ALERTS_BLIND (x5, "verified locally" falsified), SEC_POSTURE_ZEROS (x3), UNTESTED_SAFE (x3), OVERCLAIM (x4)
- Critical lesson: Fixing verdict logic without regenerating comments produces invisible fixes. The gate scored the wrong artifact. Comments are the deliverable, not JSON.
- 7 critical findings (C1-C7), 5 improvements (I1-I5)
- Next: generator must (1) fix remaining data bugs C2-C4/I5, (2) REGENERATE ALL COMMENTS, (3) verify comment-data consistency
- Handoff: persona → generator

## Iteration 3 — FAIL (1.0/10)
- Deterministic gate: 9.0/10, ACCEPTED (overridden by evaluation to FAIL)
- Sub-agent reviews: Sam 1/10, Jordan 2/10, Riley 4/10 (adjusted→3), Alex 1/10
- Consolidated: 1.0/10, FAIL (threshold 8.5)
- Reviewer errors: None. All 4 reviewers' claims verified.
- Score declined from 2.0→1.0 (end-user 2→1, accuracy 2→1, pipeline 4→3, security 3→2)
- ROOT CAUSE: STALE_COMMENTS — 3rd consecutive iteration. Generator committed 6 more code fixes this iteration (12 total across 3 iterations). ZERO deployed to comments. All 41 comment files still have mtime 2026-07-17 14:28:25.
- Data-layer fixes CONFIRMED WORKING this iteration: CVE count fallback ("26 CVE(s)" not "0"), DUAL_MERGE_RISK (both fields=Low for PR#44), UNTESTED_SAFE annotation (9 PRs annotated)
- Data-layer fixes CONFIRMED STILL NOT DEPLOYED: all 12 fixes across 3 iterations — comments unchanged
- NEW finding: PR#39 reachability overclaim (Alex) — second instance of module-scope bug. Prior conclusion "not systemic" corrected to "confirmed in 2/41." This is a CODE BUG, not just staleness.
- ESCALATED: REACHABILITY_OVERCLAIM from P2 to P1 (second confirmed instance, code bug)
- REPEATED issues: STALE_COMMENTS (x3), FALSE_BLOCK (x3), WRONG_DEDUP (x3), VERDICT_MISMATCH (x3), PHANTOM_REVIEW (x3), ALERTS_BLIND (x6), SEC_POSTURE_ZEROS (x4), OVERCLAIM (x4), GO_PROBE_FABRICATED (x2)
- CI-dependent fixes (Go probe, SEC_POSTURE_ZEROS) reclassified from "FIXED" to COMMITTED_UNVERIFIED — never re-run
- 9 critical findings (C1-C9), 6 improvements (I1-I6)
- **ACTION FOR ITERATION 4: (1) REGENERATE ALL 41 COMMENTS — this is the ONLY priority. (2) Fix reachability module-scope code bug. (3) Do NOT add more data-layer fixes. (4) Do NOT mark CI-dependent fixes as FIXED.**
- Handoff: persona → generator
