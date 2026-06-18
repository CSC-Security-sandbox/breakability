#!/usr/bin/env python3
"""Unit tests for ecosystem_adapters.py."""
import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ecosystem_adapters import (  # noqa: E402
    CapabilityNotSupported,
    CapabilityType,
    CommandSpec,
    EcosystemAdapter,
    EcosystemCapability,
    EcosystemError,
    EcosystemRegistry,
    UnknownEcosystem,
    _build_go_adapter,
    _build_npm_adapter,
    _build_pip_adapter,
    get_default_registry,
    new_registry,
)


class CommandSpecTests(unittest.TestCase):
    """Tests for CommandSpec."""

    def test_basic_command_spec(self):
        cmd = CommandSpec(cmd="go", args=("build", "./..."))
        self.assertEqual(cmd.cmd, "go")
        self.assertEqual(cmd.args, ("build", "./..."))
        self.assertEqual(cmd.timeout_sec, 300)

    def test_command_spec_with_env(self):
        cmd = CommandSpec(
            cmd="npm",
            args=("ci",),
            env={"NODE_ENV": "production"},
            timeout_sec=120,
        )
        self.assertEqual(cmd.env["NODE_ENV"], "production")

    def test_command_spec_to_dict(self):
        cmd = CommandSpec(
            cmd="go",
            args=("test", "./..."),
            timeout_sec=500,
            description="Run tests",
        )
        d = cmd.to_dict()
        self.assertEqual(d["cmd"], "go")
        self.assertEqual(d["args"], ["test", "./..."])
        self.assertEqual(d["timeout_sec"], 500)
        self.assertEqual(d["description"], "Run tests")

    def test_command_spec_from_dict(self):
        data = {
            "cmd": "pytest",
            "args": ["--tb=short"],
            "env": {"PYTHONPATH": "/app"},
            "timeout_sec": 200,
        }
        cmd = CommandSpec.from_dict(data)
        self.assertEqual(cmd.cmd, "pytest")
        self.assertEqual(cmd.args, ("--tb=short",))
        self.assertEqual(cmd.timeout_sec, 200)


class EcosystemCapabilityTests(unittest.TestCase):
    """Tests for EcosystemCapability."""

    def test_supported_capability(self):
        cap = EcosystemCapability(
            capability=CapabilityType.BUILD,
            supported=True,
            commands=(CommandSpec(cmd="go", args=("build", "./...")),),
        )
        self.assertTrue(cap.supported)
        self.assertEqual(len(cap.commands), 1)

    def test_unsupported_capability(self):
        cap = EcosystemCapability(
            capability=CapabilityType.API_DIFF,
            supported=False,
            reason="Not applicable for Go",
        )
        self.assertFalse(cap.supported)
        self.assertEqual(cap.reason, "Not applicable for Go")

    def test_capability_to_dict(self):
        cap = EcosystemCapability(
            capability=CapabilityType.TEST,
            supported=True,
            commands=(CommandSpec(cmd="go", args=("test", "./...")),),
        )
        d = cap.to_dict()
        self.assertEqual(d["capability"], "test")
        self.assertTrue(d["supported"])

    def test_capability_from_dict(self):
        data = {
            "capability": "build",
            "supported": True,
            "commands": [{"cmd": "go", "args": ["build"]}],
        }
        cap = EcosystemCapability.from_dict(data)
        self.assertEqual(cap.capability, CapabilityType.BUILD)
        self.assertTrue(cap.supported)


class EcosystemAdapterTests(unittest.TestCase):
    """Tests for EcosystemAdapter."""

    def test_go_adapter_basics(self):
        adapter = _build_go_adapter()
        self.assertEqual(adapter.name, "go")
        self.assertEqual(adapter.display_name, "Go")
        self.assertEqual(adapter.package_manager, "go mod")

    def test_go_adapter_has_required_capabilities(self):
        adapter = _build_go_adapter()
        self.assertTrue(adapter.has_capability(CapabilityType.BUILD))
        self.assertTrue(adapter.has_capability(CapabilityType.TEST))
        self.assertTrue(adapter.has_capability(CapabilityType.INSTALL))
        self.assertTrue(adapter.has_capability(CapabilityType.VET))

    def test_go_adapter_unsupported_capabilities(self):
        adapter = _build_go_adapter()
        # API_DIFF and RELEASE_NOTE are not supported (should return False)
        self.assertFalse(adapter.has_capability(CapabilityType.API_DIFF))
        self.assertFalse(adapter.has_capability(CapabilityType.RELEASE_NOTE))

    def test_go_adapter_build_commands(self):
        adapter = _build_go_adapter()
        cap = adapter.get_capability(CapabilityType.BUILD)
        self.assertIsNotNone(cap)
        self.assertTrue(cap.supported)
        self.assertGreater(len(cap.commands), 0)
        self.assertEqual(cap.commands[0].cmd, "go")

    def test_npm_adapter_capabilities(self):
        adapter = _build_npm_adapter()
        self.assertEqual(adapter.name, "npm")
        self.assertEqual(adapter.display_name, "npm")
        # Real adapter: install/build/test/vet/api_diff supported; release_note framework-level.
        for cap_type in (
            CapabilityType.INSTALL,
            CapabilityType.BUILD,
            CapabilityType.TEST,
            CapabilityType.VET,
            CapabilityType.API_DIFF,
        ):
            self.assertTrue(
                adapter.has_capability(cap_type), f"npm should support {cap_type}"
            )
        self.assertFalse(adapter.has_capability(CapabilityType.RELEASE_NOTE))
        # BUILD runs the TypeScript type-check (npm analogue of `go build`).
        build = adapter.get_capability(CapabilityType.BUILD)
        self.assertTrue(build.commands)
        self.assertEqual(build.commands[0].cmd, "npx")
        self.assertEqual(build.commands[0].args[0], "tsc")

    def test_adapter_to_dict(self):
        adapter = _build_go_adapter()
        d = adapter.to_dict()
        self.assertEqual(d["name"], "go")
        self.assertEqual(d["package_manager"], "go mod")
        self.assertIn("capabilities", d)

    def test_adapter_from_dict(self):
        original = _build_go_adapter()
        d = original.to_dict()
        restored = EcosystemAdapter.from_dict(d)
        self.assertEqual(restored.name, original.name)
        self.assertEqual(len(restored.capabilities), len(original.capabilities))


class EcosystemRegistryTests(unittest.TestCase):
    """Tests for EcosystemRegistry."""

    def test_empty_registry(self):
        reg = new_registry()
        self.assertIsNone(reg.get("go"))

    def test_register_adapter(self):
        reg = new_registry()
        adapter = _build_go_adapter()
        reg.register(adapter)
        self.assertEqual(reg.get("go").name, "go")

    def test_register_duplicate_fails(self):
        reg = new_registry()
        adapter1 = _build_go_adapter()
        adapter2 = _build_go_adapter()
        reg.register(adapter1)
        with self.assertRaises(ValueError):
            reg.register(adapter2)

    def test_get_or_fail_known_ecosystem(self):
        reg = new_registry()
        reg.register(_build_go_adapter())
        adapter = reg.get_or_fail("go")
        self.assertEqual(adapter.name, "go")

    def test_get_or_fail_unknown_ecosystem(self):
        reg = new_registry()
        with self.assertRaises(UnknownEcosystem):
            reg.get_or_fail("unknown")

    def test_get_commands_for_capability(self):
        reg = new_registry()
        reg.register(_build_go_adapter())
        cmds = reg.get_commands("go", CapabilityType.BUILD)
        self.assertGreater(len(cmds), 0)

    def test_get_commands_for_unsupported_capability(self):
        reg = new_registry()
        reg.register(_build_go_adapter())
        # API_DIFF is unsupported, should return empty
        cmds = reg.get_commands("go", CapabilityType.API_DIFF)
        self.assertEqual(len(cmds), 0)

    def test_get_commands_for_unknown_ecosystem(self):
        reg = new_registry()
        reg.register(_build_go_adapter())
        # Unknown ecosystem should not crash, return empty
        cmds = reg.get_commands("unknown", CapabilityType.BUILD)
        self.assertEqual(len(cmds), 0)

    def test_default_registry_has_go(self):
        reg = get_default_registry()
        adapter = reg.get("go")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.name, "go")

    def test_default_registry_has_npm(self):
        reg = get_default_registry()
        adapter = reg.get("npm")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.name, "npm")

    def test_default_registry_has_pip(self):
        reg = get_default_registry()
        adapter = reg.get("pip")
        self.assertIsNotNone(adapter)
        self.assertEqual(adapter.name, "pip")

    def test_default_registry_npm_supported(self):
        """npm adapter should now expose concrete build/install/test commands."""
        reg = get_default_registry()
        for cap_type in (
            CapabilityType.INSTALL,
            CapabilityType.BUILD,
            CapabilityType.TEST,
            CapabilityType.API_DIFF,
        ):
            cmds = reg.get_commands("npm", cap_type)
            self.assertGreater(len(cmds), 0, f"npm should support {cap_type}")

    def test_default_registry_go_build_commands(self):
        """Go adapter should have concrete build commands."""
        reg = get_default_registry()
        cmds = reg.get_commands("go", CapabilityType.BUILD)
        self.assertGreater(len(cmds), 0)
        self.assertEqual(cmds[0].cmd, "go")

    def test_list_adapters(self):
        reg = get_default_registry()
        adapters = reg.list_adapters()
        self.assertIn("go", adapters)
        self.assertIn("npm", adapters)
        self.assertIn("pip", adapters)

    def test_to_dict(self):
        reg = new_registry()
        reg.register(_build_go_adapter())
        d = reg.to_dict()
        self.assertIn("go", d)
        self.assertEqual(d["go"]["name"], "go")


class FailClosedBehaviorTests(unittest.TestCase):
    """Tests for fail-closed behavior on unknown ecosystems and unsupported capabilities."""

    def test_unknown_ecosystem_returns_no_commands(self):
        """Unknown ecosystems should not crash; instead return empty."""
        reg = get_default_registry()
        cmds = reg.get_commands("rust", CapabilityType.BUILD)
        self.assertEqual(len(cmds), 0)

    def test_get_or_fail_unknown_ecosystem_raises(self):
        """Explicitly getting unknown ecosystem with get_or_fail should raise."""
        reg = get_default_registry()
        with self.assertRaises(UnknownEcosystem):
            reg.get_or_fail("rust")

    def test_npm_build_supported(self):
        """npm build capability is now supported (TypeScript type-check)."""
        reg = get_default_registry()
        npm = reg.get("npm")
        cap = npm.get_capability(CapabilityType.BUILD)
        self.assertTrue(cap.supported)
        self.assertEqual(cap.commands[0].args[0], "tsc")

    def test_go_api_diff_unsupported(self):
        """Go API_DIFF is unsupported (framework should ABSTAIN)."""
        reg = get_default_registry()
        go = reg.get("go")
        cap = go.get_capability(CapabilityType.API_DIFF)
        self.assertFalse(cap.supported)


if __name__ == "__main__":
    unittest.main()
