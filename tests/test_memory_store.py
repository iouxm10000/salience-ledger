from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from salience_ledger.models import MemoryRecord
from salience_ledger.store import Ledger, LedgerError


def record(identifier: str, role: str, **overrides) -> MemoryRecord:
    data = {
        "id": identifier,
        "title": identifier,
        "text": f"text for {identifier}",
        "memory_type": "semantic",
        "role": role,
        "authority": "user_explicit",
        "salience": "core" if role not in {"note", "source", "evidence"} else "working",
        "importance": 90,
    }
    data.update(overrides)
    return MemoryRecord(**data)


class LedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Ledger(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def append(self, item: MemoryRecord) -> str:
        source = self.store.observe(
            f"Source evidence for {item.id}", source_type="test_fixture", source_ref=self.id()
        )
        return self.store.append(replace(item, source_episode_ids=(source,)))

    def test_core_requires_provenance(self) -> None:
        with self.assertRaisesRegex(LedgerError, "requires source_episode_ids"):
            self.store.append(record("intent", "user_intent"))

    def test_core_source_cannot_be_another_derived_record(self) -> None:
        derived_episode = self.store.append(
            record(
                "archive", "note", authority="agent_inference", salience="archive"
            )
        )
        with self.assertRaisesRegex(LedgerError, "sources must be observations"):
            self.store.append(
                record("intent", "user_intent", source_episode_ids=(derived_episode,))
            )

    def test_core_survives_irrelevant_search(self) -> None:
        self.append(record("rule", "non_negotiable", text="Never erase rejected behavior."))
        self.store.append(
            record(
                "noise", "note", text="A very detailed discussion about bananas.",
                authority="agent_inference", salience="archive",
            )
        )
        self.store.build()
        context = self.store.recovery_context("bananas")
        self.assertLess(context.index("Never erase rejected behavior"), context.index("bananas"))

    def test_conflicting_assertion_fails_closed(self) -> None:
        self.append(
            record(
                "a", "accepted_decision", assertion_key="mode", value="safe",
                rationale="accepted", authority="engineering_decision",
            )
        )
        self.append(
            record(
                "b", "engineering_proposal", assertion_key="mode", value="fast",
                rationale="proposal", authority="agent_inference",
            )
        )
        with self.assertRaisesRegex(LedgerError, "unresolved assertion conflict"):
            self.store.build()

    def test_agent_proposal_cannot_force_user_to_reconfirm(self) -> None:
        self.append(record("user", "user_intent", assertion_key="mode", value="safe"))
        self.append(
            record(
                "proposal", "engineering_proposal", assertion_key="mode", value="fast",
                rationale="agent idea", authority="agent_inference",
            )
        )
        issues = self.store.audit()
        self.assertTrue(any("lower-authority record conflicts" in item for item in issues))
        self.assertFalse(any("USER_CONFIRMATION_REQUIRED" in item for item in issues))

    def test_permanent_rejection_cannot_be_revived(self) -> None:
        self.append(
            record(
                "reject", "permanent_rejection", assertion_key="auto_merge", value=False,
                rejection_reason="User rejected silent merging.",
            )
        )
        self.append(
            record(
                "proposal", "engineering_proposal", assertion_key="auto_merge", value=False,
                rationale="Try it again under another name.", authority="agent_inference",
            )
        )
        self.assertTrue(any("revives permanent rejection" in issue for issue in self.store.audit()))

    def test_completion_gate_preserves_counterexample(self) -> None:
        self.append(
            record(
                "regression", "counterexample", text="This case still fails after restart.",
                blocks_completion=True, authority="machine_result",
            )
        )
        self.assertEqual(self.store.audit(completion=False), [])
        self.assertTrue(any("completion blocked" in issue for issue in self.store.audit(completion=True)))

    def test_generation_is_stale_after_new_episode(self) -> None:
        self.append(record("intent", "user_intent"))
        first = self.store.build()
        self.store.verify_current()
        self.append(record("task", "current_task"))
        with self.assertRaisesRegex(LedgerError, "stale"):
            self.store.verify_current()
        second = self.store.build()
        self.assertNotEqual(first["generation_id"], second["generation_id"])
        self.store.verify_current()

    def test_episode_tamper_is_detected(self) -> None:
        self.append(record("intent", "user_intent"))
        text = self.store.events_path.read_text(encoding="utf-8").replace("text for intent", "tampered")
        self.store.events_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "hash mismatch"):
            self.store.records()

    def test_observation_tamper_is_detected(self) -> None:
        self.append(record("intent", "user_intent"))
        text = self.store.events_path.read_text(encoding="utf-8").replace("Source evidence", "Forged evidence")
        self.store.events_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "observation hash mismatch"):
            self.store.records()

    def test_projection_rejects_validly_rehashed_same_id_rewrite(self) -> None:
        self.append(record("intent", "user_intent"))
        episodes = [json.loads(line) for line in self.store.events_path.read_text(encoding="utf-8").splitlines()]
        original = next(item for item in episodes if item["event"] == "upsert")
        forged = dict(original)
        forged["episode_id"] = "ep-forged-rewrite"
        forged["record"] = dict(original["record"])
        forged["record"]["text"] = "newest wording silently wins"
        canonical = json.dumps(forged["record"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        forged["record_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self.store.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(forged, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaisesRegex(LedgerError, "immutable record"):
            self.store.records()

    def test_same_id_cannot_be_overwritten_by_latest_statement(self) -> None:
        self.append(record("intent", "user_intent"))
        with self.assertRaisesRegex(LedgerError, "immutable"):
            self.append(record("intent", "user_intent", text="updated"))

    def test_agent_cannot_claim_user_authority(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot impersonate"):
            record("fake", "user_intent", authority="agent_inference").validate()

    def test_manifest_detects_projection_tamper(self) -> None:
        self.append(record("intent", "user_intent"))
        self.store.build()
        pointer = json.loads(self.store.current_path.read_text(encoding="utf-8"))
        core = self.root / pointer["generation_path"] / "CORE_MEMORY.md"
        core.write_text("corrupt", encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "generation file hash mismatch"):
            self.store.verify_current()

    def test_expired_rule_does_not_conflict_or_recover(self) -> None:
        self.append(
            record(
                "expired", "accepted_decision", assertion_key="mode", value="old",
                rationale="historical", valid_to="2000-01-01T00:00:00Z",
            )
        )
        self.append(
            record("current", "accepted_decision", assertion_key="mode", value="new", rationale="current")
        )
        self.store.build()
        context = self.store.recovery_context()
        self.assertIn("current", context)
        self.assertNotIn("historical", context)

    def test_user_ambiguity_packet_preserves_both_wordings(self) -> None:
        self.append(
            record(
                "old-wording", "user_intent", text="Keep every historical line.",
                assertion_key="history.display", value="all",
            )
        )
        self.append(
            record(
                "new-wording", "user_intent", text="Hide historical lines by default.",
                assertion_key="history.display", value="hidden",
            )
        )
        packets = self.store.clarification_packets()
        self.assertEqual(packets[0]["status"], "USER_CONFIRMATION_REQUIRED")
        self.assertCountEqual(
            [item["text"] for item in packets[0]["statements"]],
            ["Keep every historical line.", "Hide historical lines by default."],
        )

    def test_user_ambiguity_requires_new_confirmation_episode(self) -> None:
        self.append(record("u1", "user_intent", assertion_key="mode", value="one"))
        self.append(record("u2", "user_intent", assertion_key="mode", value="two"))
        ordinary = self.store.observe("choose two", source_type="user_message")
        proposed = record(
            "confirmed", "user_intent", assertion_key="mode", value="two",
            source_episode_ids=(ordinary,),
        )
        with self.assertRaisesRegex(LedgerError, "user_confirmation"):
            self.store.resolve_user_ambiguity(["u1", "u2"], proposed)

        confirmation = self.store.observe(
            "I confirm the second meaning supersedes the first.", source_type="user_confirmation"
        )
        confirmed = replace(proposed, source_episode_ids=(confirmation,))
        self.store.resolve_user_ambiguity(["u1", "u2"], confirmed)
        self.assertEqual(self.store.audit(), [])
        projected = self.store.records()
        self.assertEqual(projected["u1"].status, "superseded")
        self.assertEqual(projected["u2"].status, "superseded")
        self.assertEqual(projected["confirmed"].status, "active")


if __name__ == "__main__":
    unittest.main()
