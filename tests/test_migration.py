import json
import tempfile
import unittest
from pathlib import Path

from salience_ledger.adapters.evos_v2 import import_jsonl
from salience_ledger.store import Ledger


class MigrationTests(unittest.TestCase):
    def test_legacy_rows_never_auto_promote_to_core(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.jsonl"
            legacy.write_text(
                json.dumps({"id": "old-rule", "kind": "constraint", "title": "Old rule", "text": "Do X"}) + "\n",
                encoding="utf-8",
            )
            ledger = Ledger(root / "project")
            result = import_jsonl(ledger, legacy)
            self.assertEqual(result, {"imported": 1, "skipped": 0})
            imported = next(iter(ledger.records().values()))
            self.assertEqual(imported.role, "note")
            self.assertEqual(imported.salience, "archive")
            self.assertEqual(imported.authority, "agent_inference")
            self.assertEqual(imported.confidence, 0.0)

            second = import_jsonl(ledger, legacy)
            self.assertEqual(second, {"imported": 0, "skipped": 1})

    def test_identical_legacy_rows_remain_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.jsonl"
            row = json.dumps({"kind": "note", "text": "same"})
            legacy.write_text(row + "\n" + row + "\n", encoding="utf-8")
            ledger = Ledger(root / "project")
            self.assertEqual(import_jsonl(ledger, legacy), {"imported": 2, "skipped": 0})
            self.assertEqual(len(ledger.records()), 2)
            self.assertEqual(import_jsonl(ledger, legacy), {"imported": 0, "skipped": 2})


if __name__ == "__main__":
    unittest.main()
