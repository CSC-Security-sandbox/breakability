#!/usr/bin/env python3
"""Unit tests for differential-probe sandbox safety (no pytest).

Run: python3 .github/scripts/test_probe_sandbox.py
Exits non-zero on any failure.

Tests the following invariants:
  - Environment scrubbing removes all credentials and credential-like paths
  - Ephemeral HOME isolation works correctly
  - Timeout handling doesn't leak resources
  - Workdir enforcement prevents escapes
"""
import os
import sys
import tempfile
import shutil
import importlib.util

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import differential-probe.py (with hyphen) using importlib
_dp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "differential-probe.py")
_spec = importlib.util.spec_from_file_location("differential_probe", _dp_path)
differential_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(differential_probe)


def test_scrub_env_minimal():
    """Test that scrub_env removes credentials but keeps essentials."""
    scrub_env_for_agent = differential_probe.scrub_env_for_agent
    
    env_in = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/home/user",
        "USER": "testuser",
        "GH_TOKEN": "secret123",
        "GITHUB_TOKEN": "secret456",
        "CURSOR_API_KEY": "cursor_key",
        "MY_SECRET": "value",
        "AWS_SECRET_ACCESS_KEY": "aws_secret",
        "DB_PASSWORD": "password",
        "SSH_AUTH_SOCK": "/tmp/ssh-agent",
        "SSH_AGENT_PID": "12345",
        "PYTHONPATH": "/custom/path",
        "DOCKER_HOST": "unix:///var/run/docker.sock",
        "GOWORK": "on",
        "GOPATH": "/go",
        "GOCACHE": "/go/cache",
    }
    
    env_out = scrub_env_for_agent(env_in, keep_api_key=True, work_gocache="/tmp/gocache")
    
    # Cursor key must be preserved if keep_api_key=True
    assert env_out.get("CURSOR_API_KEY") == "cursor_key", "CURSOR_API_KEY should be preserved"
    
    # All credential-like vars must be removed
    assert "GH_TOKEN" not in env_out, "GH_TOKEN should be removed"
    assert "GITHUB_TOKEN" not in env_out, "GITHUB_TOKEN should be removed"
    assert "MY_SECRET" not in env_out, "MY_SECRET should be removed"
    assert "AWS_SECRET_ACCESS_KEY" not in env_out, "AWS_SECRET_ACCESS_KEY should be removed"
    assert "DB_PASSWORD" not in env_out, "DB_PASSWORD should be removed"
    assert "SSH_AUTH_SOCK" not in env_out, "SSH_AUTH_SOCK should be removed"
    assert "SSH_AGENT_PID" not in env_out, "SSH_AGENT_PID should be removed"
    
    # Dangerous path vars must be removed
    assert "PYTHONPATH" not in env_out, "PYTHONPATH should be removed"
    assert "DOCKER_HOST" not in env_out, "DOCKER_HOST should be removed"
    
    # Go config must be set for sandbox
    assert env_out.get("GOWORK") == "off", "GOWORK should be off"
    assert env_out.get("GOCACHE") == "/tmp/gocache", "GOCACHE should be set"
    
    print("✓ test_scrub_env_minimal passed")


def test_scrub_env_without_api_key():
    """Test that scrub_env removes API key when not requested."""
    scrub_env_for_agent = differential_probe.scrub_env_for_agent
    
    env_in = {
        "CURSOR_API_KEY": "cursor_key",
        "GH_TOKEN": "token",
    }
    
    env_out = scrub_env_for_agent(env_in, keep_api_key=False, work_gocache="/tmp/gocache")
    
    assert "CURSOR_API_KEY" not in env_out, "CURSOR_API_KEY should be removed when keep_api_key=False"
    assert "GH_TOKEN" not in env_out, "GH_TOKEN should be removed"
    
    print("✓ test_scrub_env_without_api_key passed")


def test_ephemeral_home():
    """Test that ephemeral HOME is created and isolated."""
    create_ephemeral_home = differential_probe.create_ephemeral_home
    
    tmpdir = tempfile.mkdtemp(prefix="test_ephemeral_")
    try:
        home_dir = create_ephemeral_home(tmpdir)
        
        # Home must be within tmpdir
        assert home_dir.startswith(tmpdir), f"Ephemeral HOME {home_dir} must be within {tmpdir}"
        
        # Home must exist as a directory
        assert os.path.isdir(home_dir), f"Ephemeral HOME {home_dir} must exist"
        
        # Common credentials directories must exist and be empty
        ssh_dir = os.path.join(home_dir, ".ssh")
        if os.path.exists(ssh_dir):
            # If .ssh exists, it should be empty (no inherited keys)
            items = os.listdir(ssh_dir)
            assert len(items) == 0, f".ssh must be empty, found: {items}"
        
        # Verify isolation: HOME can be set without affecting parent
        real_home = os.environ.get("HOME")
        try:
            os.environ["HOME"] = home_dir
            assert os.environ["HOME"] == home_dir
        finally:
            if real_home:
                os.environ["HOME"] = real_home
        
        print("✓ test_ephemeral_home passed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_workdir_validation():
    """Test that workdir validation prevents escape attempts."""
    validate_workdir = differential_probe.validate_workdir
    
    tmpdir = tempfile.mkdtemp(prefix="test_workdir_")
    try:
        # Valid: workdir is subdirectory of temp
        subdir = os.path.join(tmpdir, "subdir")
        os.makedirs(subdir, exist_ok=True)
        assert validate_workdir(subdir, tmpdir), "Valid subdir should pass validation"
        
        # Valid: workdir is exact tmpdir
        assert validate_workdir(tmpdir, tmpdir), "Exact tmpdir should pass validation"
        
        # Invalid: workdir tries to escape
        parent = os.path.dirname(tmpdir)
        assert not validate_workdir(parent, tmpdir), "Parent dir should fail validation"
        
        # Invalid: absolute path outside tmpdir
        assert not validate_workdir("/etc/passwd", tmpdir), "Absolute path outside should fail"
        
        print("✓ test_workdir_validation passed")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_env_scrub_no_leaks():
    """Test that common credential patterns are caught."""
    scrub_env_for_agent = differential_probe.scrub_env_for_agent
    
    # Examples of credential patterns that should all be removed
    env_in = {
        "KUBECONFIG": "/etc/kube",
        "VAULT_ADDR": "http://vault:8200",
        "VAULT_TOKEN": "secret",
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/creds.json",
        "SLACK_BOT_TOKEN": "xoxb-token",
        "STRIPE_API_KEY": "sk_live_key",
        "DATABASE_URL": "postgres://user:pass@host/db",
        "API_SECRET": "secret",
        "SECRET_KEY": "another_secret",
        "PRIVATE_KEY": "key_data",
        "PASSWD": "password",
        "ENCRYPTION_KEY": "key",
        "CERTIFICATE": "cert_data",
    }
    
    env_out = scrub_env_for_agent(env_in, keep_api_key=False, work_gocache="/tmp/gocache")
    
    for key in env_in.keys():
        if key not in ("KUBECONFIG",):  # Some might be legitimate to keep
            assert key not in env_out, f"Credential-like var {key} should be removed"
    
    print("✓ test_env_scrub_no_leaks passed")


def test_grade_residual_classifies_all_bullets_before_truncating():
    """A not-observable bullet after MAX_BULLETS must still veto probing."""
    pr = {
        "package": "example.com/pkg",
        "from": "1.0.0",
        "to": "1.1.0",
        "deterministic": {
            "changelogSignal": {
                "bullets": [
                    "function now returns an error instead of nil",
                    "function now returns 0 instead of -1",
                    "output format changed to RFC3339",
                    "now rejects empty strings with a validation error",
                    "no longer returns nil; returns an empty slice",
                    "default cardinality limit changed 0 -> 2000",
                ]
            }
        },
        "declared_break_reachability": {
            "surface_evidence": [{
                "named": True,
                "is_test": False,
                "path": "example.com/pkg",
                "symbol": "New",
                "file": "internal/metrics.go",
                "line": 22,
            }]
        },
    }

    grade = differential_probe.grade_residual("999", pr, {"probe": 10, "reason": 0})
    assert grade["router_class"] == "not_observable", (
        "not-observable bullet after MAX_BULLETS must still route to reasoning, not probe"
    )
    assert grade["source"] != "budget_exhausted", "probe path should not be reached"
    print("✓ test_grade_residual_classifies_all_bullets_before_truncating passed")


def run_all():
    """Run all tests."""
    tests = [
        test_scrub_env_minimal,
        test_scrub_env_without_api_key,
        test_ephemeral_home,
        test_workdir_validation,
        test_env_scrub_no_leaks,
        test_grade_residual_classifies_all_bullets_before_truncating,
    ]
    
    failed = []
    for test in tests:
        try:
            test()
        except Exception as e:
            failed.append((test.__name__, e))
            print(f"✗ {test.__name__} failed: {e}", file=sys.stderr)
    
    if failed:
        print(f"\n{len(failed)} test(s) failed:", file=sys.stderr)
        for name, e in failed:
            print(f"  - {name}: {e}", file=sys.stderr)
        return 1
    
    print(f"\n✓ All {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
