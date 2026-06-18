🟢 Breakability: None · safe to merge · Oracle confidence: not available · Priority: P3

### Signals checked
| Signal | Result |
|---|---|
| Resolve | ✅ checked-clean |
| Build | ✅ checked-clean |
| Test | · not run (no test suite or tests not executed) |
| API diff | ✅ checked-clean |
| Usage | · not observed |
| Vulnerability | – n/a |
| Changelog | ⚪ not available |

<!-- breakability-check -->

<details><summary>Internal merge-risk detail</summary>

## 🔍 BUILD ANALYSIS — `golang.org/x/oauth2` 0.32.0 → 0.36.0 · production · major ⚠️ (0.x unstable — treat as breaking)

**Merge Risk: Medium** (Evidence: changelog unavailable × Build verification: L2 × Oracle confidence: not available) — missing or unparsable changelog; default caution

Build: ✅ passes · Verification: **L2_type_checked** · Usage: 1 file(s) · Context: 5 prod files, 3 test files, 1 CI/CD file · Module: `automations/tstctl`

### Summary (deterministic analysis)
- Package: `golang.org/x/oauth2` 0.32.0 → 0.36.0 (major ⚠️ (0.x unstable — treat as breaking) bump)
- Type: production / direct
- Build passes on PR branch
- New type errors: 0

**Recommendation:** Review changelog for major ⚠️ (0.x unstable — treat as breaking) bump breaking changes. Build passes — merge when ready.
📋 Merge plan: #LOCAL
<details><summary>🔍 How we checked (verification: L2_type_checked)</summary>

- ✅ Dependency resolved — `go mod tidy (affected modules) for golang.org/x/oauth2 0.32.0→0.36.0` — go.mod/go.sum: 2 changed, 0 new, 14 removed: go 1.24.1→1.25.0; golang.org/x/oauth2 v0.32.0/go.mod→v0.36.0/go.mod; golang.org/x/tools v0.45.0; golang.org/x/mod v0.36.0; golang.org/x/sync v0.20.0; github.com/google/go-cmp v0.6.0
- ✅ Build passes — `targeted build (automations/tstctl module): 1 dirs` — exit 0, 0 new error(s)
- ⬜ Tests not configured or not run
- ✅ Diffed error output: PR introduces 0 new diagnostics
- ℹ️ govulncheck: disabled by config — CVE list sourced from Dependabot alerts (govulncheck is hint-only; not a merge gate)
</details>

### BREAK-reachability context
- No exported API symbols changed per apidiff; reachability is import-level only (see imported files below).
<details><summary>📂 Files importing this package (1 file(s))</summary>

- `automations/tstctl/common/git.go`
</details>
<details><summary>📦 Go dependency-resolution output</summary>

```
--- go mod tidy: automations/tstctl (exit=0) ---
```
</details>
<details><summary>🧾 go.mod / go.sum diff</summary>

```diff
diff --git a/automations/tstctl/go.mod b/automations/tstctl/go.mod
index d7680825..ee0610da 100644
--- a/automations/tstctl/go.mod
+++ b/automations/tstctl/go.mod
@@ -3 +3 @@ module github.com/tstctl
-go 1.24.1
+go 1.25.0
@@ -7 +6,0 @@ require (
-	github.com/lib/pq v1.11.2
@@ -10 +9 @@ require (
-	golang.org/x/oauth2 v0.32.0
+	golang.org/x/oauth2 v0.36.0
diff --git a/automations/tstctl/go.sum b/automations/tstctl/go.sum
index a978f9cf..2be5f9fe 100644
--- a/automations/tstctl/go.sum
+++ b/automations/tstctl/go.sum
@@ -14,2 +13,0 @@ github.com/inconshreveable/mousetrap v1.1.0/go.mod h1:vpF70FUmC8bwa3OWnCshd2FqLf
-github.com/lib/pq v1.11.2 h1:x6gxUeu39V0BHZiugWe8LXZYZ+Utk7hSJGThs8sdzfs=
-github.com/lib/pq v1.11.2/go.mod h1:/p+8NSbOcwzAEI7wiMXFlgydTwcgTr3OSKMsD2BitpA=
@@ -29,2 +27,2 @@ github.com/stretchr/testify v1.7.0/go.mod h1:6Fq8oRcR53rry900zMqJjRRixrwX3KX962/
-golang.org/x/oauth2 v0.32.0 h1:jsCblLleRMDrxMN29H3z/k1KliIvpLgCkE6R8FXXNgY=
-golang.org/x/oauth2 v0.32.0/go.mod h1:lzm5WQJQwKZ3nwavOZ3IS5Aulzxi68dUSgRHujetwEA=
+golang.org/x/oauth2 v0.36.0 h1:peZ/1z27fi9hUOFCAZaHyrpWG5lwe0RJEEEeH0ThlIs=
+golang.org/x/oauth2 v0.36.0/go.mod h1:YDBUJMTkDnJS+A4BP4eZBjCqtokkg1hODuPjwiGPO7Q=
```
</details>
<details><summary>🖥️ Build output (last lines)</summary>

```
  targeted build (automations/tstctl module): 1 dirs
    dirs: ./common/...
```
</details>

### Changelog signals
Source: deterministic changelog analysis (same source as the verdict)
- No breaking-change markers found in the analyzed changelog.

### API diff signal
- ✅ Go apidiff ran in **module mode** using `golang.org/x/exp/cmd/apidiff@v0.0.0-20260529124908-c761662dc8c9`
- Command: `go run golang.org/x/exp/cmd/apidiff@v0.0.0-20260529124908-c761662dc8c9 -m /var/folders/hd/384cc81x08v0f_q5zl7h6yf40000gp/T/bc-go-api-0d7e5929ca707eaa.api /var/folders/hd/384cc81x08v0f_q5zl7h6yf40000gp/T/bc-go-api-e5e8de43daa2a928.api`
> 🔬 **Advisory mode** — This analysis is informational. No merges are blocked.

> 🔬 *Deterministic analysis — based on build comparison of main vs PR branch*
</details>
