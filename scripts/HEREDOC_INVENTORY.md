# Inline Python Heredoc Inventory

Status of Python code embedded in bash scripts. Goal: extract all to standalone `.py` files.

## build-check.sh

| # | Lines | Size | Marker | Quoted? | Description | Status |
|---|-------|------|--------|---------|-------------|--------|
| 1 | 144-165 | 20 | `<<'PY'` | Yes | PR filter subset writer | Inline (small) |
| 2 | 430-466 | 35 | `<< PYEOF` | No | Main build baseline JSON writer | Inline (small) |
| 3 | ~~477-503~~ | 25 | `<< 'PEERDEPS_SCRIPT'` | Yes | Peer dep group discovery | **Extracted** → `discover_peer_groups.py` |
| 4 | ~~590-646~~ | 55 | `<< SKIPEOF` | No | Skip entry writer | **Extracted** → `write_skip_entry.py` |
| 5 | 2161-3219 | **1058** | `<< PYEOF` | No | Per-PR result writer (the big one) | Deferred |
| 6 | ~~3312-3330~~ | 17 | `<<'BATCHVULN'` | Yes | Batch vuln summary | **Extracted** → `batch_vuln_summary.py` |
| 7 | ~~3349-3552~~ | 202 | `<< SECURITYEOF` | No | Security posture scan | **Extracted** → `security_posture_scan.py` |

Plus 8 inline `python3 -c` blocks (12-69 lines each, ~244 lines total) — too small/tightly-coupled for extraction.

## post-fallback-comments.sh

| # | Lines | Size | Marker | Quoted? | Description | Status |
|---|-------|------|--------|---------|-------------|--------|
| 1 | 202-249 | 46 | `<<'PYEOF'` | Yes | AI adjudication re-assert | Inline |
| 2 | 251-461 | 209 | `<<'PYEOF'` | Yes | Policy lowering overlay | Inline |
| 3 | 954-1036 | 81 | `<<'PYEOF'` | Yes | BREAK-reachability usage context | Inline |
| 4 | 1048-1143 | 94 | `<<'PYEOF'` | Yes | Declared-break reachability proof | Inline |
| 5 | 1148-1152 | 3 | `<<'PYEOF'` | Yes | Import-reachable flag check | Inline (tiny) |
| 6 | 1313-1326 | 12 | `<<'PYEOF'` | Yes | No-test confidence block | Inline (small) |
| 7 | 1329-1346 | 16 | `<<'PYEOF'` | Yes | API diff tool signal block | Inline (small) |
| 8 | 1981-2045 | 63 | `<<'PYEOF'` | Yes | CVE/vuln reachability detail | Inline |
| 9 | 2221-2443 | 221 | `<<'PYEOF'` | Yes | PR comment builder | Inline |
| 10 | 2824-3976 | **1151** | `<< 'PYEOF'` | Yes | Merge plan generator (the big one) | Deferred |
| 11 | 4000-4017 | 16 | `<< 'PYEOF'` | Yes | Merge plan truncation | Inline (small) |

Plus 21 inline `python3 -c` blocks (5-66 lines each, ~437 lines total).

## Summary

| | Extracted | Remaining heredocs | Remaining -c blocks | Total remaining Python lines |
|---|---|---|---|---|
| build-check.sh | 5 scripts | 2 heredocs (1078 lines) | 8 blocks (~244 lines) | ~1,322 |
| post-fallback-comments.sh | 0 scripts | 11 heredocs (~1,912 lines) | 21 blocks (~437 lines) | ~2,349 |
| **Total** | **5 scripts** | **13 heredocs** | **29 blocks** | **~3,671** |

The two biggest remaining heredocs (1,058 + 1,151 lines) are the highest-value targets
but also the most complex due to many bash variable dependencies.
