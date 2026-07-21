# Known Issues

## Verdict header contradicts ground truth on CVE-floor BLOCKED PRs (VERDICT_HEADER_MISMATCH)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 5 (ndm×3 + VCP×2)
- Status: **FIXED** (VCP iter 1) — _enforce_verdict_floor() enhanced to rewrite body text + all 17 comments regenerated.
- Impact: Was: 4/6 BLOCKED PRs showed "REVIEW RISK" headline. Now: all show "🚫 BLOCKED".

## PR comment is broken artifact — leaked LLM narration (BROKEN_COMMENT_ARTIFACT)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — _sanitize_comment() strips QA notes, fabricated URLs. _strip_agent_narration strips leaked narration. Comment regenerated via _fallback_comment().

## CVE-floor reason drops high-confidence probe evidence (CVE_FLOOR_REASON_DROP)
- First seen: v15 ndm-fresh iter 6 (as PR43_VERDICT_REASON)
- Occurrence count: 2
- Status: **FIXED** (VCP iter 1) — same_behavior=False branch already existed in code (ndm iter 6). VCP data already correct. STALE_COMMENTS pattern — regeneration fixed.

## PR#8 behavioral probe hides PACKAGE-MISMATCH caveat (PROBE_MISMATCH_HIDDEN)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — _fallback_comment now checks reconciliation_note for MISMATCH. Shows LOW confidence with "⚠️ package mismatch" caveat.

## merge_risk.tag not escalated on CVE floor (MERGE_RISK_CVE_FLOOR)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — CLI post-processing already escalated to High. STALE_COMMENTS — regeneration fixed. All 6 BLOCKED PRs show "🔴 High".

## Actions PRs cite Node.js artifacts in Go-only repo (ACTIONS_WRONG_ECOSYSTEM)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — Added ecosystem context to AI prompt. _fallback_comment uses ecosystem-aware verification (git diff, not npm). 0 Node.js references in PRs 4,19,20,21,22.

## govulncheck recommended despite permanent ban (GOVULNCHECK_POLICY_LEAK)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — Added DENY LIST to AI prompt. _strip_govulncheck post-gen strip existed. 0 govulncheck refs in PRs 10,53,54.

## Template fallback on highest-priority security PR (TEMPLATE_FALLBACK_CRITICAL)
- First seen: v15 VCP iter 1
- Occurrence count: 1
- Status: **FIXED** (VCP iter 1) — Added CVE-floor security urgency banner. Fallback now shows per-CVE data from cve_details. PR#32 has 91-line enriched comment with 3 CVEs and CVSS scores.

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
- Impact: PR#43 reason was "change evidence is limited; default caution" despite having same_behavior=true probe data.
- Fix applied: merge_risk fallback now includes behavioral probe evidence in reason string.
- Note: Verdict stays REVIEW (not SAFE) because corpus ground truth expects true_review for build=pre_existing PRs.

## Merge-plan headline severity summary fabricated (MERGE_PLAN_SEVERITY_FABRICATION)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6) — removed L[0-5] regex gate from both headline_severity() and committed_v2_verdict()

## Merge plan security fix in dead code (MERGE_PLAN_DEAD_CODE)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6) — ported security posture fallback to generate_ai_merge_plan.py with active/historical split

## Merge plan CVE table overclaims active security fixes (MERGE_PLAN_CVE_OVERCLAIM)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6) — added _is_currently_vulnerable() check, split into Active vs Historical Advisories

## Actions PRs get nonsensical npm install commands (ACTIONS_NPM_COMMANDS)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6) — added ecosystem=actions branch with git diff and release notes link

## Test row does not distinguish pre-existing from new failures (TEST_ROW_PRE_EXISTING)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6) — renders "⚠️ Failed — pre-existing" when new_failures=[] and test.exit!=0

## P0/critical CVE-floor PRs get REVIEW not BLOCKED (CVE_FLOOR_VERDICT_INCONSISTENCY)
- First seen: v15 ndm-fresh iter 6
- Occurrence count: 1
- Status: **FIXED** (iter 6) — _apply_cve_floor now escalates to BLOCKED when severity=critical AND priority=P0

## Changelog table row ignores changelogSignal.status (CHANGELOG_FABRICATION)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 1
- Status: **FIXED** (iter 5) — fallback renderer reads changelogSignal.status

## CVE remediation overclaim in comment body (CVE_OVERCLAIM)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 1
- Status: **FIXED** (iter 5) — _is_currently_vulnerable() handles non-PEP440 versions

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

## Dependabot Alerts Unavailable (ALERTS_BLIND)
- First seen: pre-v15
- Occurrence count: 10
- Status: **OPEN** — Root cause found: BREAKABILITY_PAT not passed to finalize merge step. Fix committed (93e2b00) but CI run 29805118237 predates fix by 5 minutes. UNVERIFIED.
- Impact: alerts_unavailable=true, total_open_alerts=0, severity_counts={}. Cannot correlate alerts with PRs.
- Note: 10th consecutive occurrence. Root cause finally identified as workflow YAML config, not PAT scope. Do NOT mark fixed until post-fix CI run shows non-empty prs_fixing_alerts.

## security_posture aggregation reports 0 CVEs (SEC_POSTURE_ZEROS)
- First seen: v15 ndm-fresh iter 1
- Occurrence count: 4
- Status: **FIXED** (iter 3)

## Merge plan security section functionally empty (MERGE_PLAN_SECURITY_EMPTY)
- First seen: v15 ndm-fresh iter 5
- Occurrence count: 2
- Status: **FIXED** (iter 6) — ported to generate_ai_merge_plan.py (see MERGE_PLAN_DEAD_CODE)

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
- Status: **FIXED** (d0eda1a) — **VERIFIED** in CI runs 29805118237 (VCP) and 29805125441 (NDM).
- Root cause: finalize job in breakability-reusable.yml never ran actions/setup-go@v5.
- Fix: added setup-go to finalize job + _find_go_binary() fallback.
- Results: NDM 4/6 Go PRs now have source=probe with high confidence. VCP 7/12 gomod probes succeed.
- Remaining: 2 NDM + 5 VCP Go PRs fall back on "go doc did not produce output" for umbrella modules (golang.org/x/net, golang.org/x/crypto) — secondary data-quality issue, NOT binary-not-found.

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
- Status: **FIXED** (iter 6) — --write post-processing escalates merge_risk.tag to Medium for Actions major bumps

## IMPORTANT CONTEXT
- Target repos: CSC-Security-sandbox/ndm-fresh-breakability (41 PRs, Node.js + Go monorepo) AND CSC-Security-sandbox/vcp-fresh-breakability (17 PRs, all Go)
- Do NOT suggest govulncheck — permanently removed.
- PR#68/#69 are genuine new failures (TestObservability) — positive controls. Do NOT change their verdicts.
- prs dict is canonical source in build-results.json; results array has known discrepancies.
- LESSON LEARNED (ITER 2-3): Fixing code logic without regenerating comments = invisible fix.
- LESSON LEARNED (ITER 5): Regenerating comments revealed renderer bugs hidden by staleness.
- LESSON LEARNED (ITER 6): Fixes must land in the file the workflow actually calls, not just a file that has the right name.
- LESSON LEARNED (ITER 1 current): Data-layer fixes to merge_risk, cross_pr_deps etc. are invisible if _fallback_comment() never reads them. Fix the renderer, not just the data.
- LESSON LEARNED: AI layer IS working (31/31 AI comments, 0 fallbacks in run 29802178785). Previous failures were from older runs. Cursor agent CLI generates rich breakability grading.
- Do NOT mark CI-dependent fixes (Go probe, alerts) as FIXED without a new CI run proving it.
- VCP was completely ignored until now — first CI run with all fixes is 29805118237.
- LESSON LEARNED (VCP ITER 1): ndm fixes don't auto-propagate to VCP comments. VCP comments must be regenerated with current codebase. Most VCP issues are STALE_COMMENTS pattern recurring on a new target.
