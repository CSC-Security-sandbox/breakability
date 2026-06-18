<!-- breakability-merge-plan -->
# 📋 Breakability Merge Plan

**Generated:** 2026-06-11 12:16 UTC (deterministic)
**PRs analyzed:** 33 Dependabot PRs

> ⏱️ **Snapshot** generated at `2026-06-11 12:16 UTC`. PR states may have changed since analysis.
> To refresh: `gh workflow run breakability-agent.yml`

## ⚡ What to Do Next

> **TLDR:** Jump to [Developer Action Summary](#developer-action-summary) for numbered merge steps. Or:

- 🛑 **Fix first:** 2 PR(s) have blocking verification issues — see 'Fix Required' below.
- 🔐 **Priority merge:** 3 PR(s) fix known CVEs — merge them first.
- 🔴 **Review required:** 9 PR(s) need careful review before merge.
- 📋 **Follow the numbered plan:** 5 PR(s) need review/glance handling — see exact actions below.

<details><summary><strong>📊 Technical Details & Risk Classification</strong> (L-levels, severity, counts)</summary>

## Summary by Verification Level

| Category | Count |
|----------|-------|
| ✅ Safe to merge — tests pass (L4) | 0 |
| ✅ Build passes — review recommended (L2/L3) | 19 |
| 🔗 Blocked (safe but companion PR needs fix) | 1 |
| 🔧 CI-only (Actions/Docker — no app impact) | 2 |
| 🔐 CI supply-chain (auth/token/registry/deploy) — security review | 3 |
| ❌ Fix required | 2 |
| 🔴 Review required (High) | 6 |

## Breakability Summary

🔴 **High:** 9 · 🟠 **Medium:** 5 · 🟡 **Low:** 12 · 🟢 **None:** 7

> High/Medium = worth a review · Low = optional glance · None = safe to merge. Severity matches each PR's breakability headline (security-fix PRs show a merge-priority headline instead).

</details>

## Developer Action Summary

**Plain-English merge guidance — see Technical Details above for verification levels.**

1. **REVIEW then MERGE — CVE fixes (build passes, tests not run):** #16, #19, #20 — check build details, then merge
2. **GLANCE then MERGE — build passes, tests not run:** 16 PR(s) — skim changelog for breaking changes
3. **WAIT — paired PRs blocked:** #30 — merge these only after fixing their companion PR
4. **MERGE — CI/Actions PRs:** 2 PR(s) — no app impact
5. **REVIEW — supply-chain sensitive CI:** #2, #6, #9 — pin to commit SHA, verify permissions
6. **FIX NEEDED:** 2 PR(s) have blocking verification issues

## 🔴 Security — CVEs Fixed by These Upgrades

> **ACTION REQUIRED:** Merge security fix PRs as soon as possible to resolve known vulnerabilities.

- **PR #16** `github.com/go-chi/chi/v5` 5.2.1→5.2.5 — GHSA-vrw8-fxc6-2r93 ⚙️ **Build verified (L2/L3) — tests not verified clean; review then merge**
- **PR #19** `github.com/jackc/pgx/v5` 5.7.4→5.9.1 — CVE-2026-33816 ⚙️ **Build verified (L2/L3) — tests not verified clean; review then merge**
- **PR #20** `golang.org/x/crypto` 0.43.0→0.49.0 — CVE-2025-47914, CVE-2025-58181, CVE-2025-47914, CVE-2025-58181 ⚙️ **Build verified (L2/L3) — tests not verified clean; review then merge**
- **PR #10** `github.com/andygrunwald/go-jira` 1.16.0→1.17.0 — CVE-2025-30204 (claimed in PR body — not version-verified vs fixed-in) ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)
- **PR #23** `go.opentelemetry.io/otel/sdk` 1.38.0→1.42.0 — CVE-2026-24051, CVE-2026-24051 ⚠️ **Review required** — see Manual Review Needed below (not auto-safe)

## ✅ Build Passes — Review Recommended (L2/L3 verified)

> Build and type-check pass. Tests were not run or had pre-existing failures. Review changelog for major bumps.

| PR | Package | Version | Bump | Merge Risk | Verification |
|----|---------|---------|----|------------|-------------|
| #1 | `github.com/sirupsen/logrus` | 1.9.3→1.9.4 | patch | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | L2_type_checked |
| #4 | `golang.org/x/oauth2` | 0.32.0→0.36.0 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked |
| #5 | `github.com/spf13/cobra` | 1.10.1→1.10.2 | patch | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | L2_type_checked |
| #11 | `github.com/golang-migrate/migrate/v4` | 4.18.2→4.19.1 | minor | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L2 × Oracle confidence: not available) — Ran `grep -rn "CasRestoreOnErr" . --include="*.go"` in bumped module `.` - returned 0 matches. Verified our only migrate usage file `database/drivers/postgres/migrate.go` uses only: postgres.WithInstance(), postgres.Config{}, migrate.NewWithInstance(), and error constants (ErrNoChange, ErrNilVersion). Ran `grep -rn '"github.com/golang-migrate/migrate/v4/database"' . --include="*.go"` - 0 matches, confirming we don't import the database package directly where CasRestoreOnErr lives. Symbol is an internal database driver utility we never call. | L2_type_checked |
| #13 | `github.com/goccy/go-yaml` | 1.18.0→1.19.2 | minor | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L2 × Oracle confidence: not available) — grep -rn 'AllowFieldPrefixes' cicd --include='*.go' → exit 1 (no matches); grep -rn 'RawMessage' cicd --include='*.go' → exit 1 (no matches); grep -rn 'yaml\.Unmarshal' cicd --include='*.go' → 3 uses found (link-check.go:87, lint.go:47, utils.go:88) but none reference the new symbols. Apidiff shows only ADDED symbols (AllowFieldPrefixes, RawMessage) which cannot break code that compiled before they existed. Build passes on PR branch. | L2_type_checked |
| #14 | `cloud.google.com/go/monitoring` | 1.24.2→1.24.3 | patch | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L2 × Oracle confidence: not available) — grep -rn 'RegisterDashboardsServiceServer|RegisterMetricsScopesServer|RegisterAlertPolicyServiceServer|RegisterGroupServiceServer|RegisterMetricServiceServer|RegisterNotificationChannelServiceServer|RegisterQueryServiceServer|RegisterServiceMonitoringServiceServer|RegisterSnoozeServiceServer|RegisterUptimeCheckServiceServer' . --include='*.go' returned NO matches. grep -rn 'cloud.google.com/go/monitoring' . found only CLIENT usage: telemetry/main.go:95 calls monitoring.NewMetricClient(ctx), telemetry/collector/volume_metrics.go:31 calls client.ListTimeSeries(ctx, req). Checked upstream googleapis PR #13063: 'update to Go gRPC Protobuf generation will change service registration function signatures to use an interface instead of a concrete type' - this affects SERVER implementations only. Codebase is CLIENT-ONLY consumer, never implements or registers any monitoring gRPC servers. | L2_type_checked |
| #15 | `golang.org/x/oauth2` | 0.30.0→0.36.0 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked |
| #16 | `github.com/go-chi/chi/v5` | 5.2.1→5.2.5 | patch | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked 🔴 GHSA-vrw8-fxc6-2r93 |
| #17 | `github.com/prometheus/client_golang` | 1.22.0→1.23.2 | minor | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L2 × Oracle confidence: not available) — grep -rn 'WrapCollectorWith' . --include='*.go' → 0 hits; grep -rn 'WrapCollectorWithPrefix' . --include='*.go' → 0 hits. Changelog confirms v1.23.0 added WrapCollectorWith/WrapCollectorWithPrefix (new symbols we don't use), v1.23.1/v1.23.2 state 'no functional changes.' Build passes. | L2_type_checked |
| #19 | `github.com/jackc/pgx/v5` | 5.7.4→5.9.1 | minor | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L2 × Oracle confidence: not available) — Ran grep -rn 'BackendKeyData|CancelRequest|MultiResultReader|ContextWatcher|HijackedConn|PgConn' . --include=*.go — zero hits. Ran grep -rn 'github.com/jackc/pgx/v5/' . --include=*.go | grep import — repo only imports pgconn subpackage. Ran grep -rn 'pgconn\.' . --include=*.go — repo only uses pgconn.PgError type. Ran go doc github.com/jackc/pgx/v5/pgconn.PgError — confirmed PgError structure unchanged. Ran grep -rn 'pgerr\.' . --include=*.go — repo only accesses .Code and .Message fields, both unchanged. None of the breaking symbols (BackendKeyData, CancelRequest, HijackedConn, MultiResultReader, ContextWatcher, PlanScan) are used in this codebase. | L2_type_checked 🔴 CVE-2026-41889,CVE-2026-33816 |
| #20 | `golang.org/x/crypto` | 0.43.0→0.49.0 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked 🔴 CVE-2025-47914,CVE-2025-58181 |
| #22 | `golang.org/x/oauth2` | 0.30.0→0.36.0 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked |
| #24 | `github.com/go-openapi/errors` | 0.22.1→0.22.7 | patch | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked |
| #25 | `golang.org/x/net` | 0.45.0→0.52.0 | major ⚠️ (0.x unstable) | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L2 × Oracle confidence: not available) — Searched for new golang.org/x/net symbols (HTTPSResource, SVCBResource, PriorityUpdateFrame, SettingNoRFC7540Priorities): grep -rn 'HTTPSResource\|SVCBResource\|PriorityUpdateFrame\|SettingNoRFC7540Priorities' --include='*.go' . → Only found unrelated string constants 'https'. Checked actual imports: grep -rn '"golang.org/x/net' --include='*.go' . → Found 3 files importing golang.org/x/net/context. Verified usage in utils/auth/credentialsClient.go: only uses context.Context type which was NOT changed. All apidiff entries are 'added (soft)' - new symbols only, backwards compatible. Build passed on PR branch. | L2_type_checked |
| #26 | `gorm.io/driver/sqlite` | 1.5.7→1.6.0 | minor | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked |
| #28 | `github.com/googleapis/gax-go/v2` | 2.14.2→2.20.0 | minor | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L2 × Oracle confidence: not available) — Ran 'grep -rn "gax\.Version" .' and 'grep -rn "github.com/googleapis/gax-go/v2"' - found 4 files importing gax-go/v2 (utils/auth/*.go) but ZERO references to gax.Version. Only gax.CallOption is used. Build passed on PR branch. | L2_type_checked |
| #29 | `golang.org/x/sync` | 0.17.0→0.20.0 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked |
| #31 | `github.com/go-faster/jx` | 1.1.0→1.2.0 | minor | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked |
| #38 | `github.com/lib/pq` | 1.11.2→1.12.3 | minor | Low (Evidence: AI-adjudicated: change not reachable in the bumped module × Build verification: L3 × Oracle confidence: not available) — Dependency `github.com/lib/pq` is not imported in the bumped module `automations/tstctl`; a breaking API change in it cannot reach this module. The flagged usages, if any, live in a different go.mod. | L3_symbols_verified |

## 🔗 Blocked — Safe but Companion PR Needs Fix First

These PRs pass build verification but are **blocked** because a companion PR (coordinated upgrade) currently has build failures or security issues.
Fix the companion PR first, then merge both together.

| PR | Package | Version | Bump | Merge Risk | Verification | Blocked By |
|----|---------|---------|------|------------|-------------|------------|
| #30 | `k8s.io/client-go` | 0.33.3→0.35.3 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | L2_type_checked ✅ | Fix #21 first |

## 🔗 Coordinated Upgrades (merge together)

- ⛔ **K8s module coordination: k8s.io/apimachinery + k8s.io/client-go must match versions:** #21 + #30 — **DO NOT MERGE as a group.** #21 build fails. Resolve #21 first (see sections below); merging the group now would pull in the blocking PR.
- **OTel coordination: SDK + metric should match:** #23 + #36 (merge together)
- **OTel coordination: SDK + trace should match:** #23 + #27 (merge together)
- **OTel coordination: trace + metric should match:** #27 + #36 (merge together)

## ❌ Fix Required — Do Not Merge

| PR | Package | Version | Bump | Merge Risk | Issue |
|----|---------|---------|----|------------|-------|
| #18 | `github.com/pb33f/libopenapi` | 0.21.12→0.34.4 | major ⚠️ (0.x unstable) | High (Evidence: break-reachable API change × Build verification: L2 × Oracle confidence: not available) — BREAK-reachable type changed API symbol `Map.OrderedMap` | Build fails |
| #21 | `k8s.io/apimachinery` | 0.33.3→0.35.3 | major ⚠️ (0.x unstable) | Medium (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution | Build fails |

## ⚠️ Manual Review Needed

- **PR #10** `github.com/andygrunwald/go-jira` 1.16.0→1.17.0 — Merge Risk: High (Evidence: declared breaking change (changelog), behavior unverified × Build verification: L2 × Oracle confidence: not available) — declared breaking change unverified by build/test: ### Breaking Changes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #12** `github.com/spf13/cobra` 1.9.1→1.10.2 — Merge Risk: High (Evidence: declared breaking change (changelog), behavior unverified × Build verification: L2 × Oracle confidence: not available) — declared breaking change unverified by build/test: This version of `pflag` carried a breaking change: it renamed `ParseErrorsWhitelist` to `ParseErrorsAllowlist` which can break builds if both `pflag` and `cobra` are dependencies in your project. — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #23** `go.opentelemetry.io/otel/sdk` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Build verification: L2 × Oracle confidence: not available) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out; verify against the release notes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #27** `go.opentelemetry.io/otel/trace` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Build verification: L2 × Oracle confidence: not available) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out; verify against the release notes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #32** `github.com/go-openapi/strfmt` 0.23.0→0.26.1 — Merge Risk: High (Evidence: release-note breaking surface × Build verification: L2 × Oracle confidence: not available) — release notes mention config/middleware/pipeline behavior changes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict
- **PR #36** `go.opentelemetry.io/otel/metric` 1.38.0→1.42.0 — Merge Risk: Medium (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Build verification: L2 × Oracle confidence: not available) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out; verify against the release notes — Verified clean (L2_type_checked); routed to review — see the PR comment for the committed verdict

## 🔧 CI-Only (Actions / Docker — no application impact)

These PRs only affect CI/CD workflows. No build verification needed — zero app code impact.

| PR | Package | Version | Bump | Merge Risk | Verification |
|----|---------|---------|----|------------|-------------|
| #3 | `azure/setup-kubectl` | 3→5 | major | Medium (Evidence: limited evidence × Build verification: L2 × Oracle confidence: not available) — change evidence is limited; default caution | CI_ONLY — auto-safe |
| #8 | `actions/setup-python` | 5→6 | major | Medium (Evidence: limited evidence × Build verification: L0 × Oracle confidence: not available) — change evidence is limited; default caution | CI_ONLY — auto-safe |

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
| #23 | `go.opentelemetry.io/otel/sdk` | 1.38.0→1.42.0 | CVE-2026-24051 | high | 1.40.0 | [CVE-2026-24051](https://nvd.nist.gov/vuln/detail/CVE-2026-24051) [CVE-2026-24051](https://nvd.nist.gov/vuln/detail/CVE-2026-24051) |
| #16 | `github.com/go-chi/chi/v5` | 5.2.1→5.2.5 | GHSA-vrw8-fxc6-2r93 | medium | 5.2.2 | _see Dependabot_ |
| #20 | `golang.org/x/crypto` | 0.43.0→0.49.0 | CVE-2025-47914, CVE-2025-58181 | medium | 0.45.0 | [CVE-2025-47914](https://nvd.nist.gov/vuln/detail/CVE-2025-47914) [CVE-2025-58181](https://nvd.nist.gov/vuln/detail/CVE-2025-58181) [CVE-2025-47914](https://nvd.nist.gov/vuln/detail/CVE-2025-47914) [CVE-2025-58181](https://nvd.nist.gov/vuln/detail/CVE-2025-58181) |

### ⚠️ Orphan Alerts — No PR Fixes These

_These open Dependabot alerts have **no corresponding PR** in this batch. Manual remediation required._

| Package | CVE | Severity | Fixed in (upstream) |
|---|---|---|---|
| `google.golang.org/grpc` | [CVE-2026-33186](https://nvd.nist.gov/vuln/detail/CVE-2026-33186) | **critical** | 1.79.3 |
| `google.golang.org/grpc` | [CVE-2026-33186](https://nvd.nist.gov/vuln/detail/CVE-2026-33186) | **critical** | 1.79.3 |
| `go.opentelemetry.io/otel` | [CVE-2026-29181](https://nvd.nist.gov/vuln/detail/CVE-2026-29181) | **high** | 1.41.0 |
| `go.opentelemetry.io/otel` | [CVE-2026-29181](https://nvd.nist.gov/vuln/detail/CVE-2026-29181) | **high** | 1.41.0 |
| `go.opentelemetry.io/otel/sdk` | [CVE-2026-39883](https://nvd.nist.gov/vuln/detail/CVE-2026-39883) | **high** | 1.43.0 |
| `go.opentelemetry.io/otel/sdk` | [CVE-2026-39883](https://nvd.nist.gov/vuln/detail/CVE-2026-39883) | **high** | 1.43.0 |
| `github.com/go-jose/go-jose/v4` | [CVE-2026-34986](https://nvd.nist.gov/vuln/detail/CVE-2026-34986) | **high** | 4.1.4 |
| `github.com/buger/jsonparser` | [CVE-2026-32285](https://nvd.nist.gov/vuln/detail/CVE-2026-32285) | **high** | 1.1.2 |
| `github.com/jackc/pgx/v5` | [CVE-2026-41889](https://nvd.nist.gov/vuln/detail/CVE-2026-41889) | **low** | 5.9.2 |

---
> 🔬 *Deterministic merge plan — generated from build-results.json. Refer to individual PR comments for full details.*
