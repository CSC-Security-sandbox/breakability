<!-- breakability-merge-plan -->
# 📋 Breakability Merge Plan

**Generated:** 2026-06-08 10:18 UTC (deterministic)
**PRs analyzed:** 33 Dependabot PRs

> ⏱️ **Snapshot** generated at `2026-06-08 10:18 UTC`. PR states may have changed since analysis.
> To refresh: `gh workflow run breakability-agent.yml`

## ⚡ What to Do Next

> **TLDR:** Jump to [Developer Action Summary](#developer-action-summary) for numbered merge steps. Or:

- 🛑 **Fix first:** 9 PR(s) have blocking verification issues — see 'Fix Required' below.
- 🔐 **Priority merge:** 5 PR(s) fix known CVEs — merge them first.
- 🔴 **Review required:** 10 PR(s) need careful review before merge.
- 📋 **Follow the numbered plan:** 16 PR(s) need review/glance handling — see exact actions below.

<details><summary><strong>📊 Technical Details & Risk Classification</strong> (L-levels, severity, counts)</summary>

## Summary by Verification Level

| Category | Count |
|----------|-------|
| ✅ Safe to merge — tests pass (L4) | 0 |
| ✅ Build passes — review recommended (L2/L3) | 0 |
| 🔗 Blocked (safe but companion PR needs fix) | 1 |
| 🟡 CI major action bump — changelog glance | 2 |
| 🔐 CI supply-chain (auth/token/registry/deploy) — security review | 3 |
| ❌ Fix required | 9 |
| 🔴 Review required (High) | 1 |
| 🟠 Review recommended (Medium) | 11 |
| 🟡 Optional glance (Low) | 6 |

## Breakability Summary

🔴 **High:** 10 · 🟠 **Medium:** 16 · 🟡 **Low:** 6 · 🟢 **None:** 1

> High/Medium = worth a review · Low = optional glance · None = safe to merge. Severity matches each PR's breakability headline (security-fix PRs show a merge-priority headline instead).

</details>

## Developer Action Summary

**Plain-English merge guidance — see Technical Details above for verification levels.**

1. **FIX FIRST — security PRs with blocking issues:** #10, #11, #19, #28, #32 — resolve the listed blocker before merging
2. **GLANCE then MERGE — low breakability:** #1, #4, #14, #15, #24, #31 — optional changelog/API skim, not deep review
3. **WAIT — paired PRs blocked:** #5 — merge these only after fixing their companion PR
4. **GLANCE then MERGE — major CI actions:** #3, #8 — review for breaking input changes
5. **REVIEW — supply-chain sensitive CI:** #2, #6, #9 — pin to commit SHA, verify permissions
6. **FIX NEEDED:** 7 PR(s) have blocking verification issues

## 🔴 Security — CVEs Fixed by These Upgrades

> **ACTION REQUIRED:** Merge security fix PRs as soon as possible to resolve known vulnerabilities.

- **PR #10** `github.com/andygrunwald/go-jira` 1.16.0→1.17.0 — CVE-2025-30204 (claimed in PR body — not version-verified vs fixed-in) ❌ Fix required before merge
- **PR #11** `github.com/golang-migrate/migrate/v4` 4.18.2→4.19.1 — CVE-2025-47914, CVE-2025-58181 ❌ Fix required before merge
- **PR #19** `github.com/jackc/pgx/v5` 5.7.4→5.9.1 — CVE-2026-33816 ❌ Fix required before merge
- **PR #28** `github.com/googleapis/gax-go/v2` 2.14.2→2.20.0 — CVE-2026-33186, CVE-2026-29181, CVE-2026-24051, CVE-2025-47914, CVE-2025-58181 ❌ Fix required before merge
- **PR #32** `github.com/go-openapi/strfmt` 0.23.0→0.26.1 — CVE-2025-47914, CVE-2025-58181 ❌ Fix required before merge
- **PR #16** `github.com/go-chi/chi/v5` 5.2.1→5.2.5 — GHSA-vrw8-fxc6-2r93 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #20** `golang.org/x/crypto` 0.43.0→0.49.0 — CVE-2025-47914, CVE-2025-58181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #23** `go.opentelemetry.io/otel/sdk` 1.38.0→1.42.0 — CVE-2026-29181, CVE-2026-24051 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #25** `golang.org/x/net` 0.45.0→0.52.0 — CVE-2025-47914, CVE-2025-58181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #27** `go.opentelemetry.io/otel/trace` 1.38.0→1.42.0 — CVE-2026-29181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #36** `go.opentelemetry.io/otel/metric` 1.38.0→1.42.0 — CVE-2026-29181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)

## 🔗 Blocked — Safe but Companion PR Needs Fix First

These PRs pass build verification but are **blocked** because a companion PR (coordinated upgrade) currently has build failures or security issues.
Fix the companion PR first, then merge both together.

| PR | Package | Version | Bump | Merge Risk | Verification | Blocked By |
|----|---------|---------|------|------------|-------------|------------|
| #5 | `github.com/spf13/cobra` | 1.10.1→1.10.2 | patch | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | L2_type_checked ✅ | Fix #12 first |

## 🔗 Coordinated Upgrades (merge together)

- ⛔ **K8s module coordination: k8s.io/apimachinery + k8s.io/client-go must match versions:** #21 + #30 — **DO NOT MERGE as a group.** #21 build fails. Resolve #21 first (see sections below); merging the group now would pull in the blocking PR.
- **OpenTelemetry release-train coordination: go.opentelemetry.io/otel/sdk + go.opentelemetry.io/otel/metric share the otel core version — merge together to keep the resolved otel version consistent:** #23 + #27 + #36 — merge all 3 together
- ⛔ **Same package (github.com/spf13/cobra) in different modules: automations/tstctl + cicd:** #5 + #12 — **DO NOT MERGE as a group.** #12 blocked. Resolve #12 first (see sections below); merging the group now would pull in the blocking PR.
- **Same package (golang.org/x/oauth2) in different modules: cicd + /:** #4 + #15 + #22 — merge all 3 together

## ❌ Fix Required — Do Not Merge

| PR | Package | Version | Bump | Merge Risk | Issue |
|----|---------|---------|----|------------|-------|
| #10 | `github.com/andygrunwald/go-jira` | 1.16.0→1.17.0 | minor | High (Evidence: declared breaking change (changelog), behavior unverified × Build verification: L2 × Oracle confidence: not available) — declared breaking change unverified by build/test: ### Breaking Changes | New errors on top of pre-existing |
| #11 | `github.com/golang-migrate/migrate/v4` | 4.18.2→4.19.1 | minor | High (Evidence: break-reachable API change × Build verification: L3 × Oracle confidence: not available) — BREAK-reachable type changed API symbol `NewWithInstance` | New errors on top of pre-existing |
| #12 | `github.com/spf13/cobra` | 1.9.1→1.10.2 | minor | High (Evidence: declared breaking change (changelog), behavior unverified × Build verification: L2 × Oracle confidence: not available) — declared breaking change unverified by build/test: This version of `pflag` carried a breaking change: it renamed `ParseErrorsWhitelist` to `ParseErrorsAllowlist` which can break builds if both `pflag` and `cobra` are dependencies in your project. | New errors on top of pre-existing |
| #18 | `github.com/pb33f/libopenapi` | 0.21.12→0.34.4 | major ⚠️ (0.x unstable) | High (Evidence: break-reachable API change × Build verification: L2 × Oracle confidence: not available) — BREAK-reachable type changed API symbol `Document` | Build fails |
| #19 | `github.com/jackc/pgx/v5` | 5.7.4→5.9.1 | minor | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | New errors on top of pre-existing |
| #21 | `k8s.io/apimachinery` | 0.33.3→0.35.3 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | Build fails |
| #28 | `github.com/googleapis/gax-go/v2` | 2.14.2→2.20.0 | minor | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | New errors on top of pre-existing |
| #32 | `github.com/go-openapi/strfmt` | 0.23.0→0.26.1 | major ⚠️ (0.x unstable) | High (Evidence: release-note breaking surface × Build verification: L2 × Oracle confidence: not available) — release notes mention config/middleware/pipeline behavior changes | New errors on top of pre-existing |
| #38 | `github.com/lib/pq` | 1.11.2→1.12.3 | minor | High (Evidence: break-reachable API change × Build verification: L3 × Oracle confidence: not available) — BREAK-reachable type changed API symbol `Error.Code` | New errors on top of pre-existing |

## 🟡 Optional Glance — Low Breakability

These PR comments are already downgraded to **Low / optional glance** by the committed verdict. Skim the noted evidence, then merge if no project-specific concern appears.

- **PR #1** `github.com/sirupsen/logrus` 1.9.3→1.9.4 — **Low / optional glance**: build, tests, and API diff are clean; changelog is unavailable (`glance:clean-missing-release-notes`)
- **PR #4** `golang.org/x/oauth2` 0.32.0→0.36.0 — **Low / optional glance**: build, tests, and API diff are clean; changelog is unavailable (`glance:clean-missing-release-notes`)
- **PR #14** `cloud.google.com/go/monitoring` 1.24.2→1.24.3 — **Low / optional glance**: build, tests, and API diff are clean; changelog is unavailable (`glance:clean-missing-release-notes`)
- **PR #15** `golang.org/x/oauth2` 0.30.0→0.36.0 — **Low / optional glance**: build, tests, and API diff are clean; changelog is unavailable (`glance:clean-missing-release-notes`)
- **PR #24** `github.com/go-openapi/errors` 0.22.1→0.22.7 — **Low / optional glance**: build, tests, and API diff are clean; changelog is unavailable (`glance:clean-missing-release-notes`)
- **PR #31** `github.com/go-faster/jx` 1.1.0→1.2.0 — **Low / optional glance**: tests pass and API diff only found non-breaking uncertainty (`glance:tests-pass-soft-api-uncertain`)

## ⚠️ Manual Review Needed

- **PR #13** `github.com/goccy/go-yaml` 1.18.0→1.19.2 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #16** `github.com/go-chi/chi/v5` 5.2.1→5.2.5 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #17** `github.com/prometheus/client_golang` 1.22.0→1.23.2 — Merge Risk: Low (Evidence: clean changelog × Build verification: L2 × Oracle confidence: not available) — minor bump, changelog only bug fixes, no called symbols changed — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #20** `golang.org/x/crypto` 0.43.0→0.49.0 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #22** `golang.org/x/oauth2` 0.30.0→0.36.0 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #23** `go.opentelemetry.io/otel/sdk` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Build verification: L2 × Oracle confidence: not available) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out; verify against the release notes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #25** `golang.org/x/net` 0.45.0→0.52.0 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #26** `gorm.io/driver/sqlite` 1.5.7→1.6.0 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #27** `go.opentelemetry.io/otel/trace` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Build verification: L4 × Oracle confidence: not available) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out; verify against the release notes — Verified clean (L4_tests_pass); routed to review — see the PR comment for the committed verdict
- **PR #29** `golang.org/x/sync` 0.17.0→0.20.0 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #30** `k8s.io/client-go` 0.33.3→0.35.3 — Merge Risk: Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #36** `go.opentelemetry.io/otel/metric` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Build verification: L4 × Oracle confidence: not available) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out; verify against the release notes — Verified clean (L4_tests_pass); routed to review — see the PR comment for the committed verdict

## 🟡 Major CI Action Bumps — Changelog Glance

Major version bumps of CI actions. No application code is affected, but a major bump can change inputs, runtime defaults, or output names and **break the workflow**. Skim the changelog for breaking changes before merging.

| PR | Package | Version | Bump | Merge Risk | Verification |
|----|---------|---------|----|------------|-------------|
| #3 | `azure/setup-kubectl` | 3→5 | major | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | 🟡 major bump — glance changelog |
| #8 | `actions/setup-python` | 5→6 | major | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | 🟡 major bump — glance changelog |

## 🔐 CI Supply-Chain — Review Required (not auto-safe)

These CI actions handle tokens, credentials, registry/cloud auth, code signing, or deployment/publishing. A breaking or compromised release here is a supply-chain risk, so they are **not** auto-cleared. Before merging: **pin to a full commit SHA**, and review the changelog for changed **permissions / token scopes / inputs**.

| PR | Package | Version | Bump | Merge Risk | Verification |
|----|---------|---------|----|------------|-------------|
| #2 | `actions/create-github-app-token` | 1→3 | major | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | ⚠️ REVIEW — supply-chain sensitive |
| #6 | `docker/login-action` | 2→4 | major | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | ⚠️ REVIEW — supply-chain sensitive |
| #9 | `actions/deploy-pages` | 4→5 | major | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | ⚠️ REVIEW — supply-chain sensitive |

## 🛡️ Repository Security Posture

- Open Dependabot alerts: **17**
- Alerts fixable by merging these PRs: **11**
- By severity: critical: 3, high: 8, low: 1, medium: 5

### 🛡️ Security Fixes — Merge with Priority

| PR | Package | Version | CVE(s) | Severity | Fixed in | Advisory |
|---|---|---|---|---|---|---|
| #19 | `github.com/jackc/pgx/v5` | 5.7.4→5.9.1 | CVE-2026-33816 | critical | 5.9.0 | [CVE-2026-33816](https://nvd.nist.gov/vuln/detail/CVE-2026-33816) |
| #28 | `google.golang.org/grpc` | →v1.79.3 (transitive via `github.com/googleapis/gax-go/v2`) | CVE-2025-47914, CVE-2025-58181, CVE-2026-24051, CVE-2026-29181, CVE-2026-33186 | critical, high, medium | 0.45.0, 1.40.0, 1.41.0, 1.79.3 | [CVE-2026-33186](https://nvd.nist.gov/vuln/detail/CVE-2026-33186) [CVE-2026-29181](https://nvd.nist.gov/vuln/detail/CVE-2026-29181) [CVE-2026-24051](https://nvd.nist.gov/vuln/detail/CVE-2026-24051) [CVE-2025-47914](https://nvd.nist.gov/vuln/detail/CVE-2025-47914) [CVE-2025-58181](https://nvd.nist.gov/vuln/detail/CVE-2025-58181) |
| #23 | `go.opentelemetry.io/otel` | →v1.42.0 (transitive via `go.opentelemetry.io/otel/sdk`) | CVE-2026-24051, CVE-2026-29181 | high | 1.40.0, 1.41.0 | [CVE-2026-29181](https://nvd.nist.gov/vuln/detail/CVE-2026-29181) [CVE-2026-24051](https://nvd.nist.gov/vuln/detail/CVE-2026-24051) |
| #27 | `go.opentelemetry.io/otel` | →v1.42.0 (transitive via `go.opentelemetry.io/otel/trace`) | CVE-2026-29181 | high | 1.41.0 | [CVE-2026-29181](https://nvd.nist.gov/vuln/detail/CVE-2026-29181) |
| #36 | `go.opentelemetry.io/otel` | →v1.42.0 (transitive via `go.opentelemetry.io/otel/metric`) | CVE-2026-29181 | high | 1.41.0 | [CVE-2026-29181](https://nvd.nist.gov/vuln/detail/CVE-2026-29181) |
| #11 | `golang.org/x/crypto` | →v0.45.0 (transitive via `github.com/golang-migrate/migrate/v4`) | CVE-2025-47914, CVE-2025-58181 | medium | 0.45.0 | [CVE-2025-47914](https://nvd.nist.gov/vuln/detail/CVE-2025-47914) [CVE-2025-58181](https://nvd.nist.gov/vuln/detail/CVE-2025-58181) |
| #16 | `github.com/go-chi/chi/v5` | 5.2.1→5.2.5 | GHSA-vrw8-fxc6-2r93 | medium | 5.2.2 | _see Dependabot_ |
| #20 | `golang.org/x/crypto` | 0.43.0→0.49.0 | CVE-2025-47914, CVE-2025-58181 | medium | 0.45.0 | [CVE-2025-47914](https://nvd.nist.gov/vuln/detail/CVE-2025-47914) [CVE-2025-58181](https://nvd.nist.gov/vuln/detail/CVE-2025-58181) |
| #25 | `golang.org/x/crypto` | →v0.49.0 (transitive via `golang.org/x/net`) | CVE-2025-47914, CVE-2025-58181 | medium | 0.45.0 | [CVE-2025-47914](https://nvd.nist.gov/vuln/detail/CVE-2025-47914) [CVE-2025-58181](https://nvd.nist.gov/vuln/detail/CVE-2025-58181) |
| #32 | `golang.org/x/crypto` | →v0.48.0 (transitive via `github.com/go-openapi/strfmt`) | CVE-2025-47914, CVE-2025-58181 | medium | 0.45.0 | [CVE-2025-47914](https://nvd.nist.gov/vuln/detail/CVE-2025-47914) [CVE-2025-58181](https://nvd.nist.gov/vuln/detail/CVE-2025-58181) |

> ℹ️ **Some CVEs are delivered by more than one PR — merge any one to clear them:**
>   - `CVE-2025-47914`: #11, #20, #25, #28, #32
>   - `CVE-2025-58181`: #11, #20, #25, #28, #32
>   - `CVE-2026-24051`: #23, #28
>   - `CVE-2026-29181`: #23, #27, #28, #36

---
> 🔬 *Deterministic merge plan — generated from build-results.json. Refer to individual PR comments for full details.*

