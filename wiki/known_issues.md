# Known Issues

## Verdict header contradicts ground truth on CVE-floor BLOCKED PRs (VERDICT_HEADER_MISMATCH)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 7 (ndm×3 + VCP×4)
- Status: **FIXED** (VCP iter 3) — Header regex reordered for multi-word patterns, _enforce_verdict_floor reworked with body cleanup in both branches. All 6 BLOCKED PRs now show correct ## 🚫 BLOCKED header. All 17 verdicts match contract.
- LESSON: All three VCP iterations failed for the same meta-reason: local testing ≠ deployment. Iter 3 fixed by applying post-processing to CI artifacts locally.

## AI fabricates governance override / merge-encouraging language on BLOCKED PRs (AI_FABRICATED_GOVERNANCE)
- First seen: v15 VCP iter 1 (PR#54 "MERGE IMMEDIATELY")
- Occurrence count: 3
- Status: **FIXED** (VCP iter 3) — Structural _strip_merge_encouraging() strips any line with merge + positive-action word OR "merge this PR" pattern. Full-line matching (no [^.\n]* restriction). Called on all BLOCKED verdicts. All 6 BLOCKED PRs verified clean.
- LESSON: Phrase-based deny-lists cannot contain an LLM. Structural rules keyed on verdict==BLOCKED are the correct approach.

## AI fabricates specific root cause with no evidence (AI_FABRICATED_ROOT_CAUSE)
- First seen: v15 VCP iter 2
- Occurrence count: 2
- Status: **FIXED** (VCP iter 3) — _guard_empty_build_output() enhanced with code block scanning using _BUILD_OUTPUT_MARKERS regex. Strips code blocks with fabricated $WORK/b\d+, _pkg_.a markers when output_tail is empty. PR#23 fabricated blocks removed.

## AI invents non-canonical verdict category (AI_VERDICT_INVENTION)
- First seen: v15 VCP iter 2
- Occurrence count: 2
- Status: **FIXED** (VCP iter 3) — _rewrite_noncanonical_arbiter() scans AI Arbiter | <TOKEN> patterns and rewrites ANY non-canonical token to contract verdict. Generalizes to future inventions without needing map extension. PR#53 "MERGE RECOMMENDED" → "BLOCKED".

## AI fabricates file paths, line numbers, and commit SHAs (AI_FABRICATED_CITATIONS)
- First seen: v15 VCP iter 2
- Occurrence count: 1
- Status: **PARTIALLY FIXED** — Fabricated commit SHA (PR#4) and workflow filenames (PR#22 ci.yml/release.yml) no longer reproduce in iter 3. PR#54 fabricated file path status unknown.
- Impact: Reduced from iter 2. SHA and filename fabrication addressed.

## PR#52 merge_risk.tag renders invalid enum value (MERGE_RISK_ENUM_VIOLATION)
- First seen: v15 VCP iter 2
- Occurrence count: 1
- Status: **FIXED** (VCP iter 2) — PR#52 now shows merge_risk.tag="High" correctly. VERIFIED in iter 3.

## Actions PRs cite Node.js artifacts in Go-only repo (ACTIONS_WRONG_ECOSYSTEM)
- First seen: v15 VCP iter 1
- Occurrence count: 3
- Status: **FIXED** (VCP iter 3) — _strip_wrong_ecosystem_refs() enhanced with _fix_code_block() that scans YAML/shell code blocks and replaces npm ci→go build, npm test→go test, npm install→go build, yarn→go test. PR#22 npm code blocks now show Go commands.

## merge_risk.reason ignores reachability/probe evidence (MERGE_RISK_REASON_BLIND)
- First seen: v15 VCP iter 1 (as CVE_FLOOR_REASON_DROP)
- Occurrence count: 3
- Status: **OPEN** — 6+ PRs (7,8,10,11,41,42) have reason="missing changelog; default caution" despite bg.confidence=high, files_importing data. VCP iter 2 C8 "enriched 29 objects" — doesn't hold for non-CVE-floor PRs.

## AI fabricates numbered rule citations (AI_FABRICATED_RULES)
- First seen: v15 VCP iter 2 (as "Rule 0.5")
- Occurrence count: 2
- Status: **FIXED** (VCP iter 3) — _sanitize_comment() strips entire lines containing Rule N citations via regex. 0 Rule N citations remain across all 17 PRs.

## merge_risk data invisible in AI-generated comments (MERGE_RISK_INVISIBLE_AI)
- First seen: v15 VCP iter 3
- Occurrence count: 1
- Status: **FIXED** (VCP iter 3) — _inject_merge_risk() checks if merge_risk.tag exists in data but 'Merge Risk' not in comment, injects formatted section. PR#9 and PR#53 now have Merge Risk sections. No duplicates on PRs that already had them.

## PR#7 SAFE/REVIEW self-contradiction (PR7_VERDICT_CONTRADICTION)
- First seen: v15 VCP iter 3
- Occurrence count: 1
- Status: **FIXED** (VCP iter 3) — _fix_body_verdict_contradictions() rewrites SAFE→REVIEW in body text (AI Arbiter, "SAFE to merge", "Why SAFE", "keep SAFE"). Called in both branches of _enforce_verdict_floor(). PR#7 body has 0 SAFE contradictions.

## PR comment is broken artifact — leaked LLM narration (BROKEN_COMMENT_ARTIFACT)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — PR#8 now clean fallback comment. VERIFIED in iters 2 and 3.

## CVE-floor reason drops high-confidence probe evidence (CVE_FLOOR_REASON_DROP)
- First seen: v15 ndm-fresh iter 6 (as PR43_VERDICT_REASON)
- Occurrence count: 2
- Status: **FIXED in verdict_v2** (VCP iter 1). merge_risk.reason still affected — tracked as MERGE_RISK_REASON_BLIND.

## PR#8 behavioral probe hides PACKAGE-MISMATCH caveat (PROBE_MISMATCH_HIDDEN)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — VERIFIED in iters 2 and 3.

## merge_risk.tag not escalated on CVE floor (MERGE_RISK_CVE_FLOOR)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — All 6 BLOCKED PRs show merge_risk.tag="High" in data layer. VERIFIED in iters 2 and 3.

## govulncheck recommended despite permanent ban (GOVULNCHECK_POLICY_LEAK)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — 0 govulncheck references. VERIFIED in iters 2 and 3.

## Template fallback on highest-priority security PR (TEMPLATE_FALLBACK_CRITICAL)
- First seen: v15 VCP iter 1
- Occurrence count: 2
- Status: **PARTIALLY RESOLVED** — Fallback count dropped from 3→2. PR#9 no longer falls back (but now produces wrong AI output). PR#32 still fallback. PR#8 still fallback.

## Dependabot Alerts Unavailable (ALERTS_BLIND)
- First seen: pre-v15
- Occurrence count: 10
- Status: **FIXED — VERIFIED** across 2+ CI runs. alerts_unavailable=false, total_open_alerts=156. Do NOT re-open unless future run regresses.

## API Diff row fabricates "No changes" when tool never ran (API_DIFF_FABRICATION)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 2
- Status: **FIXED** (iter 7)

## BLOCKED verdicts cite zero actual error text (BLOCKED_NO_EVIDENCE)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7)

## Confidence column hardcoded MEDIUM (CONFIDENCE_HARDCODED)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7)

## SAFE headline with no pre-existing explanation (SAFE_NO_EXPLANATION)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7)

## merge_risk data invisible in rendered comments — fallback path (MERGE_RISK_INVISIBLE)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — _fallback_comment renders "### Merge Risk" section. AI path gap tracked separately as MERGE_RISK_INVISIBLE_AI.

## Cross-PR deps absent from per-PR comments (CROSS_PR_INVISIBLE)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7)

## Footer Analyzed date fabricates freshness (DATE_FABRICATION)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7)

## Merge plan _pr_row() CVE column always empty (MERGE_PLAN_CVE_COLUMN)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7)

## PR#43 verdict reason inaccurate (PR43_VERDICT_REASON)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **PARTIALLY FIXED** (iter 6)

## Merge-plan headline severity summary fabricated (MERGE_PLAN_SEVERITY_FABRICATION)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6)

## Merge plan security fix in dead code (MERGE_PLAN_DEAD_CODE)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6)

## Merge plan CVE table overclaims active security fixes (MERGE_PLAN_CVE_OVERCLAIM)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6)

## Actions PRs get nonsensical npm install commands (ACTIONS_NPM_COMMANDS)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6)

## Test row does not distinguish pre-existing from new failures (TEST_ROW_PRE_EXISTING)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6)

## P0/critical CVE-floor PRs get REVIEW not BLOCKED (CVE_FLOOR_VERDICT_INCONSISTENCY)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6)

## Changelog table row ignores changelogSignal.status (CHANGELOG_FABRICATION)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 1
- Status: **FIXED** (iter 5)

## CVE remediation overclaim in comment body (CVE_OVERCLAIM)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 1
- Status: **FIXED** (iter 5)

## Broken merge-plan link placeholder (ISSUE_NUMBER_PLACEHOLDER)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 1
- Status: **FIXED** (iter 5)

## Comments never regenerated after code fixes (STALE_COMMENTS)
- First seen: v15 ndm-fresh iter 2
- Occurrence count: 3
- Status: **FIXED** (iter 3)

## hard_fix_floor ignores build.verdict="pre_existing" (FALSE_BLOCK)
- First seen: v15 ndm-fresh iter 0/1
- Occurrence count: 3
- Status: **FIXED** (iter 1 data, iter 3 comments)

## CVE wiring gap: deterministic.security → cve_details (CVE_BLIND)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 2
- Status: **PARTIALLY FIXED** — Verdict-level applicability works. Comment-level fixed iter 5.

## CVE count reads wrong field in P0 verdict reason (CVE_COUNT_ZERO)
- First seen: v15 ndm-fresh iter 2
- Occurrence count: 1
- Status: **FIXED** (iter 2)

## PR#44 Overclaim / Dual merge_risk (OVERCLAIM / DUAL_MERGE_RISK)
- First seen: v15 ndm-fresh iter 0
- Occurrence count: 4
- Status: **FIXED** (iter 2 data, iter 3 comments)

## Reachability blast-radius overclaim (REACHABILITY_OVERCLAIM)
- First seen: v15 ndm-fresh iter 0
- Occurrence count: 4
- Status: **FIXED** (iter 3 comments)

## security_posture aggregation reports 0 CVEs (SEC_POSTURE_ZEROS)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 4
- Status: **FIXED** (iter 3)

## Merge plan security section functionally empty (MERGE_PLAN_SECURITY_EMPTY)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 2
- Status: **FIXED** (iter 6)

## Cross-PR duplicate misadvice (WRONG_DEDUP)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 3
- Status: **FIXED** (iter 1 code, iter 3 comments)

## PR#105 comment verdict contradicts verdict_v2 (VERDICT_MISMATCH)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 3
- Status: **FIXED** (iter 1 code, iter 3 comments)

## PR36/PR45 phantom REVIEW (PHANTOM_REVIEW)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 3
- Status: **FIXED** (iter 1 code, iter 3 comments)

## Go behavioral probe fabricates "go not found" (GO_PROBE_FABRICATED)
- First seen: v15 ndm-fresh iter 2
- Occurrence count: 5
- Status: **FIXED** (d0eda1a) — **VERIFIED** across multiple CI runs.

## SAFE verdict without test evidence (UNTESTED_SAFE)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 3
- Status: **FIXED** (iter 2 data, iter 3 comments)

## PR#29 verdict_v2 data bug — SAFE with same_behavior=False (PR29_VERDICT_BUG)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 1
- Status: **FIXED** (iter 5)

## verification_level contradicts build.verdict for 5 Actions PRs (ACTIONS_VERLEVEL_BUG)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 4
- Status: **FIXED** (iter 5)

## Pipeline provenance mismatch + deceptive footer (PIPELINE_PROVENANCE)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 1
- Status: **FIXED** (iter 5)

## PR#9 three-way merge_risk contradiction (PR9_MERGE_RISK)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 4
- Status: **FIXED** (iter 6)

## IMPORTANT CONTEXT
- Target repos: CSC-Security-sandbox/ndm-fresh-breakability (41 PRs, Node.js + Go monorepo) AND CSC-Security-sandbox/vcp-fresh-breakability (17 PRs, all Go)
- Do NOT suggest govulncheck — permanently removed.
- PR#68/#69 are genuine new failures (TestObservability) — positive controls. Do NOT change their verdicts.
- prs dict is canonical source in build-results.json; results array has known discrepancies.
- LESSON LEARNED (ITER 2-3): Fixing code logic without regenerating comments = invisible fix.
- LESSON LEARNED (ITER 5): Regenerating comments revealed renderer bugs hidden by staleness.
- LESSON LEARNED (ITER 6): Fixes must land in the file the workflow actually calls, not just a file that has the right name.
- LESSON LEARNED (VCP ITER 1): ndm fixes don't auto-propagate to VCP comments. VCP comments must be regenerated with current codebase.
- LESSON LEARNED (VCP ITER 2): **Fixes validated against fallback-regenerated comments do NOT prove they work on AI-generated comments.** The AI path produces different output formats, invents non-canonical terms, and bypasses post-processing safeguards.
- LESSON LEARNED (VCP ITER 3): **Fixes that are not pushed to origin are not deployed.** Three VCP iterations failed because commit 8cc96d7 was never pushed. Local test validation is necessary but not sufficient. Must verify against actual CI-produced artifacts.
- LESSON LEARNED: **Phrase-based deny-lists cannot contain an LLM that paraphrases freely.** Use structural rules keyed on verdict_v2.verdict, not pattern-matching specific strings.
- LESSON LEARNED: AI layer IS working (31/31 AI comments, 0 fallbacks in run 29802178785). Previous failures were from older runs.
- Do NOT mark CI-dependent fixes as FIXED without a new CI run proving it.
