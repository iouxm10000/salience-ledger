import unittest

from salience_ledger.models import MemoryRecord


def base(**overrides):
    data = {
        "id": "m1", "title": "title", "text": "text", "memory_type": "semantic",
        "role": "accepted_decision", "authority": "engineering_decision",
        "salience": "core", "rationale": "because",
    }
    data.update(overrides)
    return MemoryRecord(**data)


class ModelTests(unittest.TestCase):
    def test_decision_requires_rationale(self):
        with self.assertRaisesRegex(ValueError, "requires rationale"):
            base(rationale="").validate()

    def test_only_blockers_can_block_completion(self):
        with self.assertRaisesRegex(ValueError, "only blocker"):
            base(blocks_completion=True).validate()

    def test_core_rejects_unstructured_note(self):
        with self.assertRaisesRegex(ValueError, "not allowed in core"):
            base(role="note", rationale="").validate()

    def test_invalid_temporal_interval_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid_from"):
            base(valid_from="2026-02-02T00:00:00Z", valid_to="2026-01-01T00:00:00Z").validate()


if __name__ == "__main__":
    unittest.main()
