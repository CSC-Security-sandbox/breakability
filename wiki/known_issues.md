# Known Issues

## Verdict header contradicts ground truth on CVE-floor BLOCKED PRs (VERDICT_HEADER_MISMATCH)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 6 (ndm×3 + VCP×3)
- Status: **OPEN — REGRESSED** (was marked FIXED in VCP iter 1, but fix only worked on fallback-regenerated comments; fresh AI-generated comments from CI run 29813885743 still show wrong headline)
- Impact: 4/6 BLOCKED PRs (PR#23, #52, #53, #54) render "🚨 REVIEW RISK" headline while verdict_v2.verdict=BLOCKED. Fallback comments (PR#9, #32) are correct. Pattern: every fallback correct, every AI-generated wrong.
- Root cause: _enforce_verdict_floor() either does not run on AI-generated comments, or the regex doesn't match the AI's output format.
- LESSON: Validating fixes against fallback-regenerated comments does NOT prove the fix works on AI-generated comments. Must test against actual AI output.

## AI fabricates governance override language on BLOCKED PRs (AI_FABRICATED_GOVERNANCE)
- First seen: v15 VCP iter 1 (PR#54 "MERGE IMMEDIATELY")
- Occurrence count: 2
- Status: **OPEN** — PR#23 "SECURITY OVERRIDE (Rule 0.5)", PR#54 "security_override = MERGE_REQUIRED", PR#52 "Merge immediately" + "MERGE IMMEDIATELY". None of these strings exist in build-results.json.
- Impact: Gate manufactures its own override authority to bypass BLOCKED verdict. Developer following instructions merges unverified (L0) PRs on CVSS-10.0 CVEs.
- Previous fix: VCP iter 1 added "MERGE IMMEDIATELY" → "Do not merge" rewrite. Applied to fallback comments only, not AI-generated.

## AI fabricates specific root cause with no evidence (AI_FABRICATED_ROOT_CAUSE)
- First seen: v15 VCP iter 2
- Occurrence count: 1
- Status: **OPEN** — PR#23 (7x "Go toolchain unavailable"), PR#54 (8x "Go toolchain not accessible"/"missing PATH"). build.output_tail is empty. main_build.go shows disk exhaustion ("no space left on device"). Same run correctly diagnoses disk-space on PR#10/#11.
- Impact: Sends developer down wrong debugging path.

## AI fabricates file paths, line numbers, and commit SHAs (AI_FABRICATED_CITATIONS)
- First seen: v15 VCP iter 2
- Occurrence count: 1
- Status: **OPEN** — PR#54 invents `utils/crypto/encryption.go` (not in files_importing). PR#22 invents `ci.yml`, `release.yml`, `dependabot.yml` (files_importing=[]). PR#4 fabricates commit SHA `fa0a91b85d4f404`.
- Impact: Developer could trust fabricated file paths for blast-radius assessment or copy fabricated SHA into workflow.

## AI invents non-canonical verdict category (AI_VERDICT_INVENTION)
- First seen: v15 VCP iter 2
- Occurrence count: 1
- Status: **OPEN** — PR#23 produces "SECURITY_RISK" / "SECURITY RISK" verdict. Schema only has SAFE/REVIEW/BLOCKED. Three different verdict strings in one comment.

## PR#52 merge_risk.tag renders invalid enum value (MERGE_RISK_ENUM_VIOLATION)
- First seen: v15 VCP iter 2
- Occurrence count: 1
- Status: **OPEN** — PR#52 comment shows "Merge Risk: BLOCKED". Ground truth tag="High". Enum is Low/Medium/High/None.

## Actions PRs cite Node.js artifacts in Go-only repo (ACTIONS_WRONG_ECOSYSTEM)
- First seen: v15 VCP iter 1
- Occurrence count: 2
- Status: **OPEN — REGRESSED** (was marked FIXED, but fresh AI-generated comments reintroduce Node.js/TypeScript references; PR#20 line 91 has actionable "Verify that Node.js 20 is installed" instruction in a Go-only repo)
- Previous fix: VCP iter 1 added ecosystem context to AI prompt. Fix didn't survive fresh AI generation.

## merge_risk.reason ignores reachability/probe evidence (MERGE_RISK_REASON_BLIND)
- First seen: v15 VCP iter 1 (as CVE_FLOOR_REASON_DROP)
- Occurrence count: 2
- Status: **OPEN** — 12/17 PRs have reason = "missing changelog; default caution" despite usages=[], api_diff available.
- Note: verdict_v2.reason includes probe evidence for some PRs but merge_risk.reason doesn't.

## PR comment is broken artifact — leaked LLM narration (BROKEN_COMMENT_ARTIFACT)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — PR#8 now clean fallback comment. VERIFIED in iter 2.

## CVE-floor reason drops high-confidence probe evidence (CVE_FLOOR_REASON_DROP)
- First seen: v15 ndm-fresh iter 6 (as PR43_VERDICT_REASON)
- Occurrence count: 2
- Status: **FIXED in verdict_v2** (VCP iter 1). merge_risk.reason still affected — tracked as MERGE_RISK_REASON_BLIND.

## PR#8 behavioral probe hides PACKAGE-MISMATCH caveat (PROBE_MISMATCH_HIDDEN)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — fallback comment shows LOW confidence with "⚠️ package mismatch" caveat. VERIFIED in iter 2.

## merge_risk.tag not escalated on CVE floor (MERGE_RISK_CVE_FLOOR)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — All 6 BLOCKED PRs show merge_risk.tag="High" in data layer. VERIFIED in iter 2.

## govulncheck recommended despite permanent ban (GOVULNCHECK_POLICY_LEAK)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — 0 govulncheck references in all 17 comments. VERIFIED in iter 2.

## Template fallback on highest-priority security PR (TEMPLATE_FALLBACK_CRITICAL)
- First seen: v15 VCP iter 1
- Occurrence count: 2
- Status: **OPEN** — PR#9 and PR#32 (both pgx/v5, CVSS 9.8, P0/critical) fell back to template. Fallback content is correct (BLOCKED headline, CVE data) but less detailed. 2/3 fallbacks are P0 PRs.

## Dependabot Alerts Unavailable (ALERTS_BLIND)
- First seen: pre-v15
- Occurrence count: 10
- Status: **FIXED — VERIFIED** in CI run 29813885743. alerts_unavailable=false, total_open_alerts=156, severity_counts={medium:43, critical:64, high:45, low:4}, prs_fixing_alerts populated for 12/17 PRs with real CVE IDs, CVSS scores, advisory URLs. Root cause fix: 93e2b00 (BREAKABILITY_PAT passed to finalize merge step).
- Resolution: 10-iteration saga resolved. Do NOT re-open unless a future run regresses.

## API Diff row fabricates "No changes" when tool never ran (API_DIFF_FABRICATION)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 2
- Status: **FIXED** (iter 7) — checks api_diff_tool is None before converting to dict

## BLOCKED verdicts cite zero actual error text (BLOCKED_NO_EVIDENCE)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — BLOCKED comments now render build.new_errors, test.new_failures, or output_tail excerpt

## Confidence column hardcoded MEDIUM (CONFIDENCE_HARDCODED)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — probe reads bg.confidence, unavailable signals show "—" not MEDIUM

## SAFE headline with no pre-existing explanation (SAFE_NO_EXPLANATION)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — bridging note inserted between SAFE headline and pre-existing rows

## merge_risk data invisible in rendered comments (MERGE_RISK_INVISIBLE)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — _fallback_comment renders "### Merge Risk" section with tag, emoji, and reason

## Cross-PR deps absent from per-PR comments (CROSS_PR_INVISIBLE)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — _fallback_comment renders "### ⚠️ Coordinated Upgrades" with related PRs and merge order

## Footer Analyzed date fabricates freshness (DATE_FABRICATION)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — footer uses metadata.timestamp (date part) instead of date.today()

## Merge plan _pr_row() CVE column always empty (MERGE_PLAN_CVE_COLUMN)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 1
- Status: **FIXED** (iter 7) — _pr_row falls back to deterministic.security.cveIds with severity-colored emoji

## PR#43 verdict reason inaccurate (PR43_VERDICT_REASON)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **PARTIALLY FIXED** (iter 6) — reason now includes probe evidence; verdict stays REVIEW per corpus

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
- Status: **FIXED** (iter 6) — _apply_cve_floor now escalates to BLOCKED when severity=critical AND priority=P0

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
- Status: **FIXED** (d0eda1a) — **VERIFIED** in CI runs 29805118237 and 29813885743.
- Results: 7/12 gomod probes succeed with source=probe, confidence=high. 5 umbrella modules fall back ("go doc did not produce output") — secondary data-quality issue.

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
- LESSON LEARNED (VCP ITER 2): **Fixes validated against fallback-regenerated comments do NOT prove they work on AI-generated comments.** The AI path produces different output formats, invents non-canonical terms, and bypasses post-processing safeguards. All post-processing must be tested against actual AI output, not just fallback templates.
- LESSON LEARNED: AI layer IS working (31/31 AI comments, 0 fallbacks in run 29802178785). Previous failures were from older runs.
- Do NOT mark CI-dependent fixes as FIXED without a new CI run proving it.
