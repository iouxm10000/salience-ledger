import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class TaskRunCliTests(unittest.TestCase):
    def test_cli_round_trip_and_completion_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ)

            def run(*arguments, check=True):
                return subprocess.run(
                    [sys.executable, "-m", "salience_ledger", "--root", str(root), *arguments],
                    check=check,
                    text=True,
                    capture_output=True,
                    env=env,
                )

            run("init")
            source = run(
                "observe",
                "--content", "Conflicting user meanings require confirmation.",
                "--source-type", "user_message",
            ).stdout.strip()
            run(
                "add",
                "--id", "ambiguity-gate",
                "--title", "Ambiguity gate",
                "--text", "Ask the user.",
                "--type", "procedural",
                "--role", "non_negotiable",
                "--authority", "user_explicit",
                "--salience", "core",
                "--source-episode", source,
            )
            run("build")
            run(
                "task-init",
                "--run-id", "smoke",
                "--goal", "Close one item",
                "--gate", "unit",
                "--completion", "item verified",
            )
            run(
                "task-event",
                "--run-id", "smoke",
                "--actor", "executor",
                "--event-type", "item_registered",
                "--payload-json", json.dumps({
                    "item_id": "i1", "title": "smoke", "acceptance": "unit passes"
                }),
            )
            before = run("task-doctor", "--run-id", "smoke", "--completion", check=False)
            self.assertEqual(before.returncode, 1)
            self.assertIn("open item: i1", before.stdout)
            run(
                "task-event",
                "--run-id", "smoke",
                "--actor", "executor",
                "--event-type", "round_closed",
                "--payload-json", json.dumps({
                    "round": 1,
                    "item_id": "i1",
                    "result": "completed",
                    "evidence": ["unittest output"],
                    "gates": {"unit": "pass"},
                    "mode": "implement",
                    "net_lines": 1,
                }),
            )
            run(
                "task-event",
                "--run-id", "smoke",
                "--actor", "executor",
                "--event-type", "completion_claimed",
                "--payload-json", json.dumps({
                    "evidence": ["unittest output"],
                    "criteria": {"item verified": ["unittest output"]},
                }),
            )
            run(
                "task-event",
                "--run-id", "smoke",
                "--actor", "supervisor",
                "--event-type", "completion_accepted",
                "--payload-json", json.dumps({
                    "evidence": ["cold-read reproduction"],
                    "criteria": {"item verified": ["cold-read reproduction"]},
                }),
            )
            self.assertEqual(run("task-doctor", "--run-id", "smoke", "--completion").stdout, "PASS\n")
            context = run("task-context", "--run-id", "smoke").stdout
            self.assertIn("# Salience Task Run", context)
            self.assertIn("completion: `accepted`", context)


if __name__ == "__main__":
    unittest.main()
