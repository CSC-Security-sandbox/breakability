<!-- breakability-merge-plan -->
# 📋 Breakability Merge Plan

**Generated:** 2026-06-05 12:48 UTC (deterministic)
**PRs analyzed:** 33 Dependabot PRs

> ⏱️ **Snapshot** generated at `2026-06-05 12:48 UTC`. PR states may have changed since analysis.
> To refresh: `gh workflow run breakability-agent.yml`

## Summary

| Category | Count |
|----------|-------|
| ✅ Safe to merge — tests pass (L4) | 3 |
| ✅ Build passes — review recommended (L2/L3) | 5 |
| 🔧 CI-only (Actions/Docker — no app impact) | 5 |
| ❌ Fix required | 3 |
| 🔴 Review required (High) | 5 |
| 🟠 Review recommended (Medium) | 5 |
| 🟡 Optional glance (Low) | 7 |

## Breakability Summary

🔴 **High:** 8 · 🟠 **Medium:** 5 · 🟡 **Low:** 19 · 🟢 **None:** 1

> High/Medium = worth a review · Low = optional glance · None = safe to merge. Severity matches each PR's breakability headline (security-fix PRs show a merge-priority headline instead).

## Developer Action Summary

1. **MERGE NOW — security fixes:** #28 (1 PR(s) fix known CVEs — tests verified L4; CVE reachability is hint-only, patch regardless)
2. **MERGE AFTER REVIEW — security fixes (tests not verified clean):** #16 (1 PR(s) fix known CVEs — build verified L2/L3 but tests did not pass or were not run; verify, then merge — CVE reachability is hint-only, patch regardless)
3. **Batch merge — 3 PRs with full test pass** (L4 verified, lowest risk — excluding CVE PR above)
4. **Review then merge — 4 PRs** (build + type-check pass, tests not verified clean — review changelog before merging)
5. **Merge CI/Actions PRs — 5 PRs** (no app code impact)
6. **Assign to team — 3 PRs need code fixes** before merge

## 🔴 Security — CVEs Fixed by These Upgrades

> **ACTION REQUIRED:** Merge security fix PRs as soon as possible to resolve known vulnerabilities.

- **PR #16** `github.com/go-chi/chi/v5` 5.2.1→5.2.5 — GHSA-vrw8-fxc6-2r93 ⚙️ **Build verified (L2/L3) — tests not verified clean; review then merge**
- **PR #28** `github.com/googleapis/gax-go/v2` 2.14.2→2.20.0 — CVE-2026-33186, CVE-2026-29181, CVE-2026-24051, CVE-2025-47914, CVE-2025-58181 ✅ **SAFE — merge now** (tests pass, L4)
- **PR #10** `github.com/andygrunwald/go-jira` 1.16.0→1.17.0 — CVE-2025-30204 (claimed in PR body — not version-verified vs fixed-in) ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #11** `github.com/golang-migrate/migrate/v4` 4.18.2→4.19.1 — CVE-2025-47914, CVE-2025-58181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #19** `github.com/jackc/pgx/v5` 5.7.4→5.9.1 — CVE-2026-33816 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #20** `golang.org/x/crypto` 0.43.0→0.49.0 — CVE-2025-47914, CVE-2025-58181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #23** `go.opentelemetry.io/otel/sdk` 1.38.0→1.42.0 — CVE-2026-29181, CVE-2026-24051 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #25** `golang.org/x/net` 0.45.0→0.52.0 — CVE-2025-47914, CVE-2025-58181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #27** `go.opentelemetry.io/otel/trace` 1.38.0→1.42.0 — CVE-2026-29181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #32** `github.com/go-openapi/strfmt` 0.23.0→0.26.1 — CVE-2025-47914, CVE-2025-58181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #36** `go.opentelemetry.io/otel/metric` 1.38.0→1.42.0 — CVE-2026-29181 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)

## ✅ Safe to Merge — Tests Pass (L4 verified, lowest risk)

| PR | Package | Version | Bump | Merge Risk | Verification |
|----|---------|---------|----|------------|-------------|
| #26 | `gorm.io/driver/sqlite` | 1.5.7→1.6.0 | minor | Medium (Evidence: changelog unavailable × Confidence: L4) — missing or unparsable changelog; default caution | L4_tests_pass |
| #28 | `github.com/googleapis/gax-go/v2` | 2.14.2→2.20.0 | minor | Medium (Evidence: changelog unavailable × Confidence: L4) — missing or unparsable changelog; default caution | L4_tests_pass |
| #31 | `github.com/go-faster/jx` | 1.1.0→1.2.0 | minor | Medium (Evidence: changelog unavailable × Confidence: L4) — missing or unparsable changelog; default caution | L4_tests_pass |

## ✅ Build Passes — Review Recommended (L2/L3 verified)

> Build and type-check pass. Tests were not run or had pre-existing failures. Review changelog for major bumps.

| PR | Package | Version | Bump | Merge Risk | Verification |
|----|---------|---------|----|------------|-------------|
| #13 | `github.com/goccy/go-yaml` | 1.18.0→1.19.2 | minor | Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution | L2_type_checked |
| #14 | `cloud.google.com/go/monitoring` | 1.24.2→1.24.3 | patch | Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution | L2_type_checked |
| #16 | `github.com/go-chi/chi/v5` | 5.2.1→5.2.5 | patch | Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution | L2_type_checked 🔴 GHSA-vrw8-fxc6-2r93 |
| #17 | `github.com/prometheus/client_golang` | 1.22.0→1.23.2 | minor | Low (Evidence: clean changelog × Confidence: L2) — minor bump, changelog only bug fixes, no called symbols changed | L2_type_checked |
| #24 | `github.com/go-openapi/errors` | 0.22.1→0.22.7 | patch | Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution | L2_type_checked |

## 🔗 Coordinated Upgrades (merge together)

- ⛔ **K8s module coordination: k8s.io/apimachinery + k8s.io/client-go must match versions:** #21 + #30 — **DO NOT MERGE as a group.** #21 build fails. Resolve #21 first (see sections below); merging the group now would pull in the blocking PR.
- **OpenTelemetry release-train coordination: go.opentelemetry.io/otel/sdk + go.opentelemetry.io/otel/metric share the otel core version — merge together to keep the resolved otel version consistent:** #23 + #27 + #36 — merge all 3 together
- **Same package (github.com/spf13/cobra) in different modules: automations/tstctl + cicd:** #5 + #12 (merge all to fully upgrade)
- ⛔ **Same package (golang.org/x/oauth2) in different modules: cicd + /:** #4 + #15 + #22 — **DO NOT MERGE as a group.** #22 build fails. Resolve #22 first (see sections below); merging the group now would pull in the blocking PR.

## ❌ Fix Required — Do Not Merge

| PR | Package | Version | Bump | Merge Risk | Issue |
|----|---------|---------|----|------------|-------|
| #18 | `github.com/pb33f/libopenapi` | 0.21.12→0.34.4 | major ⚠️ (0.x unstable) | High (Evidence: break-reachable API change × Confidence: L3) — BREAK-reachable type changed API symbol `Document` | Build fails |
| #21 | `k8s.io/apimachinery` | 0.33.3→0.35.3 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution | Build fails |
| #22 | `golang.org/x/oauth2` | 0.30.0→0.36.0 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution | Build fails |

## ⚠️ Manual Review Needed

- **PR #1** `github.com/sirupsen/logrus` 1.9.3→1.9.4 — Merge Risk: Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #4** `golang.org/x/oauth2` 0.32.0→0.36.0 — Merge Risk: Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #5** `github.com/spf13/cobra` 1.10.1→1.10.2 — Merge Risk: Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #10** `github.com/andygrunwald/go-jira` 1.16.0→1.17.0 — Merge Risk: High (Evidence: declared breaking change (changelog), behavior unverified × Confidence: L2) — declared breaking change unverified by build/test: ### Breaking Changes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #11** `github.com/golang-migrate/migrate/v4` 4.18.2→4.19.1 — Merge Risk: High (Evidence: break-reachable API change × Confidence: L3) — BREAK-reachable type changed API symbol `NewWithInstance` — Verified clean (L3_symbols_verified); routed to review — see the PR comment for the committed verdict
- **PR #12** `github.com/spf13/cobra` 1.9.1→1.10.2 — Merge Risk: High (Evidence: declared breaking change (changelog), behavior unverified × Confidence: L2) — declared breaking change unverified by build/test: This version of `pflag` carried a breaking change: it renamed `ParseErrorsWhitelist` to `ParseErrorsAllowlist` which can break builds if both `pflag` and `cobra` are dependencies in your project. — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #15** `golang.org/x/oauth2` 0.30.0→0.36.0 — Merge Risk: Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #19** `github.com/jackc/pgx/v5` 5.7.4→5.9.1 — Merge Risk: Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #20** `golang.org/x/crypto` 0.43.0→0.49.0 — Merge Risk: Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #23** `go.opentelemetry.io/otel/sdk` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Confidence: L4) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out — behavioral oracle graded exposure Medium (see PR comment) — Verified clean (L4_tests_pass); routed to review — see the PR comment for the committed verdict
- **PR #25** `golang.org/x/net` 0.45.0→0.52.0 — Merge Risk: Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #27** `go.opentelemetry.io/otel/trace` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Confidence: L4) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out — behavioral oracle graded exposure Low (see PR comment) — Verified clean (L4_tests_pass); routed to review — see the PR comment for the committed verdict
- **PR #29** `golang.org/x/sync` 0.17.0→0.20.0 — Merge Risk: Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #30** `k8s.io/client-go` 0.33.3→0.35.3 — Merge Risk: Medium (Evidence: changelog unavailable × Confidence: L2) — missing or unparsable changelog; default caution — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #32** `github.com/go-openapi/strfmt` 0.23.0→0.26.1 — Merge Risk: High (Evidence: release-note breaking surface × Confidence: L2) — release notes mention config/middleware/pipeline behavior changes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #36** `go.opentelemetry.io/otel/metric` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Confidence: L4) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out — behavioral oracle graded exposure Medium (see PR comment) — Verified clean (L4_tests_pass); routed to review — see the PR comment for the committed verdict
- **PR #38** `github.com/lib/pq` 1.11.2→1.12.3 — Merge Risk: High (Evidence: break-reachable API change × Confidence: L3) — BREAK-reachable type changed API symbol `Error.Code` — Verified clean (L3_symbols_verified); routed to review — see the PR comment for the committed verdict

## 🔧 CI-Only (Actions / Docker — no application impact)

These PRs only affect CI/CD workflows. No build verification needed — zero app code impact.

| PR | Package | Version | Bump | Merge Risk | Verification |
|----|---------|---------|----|------------|-------------|
| #2 | `actions/create-github-app-token` | 1→3 | major | Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution | CI_ONLY — auto-safe |
| #3 | `azure/setup-kubectl` | 3→5 | major | Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution | CI_ONLY — auto-safe |
| #6 | `docker/login-action` | 2→4 | major | Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution | CI_ONLY — auto-safe |
| #8 | `actions/setup-python` | 5→6 | major | Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution | CI_ONLY — auto-safe |
| #9 | `actions/deploy-pages` | 4→5 | major | Medium (Evidence: limited evidence × Confidence: L2) — change evidence is limited; default caution | CI_ONLY — auto-safe |

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

### ⚠️ Orphan Alerts — No PR Fixes These

_These open Dependabot alerts have **no corresponding PR** in this batch. Manual remediation required._

| Package | CVE | Severity | Fixed in (upstream) |
|---|---|---|---|
| `go.opentelemetry.io/otel/sdk` | [CVE-2026-39883](https://nvd.nist.gov/vuln/detail/CVE-2026-39883) | **high** | 1.43.0 |
| `github.com/go-jose/go-jose/v4` | [CVE-2026-34986](https://nvd.nist.gov/vuln/detail/CVE-2026-34986) | **high** | 4.1.4 |
| `github.com/buger/jsonparser` | [CVE-2026-32285](https://nvd.nist.gov/vuln/detail/CVE-2026-32285) | **high** | 1.1.2 |
| `github.com/jackc/pgx/v5` | [CVE-2026-41889](https://nvd.nist.gov/vuln/detail/CVE-2026-41889) | **low** | 5.9.2 |

---
> 🔬 *Deterministic merge plan — generated from build-results.json. Refer to individual PR comments for full details.*
