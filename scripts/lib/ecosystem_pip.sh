#!/usr/bin/env bash
# Pip ecosystem: usage scanning.
# Sourced by build-check.sh — do not run directly.

scan_usage_pip() {
  local pkg="$1"
  local import_name
  import_name=$(map_import_name "$pkg")
  grep -rn "from ${import_name} import\\|import ${import_name}" \
    --include="*.py" . 2>/dev/null | head -50 || true
}
