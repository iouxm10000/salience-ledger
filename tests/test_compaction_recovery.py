import tempfile
import unittest
from pathlib import Path

from salience_ledger.models import MemoryRecord
from salience_ledger.store import Ledger


class CompactionRecoveryTests(unittest.TestCase):
    def test_core_and_blockers_survive_large_archive_retrieval(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(Path(directory))
            source = ledger.observe(
                "Never let a newer ambiguous statement silently replace an older user meaning.",
                source_type="user_message",
            )
            ledger.append(
                MemoryRecord(
                    id="ambiguity-rule",
                    title="Ambiguity requires confirmation",
                    text="Show both user statements and ask for explicit confirmation.",
                    memory_type="procedural",
                    role="non_negotiable",
                    authority="user_explicit",
                    salience="core",
                    importance=100,
                    source_episode_ids=(source,),
                )
            )
            blocker_source = ledger.observe("Restart parity still fails.", source_type="test_result")
            ledger.append(
                MemoryRecord(
                    id="restart-regression",
                    title="Restart regression",
                    text="The restart path is not equivalent yet.",
                    memory_type="episodic",
                    role="counterexample",
                    authority="machine_result",
                    salience="core",
                    importance=100,
                    source_episode_ids=(blocker_source,),
                    blocks_completion=True,
                )
            )
            for number in range(200):
                ledger.append(
                    MemoryRecord(
                        id=f"archive-{number:03d}",
                        title=f"Archive discussion {number}",
                        text=f"Verbose banana discussion number {number}.",
                        memory_type="episodic",
                        role="note",
                        authority="agent_inference",
                        salience="archive",
                        importance=90,
                    )
                )
            ledger.build()
            recovered = ledger.recovery_context("banana", limit=3)
            self.assertLess(
                recovered.index("Ambiguity requires confirmation"),
                recovered.index("Archive discussion"),
            )
            self.assertIn("Restart regression", recovered)
            self.assertTrue(any("completion blocked" in item for item in ledger.audit(completion=True)))


if __name__ == "__main__":
    unittest.main()
