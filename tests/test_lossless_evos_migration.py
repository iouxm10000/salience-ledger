import hashlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from salience_ledger.models import MemoryRecord
from salience_ledger.migration.evos_v2 import EvosV2Migrator
from salience_ledger.store import Ledger, LedgerError


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LosslessEvosMigrationTests(unittest.TestCase):
    def make_legacy_project(self, root: Path) -> Path:
        project = root / "legacy-project"
        generation = project / "evos_memory/index/generations/gen-test"
        generation.mkdir(parents=True)
        (project / "evos_memory/seeds").mkdir(parents=True)
        (project / "evos_memory/thread_memories").mkdir(parents=True)
        (project / "tools").mkdir(parents=True)
        (project / "docs").mkdir(parents=True)
        (project / "AGENTS.md").write_text("memory first\n", encoding="utf-8")
        (project / "tools/evos_memory.py").write_text("# legacy builder\n", encoding="utf-8")
        (project / "evos_memory/seeds/a.jsonl").write_text('{"seed":1}\n', encoding="utf-8")
        (project / "evos_memory/thread_memories/thread.md").write_text("thread\n", encoding="utf-8")
        (project / "docs/source.md").write_text("accepted source\n", encoding="utf-8")

        db = generation / "memory.sqlite"
        connection = sqlite3.connect(db)
        connection.executescript(
            """
            CREATE TABLE memories (
              id TEXT PRIMARY KEY, kind TEXT, title TEXT, summary TEXT, body TEXT,
              tags TEXT, status TEXT, priority INTEGER, confidence REAL, source TEXT,
              created_at TEXT, updated_at TEXT, task_id TEXT, thread_id TEXT,
              thread_memory_file TEXT, superseded_by TEXT, lifecycle_reason TEXT,
              is_current_task INTEGER
            );
            """
        )
        rows = [
            ("task-1", "task", "Current", "summary", "body", "task", "active", 100, 1.0,
             "evos_memory/thread_memories/thread.md", "2026-01-01", "2026-01-01", "T1", "thread",
             "evos_memory/thread_memories/thread.md", "", "active", 1),
            ("constraint-1", "constraint", "Rule", "do not lose", "body", "rule", "active", 95, 1.0,
             "docs/source.md", "2026-01-01", "2026-01-01", "", "", "", "", "", 0),
            ("note-1", "note", "Note", "archive", "body", "note", "active", 20, 0.5,
             "", "2026-01-01", "2026-01-01", "", "", "", "", "", 0),
        ]
        connection.executemany("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        connection.commit()
        connection.close()
        context = generation / "EVOS_CONTEXT.md"
        context.write_text("legacy context\n", encoding="utf-8")
        task_state = project / "evos_memory/task_state.json"
        task_state.write_text(json.dumps({"current_task": {"memory_id": "task-1"}}), encoding="utf-8")
        manifest = {
            "generation_id": "gen-test",
            "source_hash": "abc123",
            "task_state_sha256": sha256(task_state),
            "files": {
                "database": {"path": "memory.sqlite", "sha256": sha256(db)},
                "context": {"path": "EVOS_CONTEXT.md", "sha256": sha256(context)},
            },
        }
        (generation / "build_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        current = project / "evos_memory/index/CURRENT.json"
        current.write_text(json.dumps({
            "generation_path": "evos_memory/index/generations/gen-test"
        }), encoding="utf-8")
        return project

    def test_snapshot_is_one_to_one_idempotent_and_cutover_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_legacy_project(root)
            ledger = Ledger(root / "new-project")
            migrator = EvosV2Migrator(ledger, project)

            manifest = migrator.migrate()
            self.assertEqual(manifest["legacy_row_count"], 3)
            self.assertEqual(len(manifest["row_mappings"]), 3)
            self.assertEqual(len({item["ledger_id"] for item in manifest["row_mappings"]}), 3)
            report = migrator.verify(manifest["migration_id"], require_current=True)
            self.assertEqual(report["evidence_parity"], "PASS")
            self.assertEqual(report["semantic_parity"], "PENDING")
            self.assertFalse(report["cutover_ready"])
            self.assertEqual(report["pending_governing_count"], 2)

            episode_count = len(ledger.episode_ids())
            self.assertEqual(migrator.migrate(), manifest)
            self.assertEqual(len(ledger.episode_ids()), episode_count)

            migration_dir = ledger.state / "migrations" / manifest["migration_id"]
            export_rows = [
                json.loads(line)
                for line in (migration_dir / "evos_rows.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual({row["id"] for row in export_rows}, {"task-1", "constraint-1", "note-1"})

            current_review = ledger.observe(
                "Coordinator confirms the migrated current task.",
                source_type="coordinator_review",
            )
            with self.assertRaises(LedgerError):
                migrator.review(
                    manifest["migration_id"], "task-1", "ARCHIVE_PRESERVED", current_review
                )
            current_mapping = next(
                item for item in manifest["row_mappings"] if item["legacy_id"] == "task-1"
            )
            ledger.append(MemoryRecord(
                id="promoted-current",
                title="Current",
                text="Reviewed current task",
                memory_type="semantic",
                role="current_task",
                authority="engineering_decision",
                salience="core",
                source_episode_ids=(current_mapping["source_episode_id"], current_review),
                must_read=True,
            ))
            migrator.review(
                manifest["migration_id"], "task-1", "PROMOTED", current_review, "promoted-current"
            )
            constraint_review = ledger.observe(
                "Constraint retained only as archive evidence.",
                source_type="coordinator_review",
            )
            migrator.review(
                manifest["migration_id"], "constraint-1", "ARCHIVE_PRESERVED", constraint_review
            )
            final_report = migrator.verify(manifest["migration_id"], require_current=True)
            self.assertEqual(final_report["semantic_parity"], "PASS")
            self.assertTrue(final_report["cutover_ready"])

    def test_tampering_and_generation_advance_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = self.make_legacy_project(root)
            ledger = Ledger(root / "new-project")
            migrator = EvosV2Migrator(ledger, project)
            manifest = migrator.migrate()
            migration_dir = ledger.state / "migrations" / manifest["migration_id"]

            copied = migration_dir / manifest["source_files"][0]["bundle_path"]
            copied.write_bytes(copied.read_bytes() + b"tamper")
            self.assertEqual(migrator.verify(manifest["migration_id"])["evidence_parity"], "FAIL")

            copied.write_bytes(
                (project / manifest["source_files"][0]["source_path"]).read_bytes()
            )
            current = project / "evos_memory/index/CURRENT.json"
            old_generation = project / "evos_memory/index/generations/gen-test"
            new_generation = project / "evos_memory/index/generations/gen-rebuild"
            shutil.copytree(old_generation, new_generation)
            rebuilt_manifest_path = new_generation / "build_manifest.json"
            rebuilt_manifest = json.loads(rebuilt_manifest_path.read_text(encoding="utf-8"))
            rebuilt_manifest["generation_id"] = "gen-rebuild"
            rebuilt_manifest_path.write_text(json.dumps(rebuilt_manifest), encoding="utf-8")
            current.write_text(json.dumps({
                "generation_path": "evos_memory/index/generations/gen-rebuild"
            }), encoding="utf-8")
            rebuild_report = migrator.verify(manifest["migration_id"], require_current=True)
            self.assertEqual(rebuild_report["evidence_parity"], "PASS")
            self.assertFalse(rebuild_report["cutover_issues"])
            episode_count = len(ledger.episode_ids())
            rebuilt_snapshot = migrator.migrate()
            self.assertNotEqual(rebuilt_snapshot["migration_id"], manifest["migration_id"])
            self.assertEqual(len(ledger.records()), 3)
            self.assertEqual(len(ledger.episode_ids()), episode_count)

            changed_generation = project / "evos_memory/index/generations/gen-changed"
            shutil.copytree(new_generation, changed_generation)
            changed_db = changed_generation / "memory.sqlite"
            connection = sqlite3.connect(changed_db)
            connection.execute("UPDATE memories SET body='changed body' WHERE id='note-1'")
            connection.commit()
            connection.close()
            changed_manifest_path = changed_generation / "build_manifest.json"
            changed_manifest = json.loads(changed_manifest_path.read_text(encoding="utf-8"))
            changed_manifest["generation_id"] = "gen-changed"
            changed_manifest["source_hash"] = "changed-source"
            changed_manifest["files"]["database"]["sha256"] = sha256(changed_db)
            changed_manifest_path.write_text(json.dumps(changed_manifest), encoding="utf-8")
            current.write_text(json.dumps({
                "generation_path": "evos_memory/index/generations/gen-changed"
            }), encoding="utf-8")
            changed_snapshot = migrator.migrate()
            old_note_id = next(
                item["ledger_id"] for item in manifest["row_mappings"] if item["legacy_id"] == "note-1"
            )
            new_note_id = next(
                item["ledger_id"] for item in changed_snapshot["row_mappings"]
                if item["legacy_id"] == "note-1"
            )
            self.assertNotEqual(old_note_id, new_note_id)
            self.assertEqual(ledger.records()[old_note_id].status, "archived")
            self.assertEqual(ledger.records()[new_note_id].status, "active")

            current.write_text(json.dumps({
                "generation_path": "evos_memory/index/generations/gen-new"
            }), encoding="utf-8")
            report = migrator.verify(manifest["migration_id"], require_current=True)
            self.assertEqual(report["evidence_parity"], "PASS")
            self.assertTrue(report["cutover_issues"])
            self.assertFalse(report["cutover_ready"])


if __name__ == "__main__":
    unittest.main()
