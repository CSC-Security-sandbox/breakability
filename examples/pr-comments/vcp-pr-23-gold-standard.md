🔴 Breakability: High · REVIEW THEN MERGE — also resolves 2 HIGH CVE(s); the breaking change is the dominant risk (verify the call sites below), merging still clears the CVE · Oracle confidence: not available · Priority: P0

Why: Improve error handling for dropped data during translation by using `prometheus.NewInvalidMetric` in `go.opentelemetry.io/otel/exporters/prometheus`. ⚠️ **Breaking Change:** Previously, these cases we — this break depends on runtime state/load/timing and is not reproducible from a minimal probe; assess against your usage.
→ Affected package used at utils/middleware/log/metric.go:22. Check whether your usage relies on the changed behavior.
Reachable at: utils/middleware/log/metric.go:22

### Signals checked
| Signal | Result |
|---|---|
| Resolve | ✅ checked-clean |
| Build | ✅ checked-clean |
| Test | ✅ checked-clean |
| API diff | ✅ checked-clean |
| Usage | · not observed |
| Vulnerability | – n/a |
| Changelog | ⚠️ concern |

<!-- breakability-check -->

<details><summary>Internal merge-risk detail</summary>

## 🔴 SECURITY FIX (HIGH) — `go.opentelemetry.io/otel/sdk` 1.38.0 → 1.42.0 · production · minor

**Merge Risk: Medium** (Evidence: declared behavioral change in a used package (internal trigger), unverified by build/test/api-diff × Build verification: L4 × Oracle confidence: not available) — review required: the changelog declares a BEHAVIORAL breaking change inside a package your production code uses (go.opentelemetry.io/otel/exporters/prometheus) (e.g. prometheus.New at utils/middleware/log/metric.go:22); the change is internal to the package, so whether it affects you depends on your runtime data/configuration — build, tests, and API-diff cannot confirm or rule it out; verify against the release notes

### ⚠️ 2 CVE(s) resolved (version-verified): CVE-2026-29181 (HIGH) · CVE-2026-24051 (HIGH)
**Severity: HIGH** — This PR fixes a known security vulnerability.

**Build Impact:** No new errors introduced by this upgrade.
Build passes on PR branch.

### Heads-up: CVE reachability (hint only)
No reachable path found by a scanner is **not** safe-to-ignore evidence. Patch regardless: this PR resolves the advisory.

### Recommendation
**MERGE NOW.** It resolves 2 version-verified known CVE(s) (the resulting version reaches each advisory's fixed-in version), introduces zero new build errors, and the test suite passes.
Security fixes should be prioritized over routine dependency upgrades.


Verification: **L4_tests_pass**
📋 Merge plan: #138
<details><summary>🔍 How we checked (verification: L4_tests_pass)</summary>

- ✅ Dependency resolved — `go mod tidy (affected modules) for go.opentelemetry.io/otel/sdk 1.38.0→1.42.0` — go.mod/go.sum: 9 changed, 0 new, 11 removed: github.com/rogpeppe/go-internal v1.13.1/go.mod→v1.14.1/go.mod; go 1.24.11→1.25.0; go.opentelemetry.io/auto/sdk v1.1.0/go.mod→v1.2.1/go.mod; go.opentelemetry.io/otel v1.38.0/go.mod→v1.42.0/go.mod; go.opentelemetry.io/otel/metric v1.38.0/go.mod→v1.42.0/go.mod; go.opentelemetry.io/otel/sdk v1.38.0/go.mod→v1.42.0/go.mod
- ✅ Build passes — `targeted build (root module): 2 dirs` — exit 0, 0 new error(s)
- ✅ Tests pass (exit=0) — 2 package(s) ok — no regressions vs main
- ✅ Diffed error output: PR introduces 0 new diagnostics
- ℹ️ go.sum: 8 new transitive deps: github.com/rogpeppe/go-internal,go.opentelemetry.io/auto/sdk,go.opentelemetry.io/otel,go.opentelemetry.io/otel/metric,go.opentelemetry.io/otel/sdk
- ⚠️ **CVE reachability NOT computed for this PR.** govulncheck (call-graph reachability on _our_ source) is disabled by config; the CVE list comes from **Dependabot**, which matches advisory version-ranges only — it does NOT prove the vulnerable symbol is reachable from our code, nor detect NEW CVEs the target version may regress in.
  - To get a per-CVE call-chain proof, re-run with `BREAKABILITY_GOVULNCHECK=1`.
  - <!-- TODO(AI-LAYER): rank which of these CVEs are actually reachable from our call-graph and whether merging this PR delivers the fix; this is decision-support govulncheck/Dependabot alone cannot synthesize. --> Until then, treat the CVE list as advisory (version-match), not reachability-confirmed.
</details>

### BREAK-reachability context
- No exported API symbols changed per apidiff; reachability is import-level only (see imported files below).

### Reachability of the declared break
- ⚠️ **Uses the affected package, but not the named symbol directly.** Your production code calls into the package surface:
  - `prometheus.New` at `utils/middleware/log/metric.go:22`  ·  package `go.opentelemetry.io/otel/exporters/prometheus`
  - `attribute.KeyValue` at `core/core-api/core-servergen/oas_handlers_gen.go:41`  ·  package `go.opentelemetry.io/otel/attribute`
  - `attribute.Set` at `core/core-api/core-servergen/oas_labeler_gen.go:21`  ·  package `go.opentelemetry.io/otel/attribute`
- The declared change centers on `Distinct`, `NewInvalidMetric`, which the library runs **internally** (e.g. during scrape / collect / serialize), not via a call you make directly. So whether it affects you depends on your **runtime data and configuration** — build, tests, and API-diff cannot see this.
- This is a **manual-review signal, not a confirmed break** — graded **Medium / Review**, not High. To settle it: check whether your usage relies on the changed behavior described in the release notes. If it does not, this signal does not block the merge.
<details><summary>📂 Files importing this package (2 file(s))</summary>

- `./ontap-proxy/middleware/metrics_test.go`
- `./utils/middleware/log/metric.go`
</details>
<details><summary>📦 Go dependency-resolution output</summary>

```
--- go mod tidy: . (exit=0) ---
```
</details>
<details><summary>🧾 go.mod / go.sum diff</summary>

```diff
diff --git a/.github/tools/reachability/go.mod b/.github/tools/reachability/go.mod
deleted file mode 100644
index 81db8c74..00000000
--- a/.github/tools/reachability/go.mod
+++ /dev/null
@@ -1,10 +0,0 @@
-module breakability/reachability
-
-go 1.25.0
-
-require golang.org/x/tools v0.45.0
-
-require (
-	golang.org/x/mod v0.36.0 // indirect
-	golang.org/x/sync v0.20.0 // indirect
-)
diff --git a/.github/tools/reachability/go.sum b/.github/tools/reachability/go.sum
deleted file mode 100644
index be8b55bf..00000000
--- a/.github/tools/reachability/go.sum
+++ /dev/null
@@ -1,8 +0,0 @@
-github.com/google/go-cmp v0.6.0 h1:ofyhxvXcZhMsU5ulbFiLKl/XBFqE1GSq7atu8tAmTRI=
-github.com/google/go-cmp v0.6.0/go.mod h1:17dUlkBOakJ0+DkrSSNjCkIjxS6bF9zb3elmeNGIjoY=
-golang.org/x/mod v0.36.0 h1:JJjpVx6myfUsUdAzZuOSTTmRE0PfZeNWzzvKrP7amb4=
-golang.org/x/mod v0.36.0/go.mod h1:moc6ELqsWcOw5Ef3xVprK5ul/MvtVvkIXLziUOICjUQ=
-golang.org/x/sync v0.20.0 h1:e0PTpb7pjO8GAtTs2dQ6jYa5BWYlMuX047Dco/pItO4=
-golang.org/x/sync v0.20.0/go.mod h1:9xrNwdLfx4jkKbNva9FpL6vEN7evnE43NNNJQ2LF3+0=
-golang.org/x/tools v0.45.0 h1:18qN3FAooORvApf5XjCXgsuayZOEtXf6JK18I3+ONa8=
-golang.org/x/tools v0.45.0/go.mod h1:LuUGqqaXcXMEFEruIVJVm5mgDD8vww/z/SR1gQ4uE/0=
diff --git a/go.mod b/go.mod
index 1b5c7e56..23410770 100644
--- a/go.mod
+++ b/go.mod
@@ -3,3 +3 @@ module github.com/vcp-vsa-control-Plane/vsa-control-plane
-go 1.24.11
-
-toolchain go1.24.12
+go 1.25.0
@@ -37 +35 @@ require (
-	go.opentelemetry.io/otel v1.38.0
+	go.opentelemetry.io/otel v1.42.0
@@ -39,4 +37,4 @@ require (
-	go.opentelemetry.io/otel/metric v1.38.0
-	go.opentelemetry.io/otel/sdk v1.38.0
-	go.opentelemetry.io/otel/sdk/metric v1.38.0
-	go.opentelemetry.io/otel/trace v1.38.0
+	go.opentelemetry.io/otel/metric v1.42.0
+	go.opentelemetry.io/otel/sdk v1.42.0
+	go.opentelemetry.io/otel/sdk/metric v1.42.0
+	go.opentelemetry.io/otel/trace v1.42.0
@@ -169 +167 @@ require (
-	go.opentelemetry.io/auto/sdk v1.1.0 // indirect
+	go.opentelemetry.io/auto/sdk v1.2.1 // indirect
@@ -175 +173 @@ require (
-	golang.org/x/sys v0.37.0 // indirect
+	golang.org/x/sys v0.41.0 // indirect
diff --git a/go.sum b/go.sum
index c59c8570..db6acb78 100644
--- a/go.sum
+++ b/go.sum
@@ -298,2 +298,2 @@ github.com/robfig/cron v1.2.0/go.mod h1:JGuDeoQd7Z6yL4zQhZ3OPEVHB7fL6Ka6skscFHfm
-github.com/rogpeppe/go-internal v1.13.1 h1:KvO1DLK/DRN07sQ1LQKScxyZJuNnedQ5/wKSR38lUII=
-github.com/rogpeppe/go-internal v1.13.1/go.mod h1:uMEvuHeurkdAXX61udpOXGD/AzZDWNMNyH2VO9fmH0o=
+github.com/rogpeppe/go-internal v1.14.1 h1:UQB4HGPB6osV0SQTLymcB4TgvyWu6ZyliaW0tI/otEQ=
+github.com/rogpeppe/go-internal v1.14.1/go.mod h1:MaRKkUm5W0goXpeCfT7UZI6fk/L7L7so1lCWt35ZSgc=
@@ -336,2 +336,2 @@ go.mongodb.org/mongo-driver v1.17.7/go.mod h1:Hy04i7O2kC4RS06ZrhPRqj/u4DTYkFDAAc
-go.opentelemetry.io/auto/sdk v1.1.0 h1:cH53jehLUN6UFLY71z+NDOiNJqDdPRaXzTel0sJySYA=
-go.opentelemetry.io/auto/sdk v1.1.0/go.mod h1:3wSPjt5PWp2RhlCcmmOial7AvC4DQqZb7a7wCow3W8A=
+go.opentelemetry.io/auto/sdk v1.2.1 h1:jXsnJ4Lmnqd11kwkBV2LgLoFMZKizbCi5fNZ/ipaZ64=
+go.opentelemetry.io/auto/sdk v1.2.1/go.mod h1:KRTj+aOaElaLi+wW1kO/DZRXwkF4C5xPbEe3ZiIhN7Y=
@@ -344,2 +344,2 @@ go.opentelemetry.io/contrib/instrumentation/net/http/otelhttp v0.61.0/go.mod h1:
-go.opentelemetry.io/otel v1.38.0 h1:RkfdswUDRimDg0m2Az18RKOsnI8UDzppJAtj01/Ymk8=
-go.opentelemetry.io/otel v1.38.0/go.mod h1:zcmtmQ1+YmQM9wrNsTGV/q/uyusom3P8RxwExxkZhjM=
+go.opentelemetry.io/otel v1.42.0 h1:lSQGzTgVR3+sgJDAU/7/ZMjN9Z+vUip7leaqBKy4sho=
+go.opentelemetry.io/otel v1.42.0/go.mod h1:lJNsdRMxCUIWuMlVJWzecSMuNjE7dOYyWlqOXWkdqCc=
@@ -350,8 +350,8 @@ go.opentelemetry.io/otel/exporters/stdout/stdoutmetric v1.35.0/go.mod h1:U2R3XyV
-go.opentelemetry.io/otel/metric v1.38.0 h1:Kl6lzIYGAh5M159u9NgiRkmoMKjvbsKtYRwgfrA6WpA=
-go.opentelemetry.io/otel/metric v1.38.0/go.mod h1:kB5n/QoRM8YwmUahxvI3bO34eVtQf2i4utNVLr9gEmI=
-go.opentelemetry.io/otel/sdk v1.38.0 h1:l48sr5YbNf2hpCUj/FoGhW9yDkl+Ma+LrVl8qaM5b+E=
-go.opentelemetry.io/otel/sdk v1.38.0/go.mod h1:ghmNdGlVemJI3+ZB5iDEuk4bWA3GkTpW+DOoZMYBVVg=
-go.opentelemetry.io/otel/sdk/metric v1.38.0 h1:aSH66iL0aZqo//xXzQLYozmWrXxyFkBJ6qT5wthqPoM=
-go.opentelemetry.io/otel/sdk/metric v1.38.0/go.mod h1:dg9PBnW9XdQ1Hd6ZnRz689CbtrUp0wMMs9iPcgT9EZA=
-go.opentelemetry.io/otel/trace v1.38.0 h1:Fxk5bKrDZJUH+AMyyIXGcFAPah0oRcT+LuNtJrmcNLE=
-go.opentelemetry.io/otel/trace v1.38.0/go.mod h1:j1P9ivuFsTceSWe1oY+EeW3sc+Pp42sO++GHkg4wwhs=
+go.opentelemetry.io/otel/metric v1.42.0 h1:2jXG+3oZLNXEPfNmnpxKDeZsFI5o4J+nz6xUlaFdF/4=
+go.opentelemetry.io/otel/metric v1.42.0/go.mod h1:RlUN/7vTU7Ao/diDkEpQpnz3/92J9ko05BIwxYa2SSI=
+go.opentelemetry.io/otel/sdk v1.42.0 h1:LyC8+jqk6UJwdrI/8VydAq/hvkFKNHZVIWuslJXYsDo=
+go.opentelemetry.io/otel/sdk v1.42.0/go.mod h1:rGHCAxd9DAph0joO4W6OPwxjNTYWghRWmkHuGbayMts=
+go.opentelemetry.io/otel/sdk/metric v1.42.0 h1:D/1QR46Clz6ajyZ3G8SgNlTJKBdGp84q9RKCAZ3YGuA=
+go.opentelemetry.io/otel/sdk/metric v1.42.0/go.mod h1:Ua6AAlDKdZ7tdvaQKfSmnFTdHx37+J4ba8MwVCYM5hc=
+go.opentelemetry.io/otel/trace v1.42.0 h1:OUCgIPt+mzOnaUTpOQcBiM/PLQ/Op7oq6g4LenLmOYY=
+go.opentelemetry.io/otel/trace v1.42.0/go.mod h1:f3K9S+IFqnumBkKhRJMeaZeNk9epyhnCmQh/EysQCdc=
@@ -425,2 +425,2 @@ golang.org/x/sys v0.6.0/go.mod h1:oPkhp1MJrh7nUepCBck5+mAzfO9JrbApNNgaTdGDITg=
-golang.org/x/sys v0.37.0 h1:fdNQudmxPjkdUTPnLn5mdQv7Zwvbvpaxqs831goi9kQ=
-golang.org/x/sys v0.37.0/go.mod h1:OgkHotnGiDImocRcuBABYBEXf8A9a87e/uXjp9XT3ks=
+golang.org/x/sys v0.41.0 h1:Ivj+2Cp/ylzLiEU89QhWblYnOE9zerudt9Ftecq2C6k=
+golang.org/x/sys v0.41.0/go.mod h1:OgkHotnGiDImocRcuBABYBEXf8A9a87e/uXjp9XT3ks=
```
</details>
<details><summary>🖥️ Build output (last lines)</summary>

```
  targeted build (root module): 2 dirs
    dirs: ./ontap-proxy/middleware/... ./utils/middleware/log/...
```
</details>
<details><summary>🧪 Test output (last lines)</summary>

```
    testing . module: 2 dirs — ./ontap-proxy/middleware/... ./utils/middleware/log/...
# github.com/vcp-vsa-control-Plane/vsa-control-plane/ontap-proxy/middleware.test
ld: warning: '/private/var/folders/hd/384cc81x08v0f_q5zl7h6yf40000gp/T/go-link-4277550790/000013.o' has malformed LC_DYSYMTAB, expected 98 undefined symbols to start at index 1626, found 95 undefined symbols starting at index 1626
# github.com/vcp-vsa-control-Plane/vsa-control-plane/utils/middleware/log.test
ld: warning: '/private/var/folders/hd/384cc81x08v0f_q5zl7h6yf40000gp/T/go-link-814249040/000013.o' has malformed LC_DYSYMTAB, expected 98 undefined symbols to start at index 1626, found 95 undefined symbols starting at index 1626
ok  	github.com/vcp-vsa-control-Plane/vsa-control-plane/ontap-proxy/middleware	3.403s
ok  	github.com/vcp-vsa-control-Plane/vsa-control-plane/utils/middleware/log	1.898s
```
</details>

### Changelog signals
Source: deterministic changelog analysis (same source as the verdict)
- Improve error handling for dropped data during translation by using `prometheus.NewInvalidMetric` in `go.opentelemetry.io/otel/exporters/prometheus`. ⚠️ **Breaking Change:** Previously, these cases were only logged and s
- `Distinct` in `go.opentelemetry.io/otel/attribute` is no longer guaranteed to uniquely identify an attribute set. Collisions between `Distinct` values for different Sets are possible with extremely high cardinality (bill
- `Distinct` in `go.opentelemetry.io/otel/attribute` is no longer guaranteed to uniquely identify an attribute set.

### API diff signal
- ⚠️ Go apidiff was unavailable; structural fallback ran instead.
- Reason: apidiff module snapshots unavailable; used structural go doc fallback (golang.org/x/exp/cmd/apidiff@v0.0.0-20260529124908-c761662dc8c9)
- Coverage note: fallback is evidence, but may miss subpackage/type-compatibility breaks that module-mode apidiff would catch.
> 🔬 **Advisory mode** — This analysis is informational. No merges are blocked.

🔗 [View analysis run](https://github.com/CSC-Security-sandbox/vcp-vsa-breakability-test/actions/runs/27190822695)
> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*
</details>
