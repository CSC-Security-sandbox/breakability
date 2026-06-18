#!/usr/bin/env python3
"""Unit tests for ai_backend -- the unified record/replay model backend.

Fast + offline: a tiny shell stub stands in for the agent CLI so we exercise the
real subprocess path without any model call. No network, sub-second.
"""

import json
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ai_backend as ab


def _make_stub(dirpath, body):
    """Write an executable stub that ignores its prompt and prints `body`."""
    p = os.path.join(dirpath, "stub_agent")
    with open(p, "w") as fh:
        fh.write("#!/usr/bin/env bash\nprintf '%s' " + json_singlequote(body) + "\n")
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return p


def json_singlequote(s):
    # safe single-quoted bash literal
    return "'" + s.replace("'", "'\\''") + "'"


class CassetteIdentityTests(unittest.TestCase):
    def test_explicit_key_is_stable_and_human_readable(self):
        a = ab.cassette_id("adjudication", "prompt A", key="adj-10")
        b = ab.cassette_id("adjudication", "TOTALLY different prompt", key="adj-10")
        self.assertEqual(a, b, "explicit key must ignore prompt text -> portable")
        self.assertIn("adj-10", a)

    def test_content_hash_when_no_key(self):
        a = ab.cassette_id("ns", "prompt one")
        b = ab.cassette_id("ns", "prompt two")
        self.assertNotEqual(a, b)
        # deterministic
        self.assertEqual(a, ab.cassette_id("ns", "prompt one"))

    def test_key_sanitized(self):
        cid = ab.cassette_id("ns/x", "p", key="weird key/../etc")
        self.assertNotIn("/", cid)          # no path separator -> no traversal
        self.assertNotIn(os.sep, cid)
        self.assertEqual(cid, os.path.basename(cid))  # single path component


class ReplayTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _backend(self, mode, cmd="false"):
        return ab.Backend(mode=mode, model="m", cmd_template=cmd,
                          cassette_dir=self.dir, timeout=5)

    def test_replay_hit_returns_recorded_response(self):
        be = self._backend(ab.MODE_REPLAY)
        path = ab.cassette_path("ns", "the prompt", key="k1", cassette_dir=self.dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"response": "RECORDED"}, fh)
        self.assertEqual(be.invoke("the prompt", namespace="ns", key="k1"), "RECORDED")

    def test_replay_miss_returns_empty_failsafe(self):
        be = self._backend(ab.MODE_REPLAY)
        self.assertEqual(be.invoke("no cassette", namespace="ns", key="missing"), "")

    def test_replay_never_invokes_command(self):
        # cmd would fail loudly if ever run; replay must not run it.
        be = self._backend(ab.MODE_REPLAY, cmd="/nonexistent/agent --model {model}")
        self.assertEqual(be.invoke("p", namespace="ns", key="x"), "")


class LiveAndRecordTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.stub = _make_stub(self.dir, "STUB_OUTPUT")

    def test_live_runs_command(self):
        be = ab.Backend(mode=ab.MODE_LIVE, model="m", cmd_template=self.stub,
                        cassette_dir=self.dir, timeout=10)
        self.assertEqual(be.invoke("p", namespace="ns", key="k"), "STUB_OUTPUT")

    def test_live_missing_command_is_failsafe_empty(self):
        be = ab.Backend(mode=ab.MODE_LIVE, model="m",
                        cmd_template="/no/such/binary {model}",
                        cassette_dir=self.dir, timeout=5)
        self.assertEqual(be.invoke("p", namespace="ns", key="k"), "")

    def test_record_writes_cassette_then_replay_offline(self):
        rec = ab.Backend(mode=ab.MODE_RECORD, model="m", cmd_template=self.stub,
                         cassette_dir=self.dir, timeout=10)
        self.assertEqual(rec.invoke("p", namespace="ns", key="rk"), "STUB_OUTPUT")
        # now replay with a command that cannot run -> proves it came from cassette
        rep = ab.Backend(mode=ab.MODE_REPLAY, model="m", cmd_template="false",
                         cassette_dir=self.dir, timeout=5)
        self.assertEqual(rep.invoke("p", namespace="ns", key="rk"), "STUB_OUTPUT")

    def test_build_argv_appends_cursor_api_key(self):
        os.environ["CURSOR_API_KEY"] = "secret123"
        try:
            be = ab.Backend(mode=ab.MODE_LIVE, model="claude-4-sonnet",
                            cmd_template="agent -p --model {model}",
                            cassette_dir=self.dir)
            argv = be.build_argv()
            self.assertIn("--api-key", argv)
            self.assertIn("secret123", argv)
            self.assertEqual(argv[argv.index("--model") + 1], "claude-4-sonnet")
        finally:
            del os.environ["CURSOR_API_KEY"]

    def test_build_argv_no_key_for_non_agent(self):
        os.environ["CURSOR_API_KEY"] = "secret123"
        try:
            be = ab.Backend(mode=ab.MODE_LIVE, model="m",
                            cmd_template="python3 stub.py",
                            cassette_dir=self.dir)
            self.assertNotIn("--api-key", be.build_argv())
        finally:
            del os.environ["CURSOR_API_KEY"]

    def test_build_argv_copilot_autocompletes_noninteractive(self):
        be = ab.Backend(mode=ab.MODE_LIVE, model="claude-sonnet-4.5",
                        cmd_template="copilot --model {model}",
                        cassette_dir=self.dir)
        argv = be.build_argv()
        self.assertIn("--allow-all-tools", argv)
        self.assertIn("--no-color", argv)
        # -p must be the final token so the prompt (appended by _run_live) is its value.
        self.assertEqual(argv[-1], "-p")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-sonnet-4.5")
        # No Cursor key leakage onto the copilot backend.
        self.assertNotIn("--api-key", argv)

    def test_build_argv_copilot_respects_explicit_flags(self):
        be = ab.Backend(mode=ab.MODE_LIVE, model="m",
                        cmd_template="copilot --allow-all --no-color --prompt",
                        cassette_dir=self.dir)
        argv = be.build_argv()
        # Already-present flags are not duplicated.
        self.assertEqual(argv.count("--no-color"), 1)
        self.assertNotIn("--allow-all-tools", argv)
        self.assertNotIn("-p", argv)


class EnvResolutionTests(unittest.TestCase):
    def test_mode_defaults_to_live(self):
        old = os.environ.pop("BRK_AGENT_MODE", None)
        try:
            self.assertEqual(ab.resolve_mode(), ab.MODE_LIVE)
        finally:
            if old is not None:
                os.environ["BRK_AGENT_MODE"] = old

    def test_mode_from_env(self):
        os.environ["BRK_AGENT_MODE"] = "replay"
        try:
            self.assertEqual(ab.resolve_mode(), ab.MODE_REPLAY)
        finally:
            del os.environ["BRK_AGENT_MODE"]

    def test_bad_mode_falls_back_to_live(self):
        os.environ["BRK_AGENT_MODE"] = "garbage"
        try:
            self.assertEqual(ab.resolve_mode(), ab.MODE_LIVE)
        finally:
            del os.environ["BRK_AGENT_MODE"]

    def test_model_default_and_override(self):
        old = os.environ.pop("BRK_AGENT_MODEL", None)
        try:
            self.assertEqual(ab.resolve_model(), ab.DEFAULT_MODEL)
            self.assertEqual(ab.resolve_model("x"), "x")
            os.environ["BRK_AGENT_MODEL"] = "envmodel"
            self.assertEqual(ab.resolve_model(), "envmodel")
        finally:
            os.environ.pop("BRK_AGENT_MODEL", None)
            if old is not None:
                os.environ["BRK_AGENT_MODEL"] = old


if __name__ == "__main__":
    unittest.main()
