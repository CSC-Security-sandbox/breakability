#!/usr/bin/env python3
"""Lightweight ecosystem adapters for multi-language breakability analysis.

This module defines interfaces and implementations for ecosystems (Go, npm, pip, etc.)
to plug into the breakability pipeline. Adapters declare their capabilities (install,
build, test, api-diff, release-note detection) and the runner uses these to orchestrate
ecosystem-specific commands.

Design: Adapters are stateless and declarative. Each ecosystem declares what it can do,
and the framework fails safely (ABSTAIN) on unknown ecosystems or unsupported capabilities.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple


class EcosystemError(Exception):
    """Base exception for ecosystem adapter errors."""


class CapabilityNotSupported(EcosystemError):
    """Raised when an ecosystem does not support a capability."""


class UnknownEcosystem(EcosystemError):
    """Raised when ecosystem is not registered."""


class CapabilityType(str, Enum):
    """Capabilities that an ecosystem can declare support for."""

    INSTALL = "install"  # Package manager install (npm ci, go mod download, pip install)
    BUILD = "build"  # Language build (go build, tsc, etc.)
    TEST = "test"  # Language test (go test, jest, pytest, etc.)
    API_DIFF = "api_diff"  # API signature comparison
    RELEASE_NOTE = "release_note"  # Release note/breaking change detection
    VET = "vet"  # Linting/static analysis (go vet, eslint, pylint, etc.)


@dataclass(frozen=True)
class CommandSpec:
    """Specification for running a command in an ecosystem."""

    cmd: str  # Command to run (e.g., "go build ./...", "npm ci", "pytest")
    args: Tuple[str, ...] = field(default_factory=tuple)  # Additional arguments
    env: Dict[str, str] = field(default_factory=dict)  # Environment overrides
    timeout_sec: int = 300  # Timeout in seconds
    description: str = ""  # Human-readable description

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cmd": self.cmd,
            "args": list(self.args),
            "env": self.env,
            "timeout_sec": self.timeout_sec,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CommandSpec:
        return cls(
            cmd=str(data.get("cmd", "")),
            args=tuple(data.get("args", [])),
            env=dict(data.get("env", {})),
            timeout_sec=int(data.get("timeout_sec", 300)),
            description=str(data.get("description", "")),
        )


@dataclass(frozen=True)
class EcosystemCapability:
    """Declaration of a single capability for an ecosystem."""

    capability: CapabilityType
    supported: bool = True  # If False, this capability is unsupported/ABSTAIN
    commands: Tuple[CommandSpec, ...] = field(
        default_factory=tuple
    )  # Commands to run for this capability
    reason: str = ""  # Why capability is unsupported (if supported=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "capability": self.capability.value,
            "supported": self.supported,
            "commands": [cmd.to_dict() for cmd in self.commands],
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EcosystemCapability:
        cap_str = str(data.get("capability", ""))
        capability = CapabilityType(cap_str) if cap_str in CapabilityType._value2member_map_ else None
        if capability is None:
            raise ValueError(f"Unknown capability: {cap_str}")
        commands = tuple(
            CommandSpec.from_dict(cmd) for cmd in data.get("commands", [])
        )
        return cls(
            capability=capability,
            supported=bool(data.get("supported", True)),
            commands=commands,
            reason=str(data.get("reason", "")),
        )


@dataclass(frozen=True)
class EcosystemAdapter:
    """Adapter for a single ecosystem (Go, npm, pip, etc.)."""

    name: str  # "go", "npm", "pip", "rust", etc.
    display_name: str  # "Go", "npm", "Python"
    package_manager: str  # "go mod", "npm", "pip", "cargo", etc.
    capabilities: Tuple[EcosystemCapability, ...] = field(default_factory=tuple)
    file_patterns: Tuple[str, ...] = field(
        default_factory=tuple
    )  # glob patterns to detect this ecosystem (e.g., "go.mod", "package.json")
    metadata: Dict[str, Any] = field(default_factory=dict)  # Extra metadata

    def has_capability(self, cap: CapabilityType) -> bool:
        """Check if this adapter supports a capability."""
        for c in self.capabilities:
            if c.capability == cap:
                return c.supported
        return False

    def get_capability(self, cap: CapabilityType) -> Optional[EcosystemCapability]:
        """Get capability details, or None if not declared."""
        for c in self.capabilities:
            if c.capability == cap:
                return c
        return None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "package_manager": self.package_manager,
            "capabilities": [cap.to_dict() for cap in self.capabilities],
            "file_patterns": list(self.file_patterns),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EcosystemAdapter:
        capabilities = tuple(
            EcosystemCapability.from_dict(cap) for cap in data.get("capabilities", [])
        )
        return cls(
            name=str(data.get("name", "")),
            display_name=str(data.get("display_name", "")),
            package_manager=str(data.get("package_manager", "")),
            capabilities=capabilities,
            file_patterns=tuple(data.get("file_patterns", [])),
            metadata=dict(data.get("metadata", {})),
        )


class EcosystemRegistry:
    """Registry for ecosystem adapters. Provides lookup and capability resolution."""

    def __init__(self):
        self._adapters: Dict[str, EcosystemAdapter] = {}

    def register(self, adapter: EcosystemAdapter) -> None:
        """Register an adapter for an ecosystem."""
        if adapter.name in self._adapters:
            raise ValueError(f"Ecosystem '{adapter.name}' already registered")
        self._adapters[adapter.name] = adapter

    def get(self, ecosystem: str) -> Optional[EcosystemAdapter]:
        """Get adapter by ecosystem name, or None if not registered."""
        return self._adapters.get(ecosystem)

    def get_or_fail(self, ecosystem: str) -> EcosystemAdapter:
        """Get adapter by ecosystem name, raise UnknownEcosystem if not found."""
        adapter = self.get(ecosystem)
        if adapter is None:
            raise UnknownEcosystem(f"No adapter registered for ecosystem '{ecosystem}'")
        return adapter

    def get_commands(self, ecosystem: str, capability: CapabilityType) -> Tuple[CommandSpec, ...]:
        """Get command specs for a capability, or empty tuple if not supported."""
        try:
            adapter = self.get_or_fail(ecosystem)
            cap = adapter.get_capability(capability)
            if cap and cap.supported:
                return cap.commands
        except UnknownEcosystem:
            pass
        return tuple()

    def list_adapters(self) -> Dict[str, EcosystemAdapter]:
        """List all registered adapters."""
        return dict(self._adapters)

    def to_dict(self) -> Dict[str, Any]:
        """Export all adapters as a dict."""
        return {name: adapter.to_dict() for name, adapter in self._adapters.items()}


def _build_go_adapter() -> EcosystemAdapter:
    """Build the Go ecosystem adapter (MVP - full implementation)."""
    return EcosystemAdapter(
        name="go",
        display_name="Go",
        package_manager="go mod",
        capabilities=(
            EcosystemCapability(
                capability=CapabilityType.INSTALL,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="go",
                        args=("mod", "download", "-x"),
                        description="Download Go module dependencies",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.BUILD,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="go",
                        args=("build", "-o", "/dev/null", "./..."),
                        timeout_sec=300,
                        description="Build all Go packages in module",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.TEST,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="go",
                        args=("test", "-timeout", "5m", "-race", "./..."),
                        timeout_sec=300,
                        description="Run Go tests with race detector",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.VET,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="go",
                        args=("vet", "./..."),
                        description="Run Go static analyzer",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.API_DIFF,
                supported=False,
                reason="Go API diff requires manual analysis or external tools (e.g., go-diff)",
            ),
            EcosystemCapability(
                capability=CapabilityType.RELEASE_NOTE,
                supported=False,
                reason="Go release notes extracted from GitHub releases/CHANGELOG.md (framework-level)",
            ),
        ),
        file_patterns=("go.mod", "go.sum"),
        metadata={"version_file": "go.mod", "version_format": "semantic"},
    )


def _build_npm_adapter() -> EcosystemAdapter:
    """Build the npm/Node.js ecosystem adapter.

    Commands mirror what build-check.sh runs for npm PRs: a dependency install
    (with --ignore-scripts for safety), a TypeScript type-check as the "build"
    signal (the npm analogue of `go build`), the package test script, and eslint
    for static analysis. API_DIFF is supported via the standalone TypeScript
    type-surface diff (npm_apidiff.mjs); release notes stay framework-level.
    """
    return EcosystemAdapter(
        name="npm",
        display_name="npm",
        package_manager="npm",
        capabilities=(
            EcosystemCapability(
                capability=CapabilityType.INSTALL,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="npm",
                        args=("ci", "--ignore-scripts"),
                        timeout_sec=300,
                        description="Install dependencies from lockfile (scripts disabled)",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.BUILD,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="npx",
                        args=("tsc", "--noEmit"),
                        timeout_sec=300,
                        description="TypeScript type-check (npm analogue of go build)",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.TEST,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="npm",
                        args=("test", "--", "--ci"),
                        timeout_sec=300,
                        description="Run the package test script",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.VET,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="npx",
                        args=("eslint", "."),
                        description="Run eslint static analysis",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.API_DIFF,
                supported=True,
                commands=(
                    CommandSpec(
                        cmd="node",
                        args=(".github/scripts/npm_apidiff.mjs",),
                        timeout_sec=240,
                        description="TypeScript type-surface diff of the dependency's .d.ts (from/to)",
                    ),
                ),
            ),
            EcosystemCapability(
                capability=CapabilityType.RELEASE_NOTE,
                supported=False,
                reason="npm release notes extracted from GitHub releases/CHANGELOG.md (framework-level)",
            ),
        ),
        file_patterns=("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml"),
        metadata={"version_file": "package.json", "version_format": "semantic"},
    )


def _build_pip_adapter() -> EcosystemAdapter:
    """Build the pip ecosystem adapter (placeholder - not implemented yet)."""
    return EcosystemAdapter(
        name="pip",
        display_name="Python",
        package_manager="pip",
        capabilities=(
            EcosystemCapability(
                capability=CapabilityType.INSTALL,
                supported=False,
                reason="pip adapter not yet implemented",
            ),
            EcosystemCapability(
                capability=CapabilityType.BUILD,
                supported=False,
                reason="pip adapter not yet implemented",
            ),
            EcosystemCapability(
                capability=CapabilityType.TEST,
                supported=False,
                reason="pip adapter not yet implemented",
            ),
            EcosystemCapability(
                capability=CapabilityType.VET,
                supported=False,
                reason="pip adapter not yet implemented",
            ),
            EcosystemCapability(
                capability=CapabilityType.API_DIFF,
                supported=False,
                reason="pip adapter not yet implemented",
            ),
            EcosystemCapability(
                capability=CapabilityType.RELEASE_NOTE,
                supported=False,
                reason="pip adapter not yet implemented",
            ),
        ),
        file_patterns=("requirements.txt", "setup.py", "pyproject.toml"),
        metadata={"version_file": "setup.py", "version_format": "semantic"},
    )


# Global registry instance
_default_registry: Optional[EcosystemRegistry] = None


def get_default_registry() -> EcosystemRegistry:
    """Get or create the default ecosystem registry with built-in adapters."""
    global _default_registry
    if _default_registry is None:
        _default_registry = EcosystemRegistry()
        _default_registry.register(_build_go_adapter())
        _default_registry.register(_build_npm_adapter())
        _default_registry.register(_build_pip_adapter())
    return _default_registry


def new_registry() -> EcosystemRegistry:
    """Create a fresh (empty) registry for testing."""
    return EcosystemRegistry()
