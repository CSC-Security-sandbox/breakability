#!/usr/bin/env bash
# Config parsing: breakability YAML config, private registries, infra patterns.
# Sourced by build-check.sh — do not run directly.

# Parse the breakability config and cache the parsed JSON in a temp file
# so we don't re-parse YAML for every call. Uses PyYAML if available, falls
# back to a simple regex parser for the subset of YAML we need.
_parse_bc_config() {
  local cache="/tmp/_bc_config_parsed.json"
  if [[ -f "$cache" ]]; then
    cat "$cache"
    return 0
  fi
  [[ -f "$BC_CONFIG" ]] || { echo "{}"; return 0; }

  python3 << 'PARSECFG' > "$cache" 2>/dev/null
import json, os, re

config_path = os.environ.get("BC_CONFIG", "")
if not config_path or not os.path.isfile(config_path):
    print("{}")
    exit()

with open(config_path) as f:
    raw = f.read()

# Try PyYAML first
try:
    import yaml
    config = yaml.safe_load(raw) or {}
    print(json.dumps(config))
    exit()
except ImportError:
    pass

# Fallback: simple parser for our known config structure
config = {}

# Parse private_registries list
registries = []
in_registries = False
current = {}
for line in raw.split("\n"):
    stripped = line.strip()
    if stripped.startswith("#") or not stripped:
        continue
    if stripped.startswith("private_registries:"):
        in_registries = True
        val = stripped.split(":", 1)[1].strip()
        if val == "[]":
            registries = []
            in_registries = False
        continue
    if in_registries:
        if stripped.startswith("- "):
            if current:
                registries.append(current)
            current = {}
            # Parse "- key: value"
            kv = stripped[2:].strip()
            m = re.match(r'(\w+):\s*["\']?([^"\']+?)["\']?\s*$', kv)
            if m:
                current[m.group(1)] = m.group(2)
        elif stripped.startswith(("scope:", "registry:", "auth_token_env:")):
            m = re.match(r'(\w+):\s*["\']?([^"\']+?)["\']?\s*$', stripped)
            if m:
                current[m.group(1)] = m.group(2)
        elif not stripped.startswith((" ", "\t", "-")):
            in_registries = False
            if current:
                registries.append(current)
                current = {}
if current:
    registries.append(current)

config["private_registries"] = registries

# Parse extra_infra_patterns list
patterns = []
in_patterns = False
for line in raw.split("\n"):
    stripped = line.strip()
    if stripped.startswith("#") or not stripped:
        continue
    if stripped.startswith("extra_infra_patterns:"):
        in_patterns = True
        val = stripped.split(":", 1)[1].strip()
        if val == "[]":
            patterns = []
            in_patterns = False
        continue
    if in_patterns:
        if stripped.startswith("- "):
            val = stripped[2:].strip().strip("\"'")
            if val:
                patterns.append(val)
        elif not stripped.startswith((" ", "\t")):
            in_patterns = False

config["extra_infra_patterns"] = patterns

# Parse mode (advisory | enforce)
mode_match = re.search(r'^mode:\s*["\']?(\w+)["\']?', raw, re.MULTILINE)
config["mode"] = mode_match.group(1) if mode_match else "advisory"

print(json.dumps(config))
PARSECFG
  cat "$cache"
}

# Set up .npmrc with private registry auth in a given directory
setup_private_registries() {
  local target_dir="$1"
  local config_json
  config_json=$(_parse_bc_config)

  [[ "$config_json" == "{}" ]] && return 0

  _BC_CONFIG_JSON="$config_json" _BC_TARGET_DIR="$target_dir" python3 << 'SETUPREG'
import json, os, sys

config = json.loads(os.environ.get('_BC_CONFIG_JSON', '{}'))
target_dir = os.environ.get('_BC_TARGET_DIR', '.')

registries = config.get("private_registries", [])
if not registries:
    sys.exit(0)

npmrc_path = os.path.join(target_dir, ".npmrc")

# Read existing .npmrc (preserve non-registry lines)
existing_lines = []
if os.path.isfile(npmrc_path):
    with open(npmrc_path) as f:
        existing_lines = f.readlines()

# Build set of scopes/hosts we'll configure
scopes = {r.get("scope","") for r in registries if r.get("scope")}

# Filter out old lines for scopes we're replacing
filtered = []
for line in existing_lines:
    s = line.strip()
    skip = False
    for reg in registries:
        scope = reg.get("scope","")
        registry_url = reg.get("registry","")
        if scope and s.startswith(f"{scope}:registry="):
            skip = True; break
        if registry_url and "//" in registry_url:
            host = registry_url.split("//",1)[1]
            if s.startswith(f"//{host}"):
                skip = True; break
    if s.startswith("//") and ":_authToken=" in s:
        # Check if this is for one of our registries
        for reg in registries:
            rurl = reg.get("registry","")
            if rurl and "//" in rurl and rurl.split("//",1)[1].rstrip("/") in s:
                skip = True; break
    if not skip:
        filtered.append(line)

# Generate new .npmrc lines
new_lines = []
configured = 0
for reg in registries:
    scope = reg.get("scope","")
    registry_url = reg.get("registry","")
    auth_env = reg.get("auth_token_env","")

    if not scope or not registry_url:
        print(f"  [registry] SKIP: missing scope or registry in config")
        continue

    token = os.environ.get(auth_env, "")
    if not token:
        print(f"  [registry] WARNING: {auth_env} not set — {scope} may fail to install")
        new_lines.append(f"{scope}:registry={registry_url}\n")
        continue

    host_part = registry_url.split("//",1)[1] if "//" in registry_url else registry_url
    if not host_part.endswith("/"):
        host_part += "/"

    new_lines.append(f"{scope}:registry={registry_url}\n")
    new_lines.append(f"//{host_part}:_authToken={token}\n")
    new_lines.append(f"//{host_part}:always-auth=true\n")
    configured += 1
    print(f"  [registry] {scope} -> {registry_url} (auth: {auth_env})")

if new_lines:
    with open(npmrc_path, "w") as f:
        f.writelines(filtered)
        f.write("\n# -- breakability-check: private registry auth --\n")
        f.writelines(new_lines)
    print(f"  [registry] .npmrc updated: {configured} registry(ies) configured")

SETUPREG

  # Check if any registries are configured
  if echo "$config_json" | python3 -c "
import json, sys
c = json.load(sys.stdin)
sys.exit(0 if c.get('private_registries') else 1)
" 2>/dev/null; then
    PRIVATE_REGISTRY_CONFIGURED=true
    echo "  [registry] Private registry support: ENABLED"
  fi

  # ── Go private module support (GOPRIVATE + netrc) ──────────────
  # Reads go_private_modules from config and sets GOPRIVATE + ~/.netrc
  local go_private
  go_private=$(echo "$config_json" | python3 -c "
import json, sys, os
c = json.load(sys.stdin)
modules = c.get('go_private_modules', [])
if not modules:
    sys.exit(0)
goprivate = []
netrc_lines = []
for m in modules:
    pattern = m.get('pattern', '')
    if pattern:
        goprivate.append(pattern)
    host = m.get('host', '')
    auth_env = m.get('auth_token_env', '')
    if host and auth_env:
        token = os.environ.get(auth_env, '')
        if token:
            netrc_lines.append(f'machine {host}')
            netrc_lines.append(f'login token')
            netrc_lines.append(f'password {token}')
if goprivate:
    print('GOPRIVATE=' + ','.join(goprivate))
if netrc_lines:
    import pathlib
    netrc_path = pathlib.Path.home() / '.netrc'
    with open(netrc_path, 'a') as f:
        f.write('\n'.join(netrc_lines) + '\n')
    netrc_path.chmod(0o600)
    print(f'NETRC={len(netrc_lines)//3} entries')
" 2>/dev/null) || true
  if [[ -n "$go_private" ]]; then
    local gp_val="${go_private#GOPRIVATE=}"
    gp_val="${gp_val%%$'\n'*}"
    if [[ -n "$gp_val" && "$gp_val" != "GOPRIVATE=" ]]; then
      export GOPRIVATE="$gp_val"
      export GONOSUMDB="$gp_val"
      echo "  [registry] Go: GOPRIVATE=$GOPRIVATE"
    fi
    [[ "$go_private" == *"NETRC="* ]] && echo "  [registry] Go: ~/.netrc configured"
  fi
}

load_extra_infra_patterns() {
  # Load project-specific infra error patterns from config
  local config_json
  config_json=$(_parse_bc_config)
  [[ "$config_json" == "{}" ]] && return 0
  echo "$config_json" | python3 -c "
import json, sys
config = json.load(sys.stdin)
for p in config.get('extra_infra_patterns', []):
    print(p)
" 2>/dev/null
}
