import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from salience_ledger.models import MemoryRecord
from salience_ledger.store import Ledger, LedgerError
from salience_ledger.task_runs import TaskRun


class TaskRunTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.ledger = Ledger(self.root)
        source = self.ledger.observe(
            "Never silently choose the newest ambiguous user meaning.",
            source_type="user_message",
        )
        self.ledger.append(
            MemoryRecord(
                id="ambiguity-gate",
                title="Ask the user about ambiguity",
                text="Show both original statements and require explicit confirmation.",
                memory_type="procedural",
                role="non_negotiable",
                authority="user_explicit",
                salience="core",
                importance=100,
                source_episode_ids=(source,),
            )
        )
        self.ledger.build()
        self.run = TaskRun(self.ledger, "long-audit")
        self.run.initialize(
            goal="Implement one verified memory feature without drift.",
            gates=["unit", "privacy"],
            completion_criteria=["production path exists", "restart parity passes"],
            red_lines=["no push", "no silent user-semantic resolution"],
            converge_every=3,
            large_change_lines=10,
        )

    def tearDown(self):
        self.temporary.cleanup()

    def register(self, item_id="item-1"):
        return self.run.append(
            actor="executor",
            event_type="item_registered",
            payload={"item_id": item_id, "title": "Implement feature", "acceptance": "unit passes"},
        )

    def close_round(self, round_number, item_id="item-1", *, net_lines=1, mode="implement"):
        return self.run.append(
            actor="executor",
            event_type="round_closed",
            payload={
                "round": round_number,
                "item_id": item_id,
                "result": "completed",
                "evidence": ["tests/test_task_runs.py::pass"],
                "gates": {"unit": "pass", "privacy": "pass"},
                "mode": mode,
                "net_lines": net_lines,
            },
        )

    def test_event_chain_and_contract_tamper_are_detected(self):
        self.register()
        events = self.run.events_path.read_text(encoding="utf-8")
        self.run.events_path.write_text(events.replace("Implement feature", "Pretend done"), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "hash mismatch"):
            self.run.events()

        self.run.events_path.write_text(events, encoding="utf-8")
        contract = json.loads(self.run.contract_path.read_text(encoding="utf-8"))
        contract["goal"] = "silently changed"
        self.run.contract_path.write_text(json.dumps(contract), encoding="utf-8")
        with self.assertRaisesRegex(LedgerError, "contract hash mismatch"):
            self.run.contract()

    def test_executor_cannot_write_supervisor_directive(self):
        with self.assertRaisesRegex(LedgerError, "cannot append"):
            self.run.append(
                actor="executor",
                event_type="directive_added",
                payload={
                    "directive_id": "dir-1",
                    "problem": "drift",
                    "required_action": "return to contract",
                    "priority": "P1",
                },
            )

    def test_round_is_one_item_and_exact_gate_set(self):
        self.register()
        with self.assertRaisesRegex(LedgerError, "exactly match"):
            self.run.append(
                actor="executor",
                event_type="round_closed",
                payload={
                    "round": 1,
                    "item_id": "item-1",
                    "result": "completed",
                    "evidence": ["unit"],
                    "gates": {"unit": "pass"},
                    "mode": "implement",
                    "net_lines": 1,
                },
            )
        self.close_round(1)
        with self.assertRaisesRegex(LedgerError, "one open item"):
            self.close_round(2)

    def test_completed_item_requires_green_gates(self):
        self.register()
        with self.assertRaisesRegex(LedgerError, "every contract gate to pass"):
            self.run.append(
                actor="executor",
                event_type="round_closed",
                payload={
                    "round": 1,
                    "item_id": "item-1",
                    "result": "completed",
                    "evidence": ["unit failed"],
                    "gates": {"unit": "fail", "privacy": "pass"},
                    "mode": "implement",
                    "net_lines": 1,
                },
            )

    def test_large_round_forces_next_round_to_converge(self):
        self.register("item-1")
        self.close_round(1, "item-1", net_lines=11)
        self.register("item-2")
        with self.assertRaisesRegex(LedgerError, "forced convergence"):
            self.close_round(2, "item-2", net_lines=1, mode="implement")
        self.close_round(2, "item-2", net_lines=-2, mode="converge")

    def test_context_rehydrates_memory_run_and_supervisor_directive(self):
        self.register()
        self.run.append(
            actor="supervisor",
            event_type="directive_added",
            payload={
                "directive_id": "dir-1",
                "problem": "test has no production call site",
                "required_action": "bind the test to the real command",
                "priority": "P1",
            },
        )
        context = self.run.render_context()
        self.assertIn("Ask the user about ambiguity", context)
        self.assertIn("item-1", context)
        self.assertIn("test has no production call site", context)

    def test_context_retains_completed_items_beyond_latest_rounds(self):
        for number in range(1, 4):
            item_id = f"item-{number}"
            self.register(item_id)
            self.close_round(
                number,
                item_id,
                net_lines=-1 if number == 3 else 1,
                mode="converge" if number == 3 else "implement",
            )
        context = self.run.render_context()
        self.assertIn("[item-1] round 1", context)
        self.assertIn("[item-3] round 3", context)

    def test_parallel_supervisor_events_remain_one_hash_chain(self):
        def add(number):
            self.run.append(
                actor="supervisor",
                event_type="directive_added",
                payload={
                    "directive_id": f"dir-{number}",
                    "problem": f"problem {number}",
                    "required_action": f"fix {number}",
                    "priority": "P2",
                },
            )

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(add, range(8)))
        events = self.run.events()
        self.assertEqual([event["sequence"] for event in events], list(range(1, 9)))
        self.assertEqual(len({event["event_sha256"] for event in events}), 8)

    def test_memory_ambiguity_blocks_task_context(self):
        for record_id, value, wording in (
            ("meaning-a", "show", "Show every historical line."),
            ("meaning-b", "hide", "Hide historical lines by default."),
        ):
            source = self.ledger.observe(wording, source_type="user_message")
            self.ledger.append(
                MemoryRecord(
                    id=record_id,
                    title=wording,
                    text=wording,
                    memory_type="semantic",
                    role="user_intent",
                    authority="user_explicit",
                    salience="core",
                    assertion_key="history.display",
                    value=value,
                    source_episode_ids=(source,),
                )
            )
        with self.assertRaisesRegex(LedgerError, "USER_CONFIRMATION_REQUIRED"):
            self.run.render_context()

    def test_completion_requires_closed_work_and_latest_gates(self):
        self.register()
        with self.assertRaisesRegex(LedgerError, "open item"):
            self.run.append(
                actor="executor",
                event_type="completion_claimed",
                payload={
                    "evidence": ["none"],
                    "criteria": {
                        "production path exists": ["none"],
                        "restart parity passes": ["none"],
                    },
                },
            )
        self.close_round(1)
        with self.assertRaisesRegex(LedgerError, "exactly match"):
            self.run.append(
                actor="executor",
                event_type="completion_claimed",
                payload={"evidence": ["unit"], "criteria": {"production path exists": ["unit"]}},
            )
        criteria = {
            "production path exists": ["production call site"],
            "restart parity passes": ["restart test"],
        }
        self.run.append(
            actor="executor",
            event_type="completion_claimed",
            payload={"evidence": ["unit", "privacy"], "criteria": criteria},
        )
        self.run.append(
            actor="supervisor",
            event_type="completion_accepted",
            payload={"evidence": ["cold audit"], "criteria": criteria},
        )
        self.assertEqual(self.run.project()["completion"], "accepted")
        with self.assertRaisesRegex(LedgerError, "closed to further events"):
            self.run.append(
                actor="executor",
                event_type="item_registered",
                payload={"item_id": "late", "title": "late", "acceptance": "never"},
            )

    def test_owner_decision_requires_blocker_specific_confirmation(self):
        self.run.append(
            actor="executor",
            event_type="owner_blocked",
            payload={
                "blocker_id": "choice-1",
                "question": "Which interpretation governs?",
                "options": ["first", "second"],
            },
        )
        unrelated = self.ledger.observe(
            "Confirm something else.", source_type="user_confirmation", source_ref="unrelated"
        )
        with self.assertRaisesRegex(LedgerError, "blocker-specific"):
            self.run.append(
                actor="owner",
                event_type="owner_decision",
                payload={
                    "blocker_id": "choice-1",
                    "decision": "first",
                    "source_episode_ids": [unrelated],
                },
            )
        confirmation = self.ledger.observe(
            "Choose first.",
            source_type="user_confirmation",
            source_ref="task-run/long-audit/blocker/choice-1",
        )
        self.run.append(
            actor="owner",
            event_type="owner_decision",
            payload={
                "blocker_id": "choice-1",
                "decision": "first",
                "source_episode_ids": [confirmation],
            },
        )


if __name__ == "__main__":
    unittest.main()
