import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SENSOR = Path(__file__).resolve().parent.parent / "bin" / "context-sensor.py"


def run_sensor(stdin_obj, env_extra=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(SENSOR)],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )
    return proc


class TestSmoke(unittest.TestCase):
    def test_empty_stdin_exits_zero(self):
        proc = run_sensor({})
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")


def _load_sensor_module():
    spec = importlib.util.spec_from_file_location("context_sensor", SENSOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestParseLastAssistantUsage(unittest.TestCase):
    def setUp(self):
        self.mod = _load_sensor_module()
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        )

    def tearDown(self):
        self.tmp.close()
        os.unlink(self.tmp.name)

    def _write(self, entries):
        for entry in entries:
            self.tmp.write(json.dumps(entry) + "\n")
        self.tmp.flush()

    def test_returns_none_when_file_missing(self):
        self.assertIsNone(
            self.mod.parse_last_assistant_usage("/nonexistent/path.jsonl")
        )

    def test_returns_none_when_empty_file(self):
        self.assertIsNone(self.mod.parse_last_assistant_usage(self.tmp.name))

    def test_returns_none_when_only_user_entries(self):
        self._write([{"type": "user", "message": {"role": "user"}}])
        self.assertIsNone(self.mod.parse_last_assistant_usage(self.tmp.name))

    def test_sums_input_cache_creation_cache_read(self):
        self._write([
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 100,
                        "cache_creation_input_tokens": 200,
                        "cache_read_input_tokens": 300,
                        "output_tokens": 50,
                    }
                },
            }
        ])
        self.assertEqual(
            self.mod.parse_last_assistant_usage(self.tmp.name), 600
        )

    def test_returns_last_when_multiple_assistant_entries(self):
        self._write([
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 10,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 0,
                    }
                },
            },
            {"type": "user", "message": {"role": "user"}},
            {
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 1000,
                        "cache_creation_input_tokens": 500,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 0,
                    }
                },
            },
        ])
        self.assertEqual(
            self.mod.parse_last_assistant_usage(self.tmp.name), 1500
        )

    def test_skips_malformed_lines(self):
        self.tmp.write("{not valid json\n")
        self.tmp.write(
            json.dumps({
                "type": "assistant",
                "message": {
                    "usage": {
                        "input_tokens": 7,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                        "output_tokens": 0,
                    }
                },
            }) + "\n"
        )
        self.tmp.flush()
        self.assertEqual(
            self.mod.parse_last_assistant_usage(self.tmp.name), 7
        )

    def test_returns_none_when_usage_missing(self):
        self._write([
            {"type": "assistant", "message": {"role": "assistant"}}
        ])
        self.assertIsNone(self.mod.parse_last_assistant_usage(self.tmp.name))


class TestComputeTier(unittest.TestCase):
    def setUp(self):
        self.mod = _load_sensor_module()

    def test_under_when_below_soft(self):
        self.assertEqual(
            self.mod.compute_tier(99_999, 100_000, 160_000), "under"
        )

    def test_soft_at_exact_soft_boundary(self):
        self.assertEqual(
            self.mod.compute_tier(100_000, 100_000, 160_000), "soft"
        )

    def test_soft_between_thresholds(self):
        self.assertEqual(
            self.mod.compute_tier(120_000, 100_000, 160_000), "soft"
        )

    def test_hard_at_exact_hard_boundary(self):
        self.assertEqual(
            self.mod.compute_tier(160_000, 100_000, 160_000), "hard"
        )

    def test_hard_above_hard(self):
        self.assertEqual(
            self.mod.compute_tier(200_000, 100_000, 160_000), "hard"
        )

    def test_zero_is_under(self):
        self.assertEqual(
            self.mod.compute_tier(0, 100_000, 160_000), "under"
        )


class TestRenderReminder(unittest.TestCase):
    def setUp(self):
        self.mod = _load_sensor_module()

    def test_under_returns_empty(self):
        self.assertEqual(self.mod.render_reminder("under", 50_000, 200_000), "")

    def test_soft_contains_token_count_and_percent(self):
        text = self.mod.render_reminder("soft", 120_000, 200_000)
        self.assertIn("120000", text)
        self.assertIn("60%", text)
        self.assertIn("<context-usage>", text)
        self.assertIn("natural stop point", text)
        self.assertNotIn("status=\"critical\"", text)

    def test_hard_contains_critical_status_and_both_role_branches(self):
        text = self.mod.render_reminder("hard", 170_000, 200_000)
        self.assertIn("status=\"critical\"", text)
        self.assertIn("170000", text)
        self.assertIn("85%", text)
        self.assertIn("main session", text)
        self.assertIn("subagent", text)
        self.assertIn("auto-compact.sh", text)
        self.assertIn("handoff", text)

    def test_percent_rounds_to_integer(self):
        text = self.mod.render_reminder("soft", 123_456, 200_000)
        self.assertIn("62%", text)


class TestMainEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        )

    def tearDown(self):
        self.tmp.close()
        os.unlink(self.tmp.name)

    def _write_transcript(self, total):
        entry = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": total,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                }
            },
        }
        self.tmp.write(json.dumps(entry) + "\n")
        self.tmp.flush()

    def _stdin(self, event="PostToolUse"):
        return {
            "hook_event_name": event,
            "session_id": "test-session",
            "transcript_path": self.tmp.name,
        }

    def test_under_tier_emits_nothing(self):
        self._write_transcript(50_000)
        proc = run_sensor(self._stdin())
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_soft_tier_emits_hook_specific_output(self):
        self._write_transcript(120_000)
        proc = run_sensor(self._stdin())
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"], "PostToolUse"
        )
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("<context-usage>", ctx)
        self.assertIn("120000", ctx)
        self.assertNotIn("status=\"critical\"", ctx)

    def test_hard_tier_emits_critical(self):
        self._write_transcript(170_000)
        proc = run_sensor(self._stdin(event="UserPromptSubmit"))
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(
            payload["hookSpecificOutput"]["hookEventName"], "UserPromptSubmit"
        )
        self.assertIn(
            "status=\"critical\"",
            payload["hookSpecificOutput"]["additionalContext"],
        )

    def test_missing_transcript_path_is_silent(self):
        proc = run_sensor({"hook_event_name": "PostToolUse"})
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_transcript_file_does_not_exist_is_silent(self):
        proc = run_sensor({
            "hook_event_name": "PostToolUse",
            "transcript_path": "/nonexistent/path.jsonl",
        })
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_malformed_stdin_is_silent(self):
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp"}
        proc = subprocess.run(
            [sys.executable, str(SENSOR)],
            input="not valid json",
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")


class TestEnvOverrides(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        )
        entry = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 60_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                }
            },
        }
        self.tmp.write(json.dumps(entry) + "\n")
        self.tmp.flush()

    def tearDown(self):
        self.tmp.close()
        os.unlink(self.tmp.name)

    def _stdin(self):
        return {
            "hook_event_name": "PostToolUse",
            "transcript_path": self.tmp.name,
        }

    def test_soft_override_fires_below_default(self):
        # 60k is under the default 100k but above a 50k override.
        proc = run_sensor(
            self._stdin(),
            env_extra={"CLAUDE_COMPACT_SOFT": "50000"},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("<context-usage>", ctx)
        self.assertNotIn("status=\"critical\"", ctx)

    def test_hard_override_fires_below_default(self):
        proc = run_sensor(
            self._stdin(),
            env_extra={
                "CLAUDE_COMPACT_SOFT": "10000",
                "CLAUDE_COMPACT_HARD": "40000",
            },
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertIn(
            "status=\"critical\"",
            payload["hookSpecificOutput"]["additionalContext"],
        )

    def test_invalid_env_falls_back_to_default(self):
        # Invalid ints should leave defaults in place; 60k stays under 100k.
        proc = run_sensor(
            self._stdin(),
            env_extra={"CLAUDE_COMPACT_SOFT": "not-a-number"},
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_window_override_changes_percent(self):
        proc = run_sensor(
            self._stdin(),
            env_extra={
                "CLAUDE_COMPACT_SOFT": "50000",
                "CLAUDE_COMPACT_WINDOW": "100000",
            },
        )
        payload = json.loads(proc.stdout)
        self.assertIn(
            "60%", payload["hookSpecificOutput"]["additionalContext"]
        )


class TestDebugLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False
        )
        entry = {
            "type": "assistant",
            "message": {
                "usage": {
                    "input_tokens": 120_000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 0,
                }
            },
        }
        self.tmp.write(json.dumps(entry) + "\n")
        self.tmp.flush()
        self.log_path = tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ).name
        os.unlink(self.log_path)  # ensure it does not exist yet

    def tearDown(self):
        self.tmp.close()
        os.unlink(self.tmp.name)
        if os.path.exists(self.log_path):
            os.unlink(self.log_path)

    def test_debug_off_writes_no_log(self):
        run_sensor({
            "hook_event_name": "PostToolUse",
            "transcript_path": self.tmp.name,
        })
        self.assertFalse(os.path.exists(self.log_path))

    def test_debug_on_writes_log(self):
        run_sensor(
            {
                "hook_event_name": "PostToolUse",
                "transcript_path": self.tmp.name,
            },
            env_extra={
                "CLAUDE_COMPACT_DEBUG": "1",
                "CLAUDE_COMPACT_DEBUG_FILE": self.log_path,
            },
        )
        self.assertTrue(os.path.exists(self.log_path))
        with open(self.log_path) as fh:
            content = fh.read()
        self.assertIn("tier=soft", content)
        self.assertIn("total=120000", content)


if __name__ == "__main__":
    unittest.main()
