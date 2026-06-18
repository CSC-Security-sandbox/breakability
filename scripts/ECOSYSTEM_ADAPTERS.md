# Multi-Ecosystem Adapter Framework

## Overview

The **Multi-Ecosystem Adapter Framework** de-couples the Go MVP (Minimum Viable Product) from hardcoded VSA/Go assumptions by defining lightweight, declarative interfaces for ecosystems (Go, npm, pip, etc.).

**Design principle**: Adapters are stateless and declarative. Each ecosystem declares what it can do, and the framework fails safely (ABSTAIN) on unknown ecosystems or unsupported capabilities.

### Why This Matters

- **Before**: Go MVP hardcoded build/test/install commands specific to Go only
- **After**: Any ecosystem can plug in by declaring its capabilities through a simple adapter interface
- **Node.js and Python**: Can implement full adapters later without changing Go code

## Architecture

### Components

1. **Python Module** (`ecosystem_adapters.py`)
   - Defines data classes for adapters and capabilities
   - Provides registry for adapter lookup and capability resolution
   - Includes built-in Go adapter (MVP) and npm/pip placeholders
   - ~300 lines, stdlib-only, testable in isolation

2. **Go Package** (`common/ecosystems.go`)
   - Mirrors Python types for Go consumers
   - Provides registry API for runtime capability resolution
   - Enables Go code to query "what can this ecosystem do?"
   - Full JSON marshaling for adapter metadata export

3. **Tests**
   - Python: 34 test cases covering all adapter operations
   - Go: 17 test cases with identical coverage
   - Tests verify: resolver, capability lookup, fail-closed behavior

## Adapter Declaration

An ecosystem declares capabilities through a simple data structure:

```python
EcosystemAdapter(
    name="go",                    # ecosystem identifier
    display_name="Go",            # display-friendly name
    package_manager="go mod",     # package manager name
    capabilities=[                # what this ecosystem can do
        EcosystemCapability(
            capability=CapabilityType.BUILD,
            supported=True,
            commands=[CommandSpec(...)]  # how to do it
        ),
        EcosystemCapability(
            capability=CapabilityType.API_DIFF,
            supported=False,
            reason="Not applicable for Go"  # why we can't
        )
    ],
    file_patterns=["go.mod", "go.sum"]  # how to detect this ecosystem
)
```

### Capabilities

Each capability represents a distinct operation the framework needs:

| Capability | Example Use | Go MVP | npm | pip |
|------------|-------------|-------|-----|-----|
| **INSTALL** | Download dependencies | ✓ | ✗ | ✗ |
| **BUILD** | Compile/transpile code | ✓ | ✗ | ✗ |
| **TEST** | Run test suite | ✓ | ✗ | ✗ |
| **VET** | Static analysis/linting | ✓ | ✗ | ✗ |
| **API_DIFF** | API signature comparison | ✗ (framework-level) | ✗ | ✗ |
| **RELEASE_NOTE** | Breaking change detection | ✗ (framework-level) | ✗ | ✗ |

**✓ = Implemented (MVP)**  
**✗ = Not yet implemented (ABSTAIN at runtime)**

## How It Works

### Registry Pattern

```python
# Python
registry = get_default_registry()
adapter = registry.get("npm")
if not adapter:
    # Unknown ecosystem — fail safely
    pass

# Get commands for a capability
cmds = registry.get_commands("go", CapabilityType.BUILD)
# Returns: [CommandSpec(cmd="go", args=["build", "-o", "/dev/null", "./..."], ...)]

# Query capability support
if adapter.has_capability(CapabilityType.TEST):
    # Can run tests
    pass
```

```go
// Go
registry := GetDefaultRegistry()
adapter, err := registry.GetOrFail("go")
if err != nil {
    // Unknown ecosystem — error on explicit lookup
    // But GetCommands() fails closed without crashing
}

// Get commands for a capability
cmds := registry.GetCommands("go", CapabilityBuild)
// Returns: []CommandSpec with "go build" details

// Query capability support
if adapter.HasCapability(CapabilityBuild) {
    // Can run build
}
```

### Fail-Closed Behavior

**Unknown ecosystems or unsupported capabilities never crash:**

```python
# Unknown ecosystem
cmds = registry.get_commands("rust", CapabilityType.BUILD)
# Returns: [] (empty, no crash)

# Unsupported capability
cmds = registry.get_commands("go", CapabilityType.API_DIFF)
# Returns: [] (empty, Go doesn't support API diff)

# Explicit lookup fails on unknown
try:
    adapter = registry.get_or_fail("unknown")
except UnknownEcosystem:
    # Framework decides what to do
    pass
```

## Integration Points

### Go MVP Usage (Today)

```go
// In Go CLI or build system
registry := GetDefaultRegistry()

// Resolve what to run
go_adapter, _ := registry.GetOrFail("go")
buildCmds := registry.GetCommands("go", CapabilityBuild)

// Extract and run the command
for _, cmd := range buildCmds {
    // cmd.Cmd = "go"
    // cmd.Args = ["build", "-o", "/dev/null", "./..."]
    // cmd.TimeoutSec = 300
    // Execute the command
}
```

### How Node.js Plugs In (Later)

```python
# Implement npm adapter (not yet done)
npm_adapter = EcosystemAdapter(
    name="npm",
    display_name="npm",
    package_manager="npm",
    capabilities=[
        EcosystemCapability(
            capability=CapabilityType.INSTALL,
            supported=True,
            commands=[CommandSpec(cmd="npm", args=["ci", "--ignore-scripts"])]
        ),
        EcosystemCapability(
            capability=CapabilityType.BUILD,
            supported=True,
            commands=[CommandSpec(cmd="npm", args=["run", "build"])]
        ),
        # ... more capabilities
    ]
)

registry.register(npm_adapter)

# Go code automatically picks it up via registry
# No changes to Go code needed!
```

## Data Flow

1. **Detection**: Framework detects ecosystem (e.g., looks for `go.mod`, `package.json`)
2. **Adapter Lookup**: `registry.get("go")` returns the Go adapter
3. **Capability Query**: `registry.get_commands("go", BUILD)` returns build command spec
4. **Execution**: Framework runs the command with specified args/env/timeout
5. **Result**: Capture exit code, stdout, stderr for evidence

## Design Decisions

### Why Declarative?

- Adapters are **data**, not code
- Easy to serialize/export as JSON for introspection
- Framework can understand capabilities without executing adapters
- No side effects in adapter definitions

### Why Fail Closed?

- Unknown ecosystems return empty results (not errors, unless explicitly queried)
- This allows the framework to ABSTAIN from signals gracefully
- Evidence contract already has `SignalStatus.UNAVAILABLE` for this

### Why Stateless?

- Adapters don't hold state or connections
- Registry can be cloned/forked for testing
- Thread-safe by default

## Testing Strategy

### Python Tests
- Unit tests for all data classes
- Registry tests for lookup and resolution
- Fail-closed behavior tests
- Round-trip JSON serialization

### Go Tests
- Mirror Python test scenarios
- Type system validation
- Error handling verification
- Default registry initialization

### Coverage
- **34 Python tests**: CommandSpec, Capability, Adapter, Registry, fail-closed behavior
- **17 Go tests**: Equivalent scenarios, JSON marshaling, default registry

## File Structure

```
.github/scripts/
├── ecosystem_adapters.py          # Python adapter definitions
├── test_ecosystem_adapters.py     # Python tests (34 test cases)
├── evidence_contract.py           # Evidence typing (unmodified)
└── ...other scripts...

common/
├── ecosystems.go                  # Go adapter definitions
├── ecosystems_test.go             # Go tests (17 test cases)
└── ...other common code...
```

## Next Steps: Implementing Node.js Adapter

To add npm/Node.js support:

1. **Update `ecosystem_adapters.py`**:
   - Replace npm placeholder with real `EcosystemCapability` entries
   - Add actual CommandSpec entries (e.g., `npm ci`, `npm run build`, `npm test`)
   - Handle monorepo specifics (workspaces)

2. **Update `common/ecosystems.go`**:
   - Mirror Python changes (already will happen automatically via JSON)

3. **No Go changes needed**:
   - Go code already queries registry dynamically
   - Framework automatically picks up npm capabilities

4. **Tests**:
   - Add npm tests to verify command resolution
   - Integration tests with actual npm projects

## Validation

- [x] Python module passes all 34 tests
- [x] Go package passes all 17 tests
- [x] Go adapter resolves build commands for Go
- [x] npm adapter declared but unsupported (ABSTAIN)
- [x] Unknown ecosystems fail gracefully
- [x] Registry JSON export/import works
