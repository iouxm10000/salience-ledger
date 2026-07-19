from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from ..models import MemoryRecord, utc_now
from ..store import Ledger, LedgerError


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


REVIEW_DISPOSITIONS = {"ARCHIVE_PRESERVED", "REJECTED", "PROMOTED"}
REVIEW_SOURCE_TYPES = {"user_confirmation", "accepted_document_review", "coordinator_review"}


class EvosV2Migrator:
    """Snapshot one immutable EvoS v2 generation without semantic promotion."""

    def __init__(self, ledger: Ledger, project_root: str | Path):
        self.ledger = ledger
        self.project_root = Path(project_root).resolve()
        self.evos_root = self.project_root / "evos_memory"

    def migrate(self) -> dict[str, Any]:
        source = self._resolve_source()
        migration_id = f"evos-v2-{source['generation_id']}-{source['source_hash'][:12]}"
        final_dir = self.ledger.state / "migrations" / migration_id
        manifest_path = final_dir / "migration_manifest.json"
        if manifest_path.exists():
            report = self.verify(migration_id)
            if report["evidence_parity"] != "PASS":
                raise LedgerError("existing migration failed verification")
            return _read_json(manifest_path)

        rows = self._read_rows(source["database_path"])
        if source["memory_count"] is not None and source["memory_count"] != len(rows):
            raise LedgerError("legacy manifest memory_count does not match immutable database")
        current_memory_id = _read_json(source["task_state_path"])["current_task"]["memory_id"]
        if sum(str(row["id"]) == current_memory_id for row in rows) != 1:
            raise LedgerError("legacy current task is not present exactly once in immutable database")
        row_mappings = self._import_rows(rows, source["generation_id"])
        source_files = self._source_files(source, rows)

        final_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{migration_id}-", dir=final_dir.parent))
        try:
            bundle_root = temporary / "source_bundle"
            file_manifest = self._copy_sources(source_files, bundle_root)
            export_path = temporary / "evos_rows.jsonl"
            with export_path.open("w", encoding="utf-8", newline="\n") as handle:
                for row in rows:
                    handle.write(_canonical(row) + "\n")
            queue = self._promotion_queue(rows, row_mappings)
            _write_json(temporary / "promotion_queue.json", queue)
            task_state = _read_json(source["task_state_path"])
            manifest = {
                "schema_version": "salience_evos_v2_migration.v1",
                "migration_id": migration_id,
                "legacy_generation_id": source["generation_id"],
                "legacy_source_hash": source["source_hash"],
                "legacy_database_sha256": _sha_file(source["database_path"]),
                "legacy_row_count": len(rows),
                "legacy_current_task_memory_id": task_state["current_task"]["memory_id"],
                "source_files": file_manifest,
                "row_export": {
                    "path": "evos_rows.jsonl",
                    "sha256": _sha_file(export_path),
                    "line_count": len(rows),
                },
                "row_mappings": row_mappings,
                "promotion_queue": {
                    "path": "promotion_queue.json",
                    "sha256": _sha_file(temporary / "promotion_queue.json"),
                    "item_count": len(queue["items"]),
                    "pending_governing_count": queue["pending_governing_count"],
                },
                "authority": "legacy evidence preserved; semantic promotion not performed",
                "cutover_ready": False,
            }
            _write_json(temporary / "migration_manifest.json", manifest)
            _write_json(temporary / "migration_receipt.json", {
                "schema_version": "salience_migration_receipt.v1",
                "migration_id": migration_id,
                "manifest_sha256": _sha_file(temporary / "migration_manifest.json"),
            })
            os.replace(temporary, final_dir)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        self._archive_superseded_row_versions(migration_id, row_mappings)
        report = self.verify(migration_id)
        if report["evidence_parity"] != "PASS":
            raise LedgerError("new migration failed evidence parity")
        return _read_json(manifest_path)

    def review(
        self,
        migration_id: str,
        legacy_id: str,
        disposition: str,
        review_episode_id: str,
        promoted_record_id: str | None = None,
    ) -> dict[str, Any]:
        """Append one source-backed semantic disposition; never rewrite the frozen queue."""
        if disposition not in REVIEW_DISPOSITIONS:
            raise LedgerError(f"invalid migration disposition: {disposition}")
        migration_dir = self.ledger.state / "migrations" / migration_id
        manifest = _read_json(migration_dir / "migration_manifest.json")
        queue = _read_json(migration_dir / manifest["promotion_queue"]["path"])
        queue_items = {str(item["legacy_id"]): item for item in queue["items"]}
        item = queue_items.get(legacy_id)
        if item is None:
            raise LedgerError(f"unknown legacy migration id: {legacy_id}")
        if item["disposition"] != "PENDING_REVIEW":
            raise LedgerError(f"legacy id is archival and does not require governing review: {legacy_id}")
        if item["is_current_task"] and disposition != "PROMOTED":
            raise LedgerError("legacy current task must be explicitly promoted before cutover")

        observations = self.ledger.observations()
        review_episode = observations.get(review_episode_id)
        if review_episode is None or review_episode.get("source_type") not in REVIEW_SOURCE_TYPES:
            raise LedgerError(
                "migration review requires an observation sourced as user_confirmation, "
                "accepted_document_review, or coordinator_review"
            )
        existing = self._review_dispositions(migration_dir)
        if legacy_id in existing:
            raise LedgerError(f"legacy id already has an immutable review disposition: {legacy_id}")

        mapping = next(
            value for value in manifest["row_mappings"] if value["legacy_id"] == legacy_id
        )
        if disposition == "PROMOTED":
            if not promoted_record_id:
                raise LedgerError("PROMOTED disposition requires promoted_record_id")
            promoted = self.ledger.records().get(promoted_record_id)
            if promoted is None or promoted.status not in {"active", "accepted"}:
                raise LedgerError("promoted record is missing or inactive")
            if promoted.salience == "archive" or promoted.role == "note":
                raise LedgerError("promoted record must be typed governing/working memory")
            if item["recommended_role"] != "note" and promoted.role != item["recommended_role"]:
                raise LedgerError(
                    f"promoted record role must be {item['recommended_role']} for this legacy item"
                )
            if promoted.authority == "user_explicit" and review_episode["source_type"] != "user_confirmation":
                raise LedgerError("user_explicit promotion requires user_confirmation evidence")
            if (
                promoted.authority == "accepted_document"
                and review_episode["source_type"] != "accepted_document_review"
            ):
                raise LedgerError("accepted_document promotion requires accepted_document_review evidence")
            required_sources = {mapping["source_episode_id"], review_episode_id}
            if not required_sources.issubset(promoted.source_episode_ids):
                raise LedgerError(
                    "promoted record must cite both the exact legacy row and the new review episode"
                )
        elif promoted_record_id:
            raise LedgerError(f"{disposition} disposition must not name a promoted record")

        payload = {
            "schema_version": "salience_migration_review.v1",
            "migration_id": migration_id,
            "legacy_id": legacy_id,
            "disposition": disposition,
            "review_episode_id": review_episode_id,
            "promoted_record_id": promoted_record_id,
            "recorded_at": utc_now(),
        }
        payload["event_sha256"] = _sha_bytes(_canonical(payload).encode("utf-8"))
        journal = migration_dir / "review_dispositions.jsonl"
        with journal.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical(payload) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return payload

    def verify(self, migration_id: str, *, require_current: bool = False) -> dict[str, Any]:
        migration_dir = self.ledger.state / "migrations" / migration_id
        manifest_path = migration_dir / "migration_manifest.json"
        receipt_path = migration_dir / "migration_receipt.json"
        manifest = _read_json(manifest_path)
        evidence_issues: list[str] = []
        semantic_issues: list[str] = []
        cutover_issues: list[str] = []
        if not receipt_path.exists():
            evidence_issues.append("migration receipt missing")
        else:
            receipt = _read_json(receipt_path)
            if receipt.get("migration_id") != migration_id:
                evidence_issues.append("migration receipt identity mismatch")
            if receipt.get("manifest_sha256") != _sha_file(manifest_path):
                evidence_issues.append("migration manifest hash mismatch")
        for item in manifest["source_files"]:
            copied = migration_dir / item["bundle_path"]
            if not copied.exists() or _sha_file(copied) != item["sha256"]:
                evidence_issues.append(f"source bundle mismatch: {item['source_path']}")

        export_path = migration_dir / manifest["row_export"]["path"]
        if not export_path.exists() or _sha_file(export_path) != manifest["row_export"]["sha256"]:
            evidence_issues.append("row export hash mismatch")
            exported_rows: list[dict[str, Any]] = []
        else:
            exported_rows = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines() if line]
        if len(exported_rows) != manifest["legacy_row_count"]:
            evidence_issues.append("row export count mismatch")

        mappings = manifest["row_mappings"]
        if len(mappings) != manifest["legacy_row_count"]:
            evidence_issues.append("row mapping count mismatch")
        mapped_legacy_ids = [item["legacy_id"] for item in mappings]
        if len(set(mapped_legacy_ids)) != len(mapped_legacy_ids):
            evidence_issues.append("legacy row id mapping is not one-to-one")

        records = self.ledger.records()
        observations = self.ledger.observations()
        exported_by_id = {str(row["id"]): row for row in exported_rows}
        for mapping in mappings:
            row = exported_by_id.get(mapping["legacy_id"])
            if row is None or _sha_bytes(_canonical(row).encode("utf-8")) != mapping["row_sha256"]:
                evidence_issues.append(f"row mapping hash mismatch: {mapping['legacy_id']}")
                continue
            record = records.get(mapping["ledger_id"])
            observation = observations.get(mapping["source_episode_id"])
            if record is None:
                evidence_issues.append(f"missing ledger archive record: {mapping['ledger_id']}")
            if observation is None or observation.get("content") != _canonical(row):
                evidence_issues.append(f"missing exact row observation: {mapping['legacy_id']}")

        queue_path = migration_dir / manifest["promotion_queue"]["path"]
        if not queue_path.exists() or _sha_file(queue_path) != manifest["promotion_queue"]["sha256"]:
            evidence_issues.append("promotion queue hash mismatch")
            pending = manifest["promotion_queue"]["pending_governing_count"]
        else:
            queue = _read_json(queue_path)
            pending_ids = {
                str(item["legacy_id"])
                for item in queue["items"] if item["disposition"] == "PENDING_REVIEW"
            }
            try:
                dispositions = self._review_dispositions(migration_dir, manifest=manifest)
            except LedgerError as exc:
                semantic_issues.append(str(exc))
                dispositions = {}
            unexpected = sorted(set(dispositions) - pending_ids)
            if unexpected:
                semantic_issues.append(
                    "review disposition targets non-pending legacy ids: " + ", ".join(unexpected)
                )
            pending = len(pending_ids - set(dispositions))

        current_id = manifest["legacy_current_task_memory_id"]
        current_mappings = [item for item in mappings if item["legacy_id"] == current_id]
        if len(current_mappings) != 1:
            evidence_issues.append("legacy current task is not mapped exactly once")
        if pending == 0:
            active_current = [
                record for record in self.ledger.records().values()
                if record.status in {"active", "accepted"} and record.role == "current_task"
            ]
            if len(active_current) != 1:
                semantic_issues.append(
                    "semantic cutover requires exactly one active promoted current_task"
                )

        if require_current:
            try:
                current_source = self._resolve_source()
            except (OSError, KeyError, ValueError, json.JSONDecodeError, LedgerError) as exc:
                cutover_issues.append(f"legacy authority cannot be revalidated: {exc}")
            else:
                if current_source["source_hash"] != manifest["legacy_source_hash"]:
                    cutover_issues.append("legacy CURRENT source identity changed")
                if _sha_file(current_source["database_path"]) != manifest["legacy_database_sha256"]:
                    cutover_issues.append("legacy CURRENT database identity changed")
                frozen_task_state = next(
                    (
                        item["sha256"] for item in manifest["source_files"]
                        if item["source_path"] == "evos_memory/task_state.json"
                    ),
                    None,
                )
                if frozen_task_state is None or _sha_file(current_source["task_state_path"]) != frozen_task_state:
                    cutover_issues.append("legacy CURRENT task-state identity changed")

        if evidence_issues or semantic_issues:
            semantic_parity = "FAIL"
        elif pending:
            semantic_parity = "PENDING"
        else:
            semantic_parity = "PASS"
        all_issues = sorted(set(evidence_issues + semantic_issues + cutover_issues))

        return {
            "schema_version": "salience_migration_parity.v1",
            "migration_id": migration_id,
            "reviewed_legacy_generation_id": manifest["legacy_generation_id"],
            "observed_current_generation_id": (
                current_source["generation_id"] if require_current and "current_source" in locals() else None
            ),
            "evidence_parity": "PASS" if not evidence_issues else "FAIL",
            "semantic_parity": semantic_parity,
            "cutover_ready": (
                require_current and not evidence_issues and not semantic_issues
                and not cutover_issues and pending == 0
            ),
            "legacy_row_count": manifest["legacy_row_count"],
            "mapped_row_count": len(mappings),
            "pending_governing_count": pending,
            "evidence_issues": sorted(set(evidence_issues)),
            "semantic_issues": sorted(set(semantic_issues)),
            "cutover_issues": sorted(set(cutover_issues)),
            "issues": all_issues,
        }

    def _resolve_source(self) -> dict[str, Any]:
        pointer_path = self.evos_root / "index" / "CURRENT.json"
        task_state_path = self.evos_root / "task_state.json"
        pointer = _read_json(pointer_path)
        generation_path = (self.project_root / pointer["generation_path"]).resolve()
        try:
            generation_path.relative_to(self.project_root)
        except ValueError as exc:
            raise LedgerError("legacy generation path escapes project root") from exc
        manifest_path = generation_path / "build_manifest.json"
        manifest = _read_json(manifest_path)
        if pointer.get("generation_id") not in (None, manifest["generation_id"]):
            raise LedgerError("legacy pointer and manifest generation identities disagree")
        database_path = (generation_path / manifest["files"]["database"]["path"]).resolve()
        context_path = (generation_path / manifest["files"]["context"]["path"]).resolve()
        for path in (database_path, context_path):
            try:
                path.relative_to(generation_path)
            except ValueError as exc:
                raise LedgerError("legacy generation file escapes generation directory") from exc
        for path, expected in (
            (database_path, manifest["files"]["database"]["sha256"]),
            (context_path, manifest["files"]["context"]["sha256"]),
        ):
            if _sha_file(path) != expected:
                raise LedgerError(f"legacy generation hash mismatch: {path.name}")
        if _sha_file(task_state_path) != manifest["task_state_sha256"]:
            raise LedgerError("legacy task_state hash mismatch")
        return {
            "generation_id": manifest["generation_id"],
            "source_hash": manifest["source_hash"],
            "generation_path": generation_path,
            "database_path": database_path,
            "context_path": context_path,
            "manifest_path": manifest_path,
            "pointer_path": pointer_path,
            "task_state_path": task_state_path,
            "memory_count": manifest.get("memory_count"),
        }

    def _review_dispositions(
        self, migration_dir: Path, *, manifest: dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        journal = migration_dir / "review_dispositions.jsonl"
        if not journal.exists():
            return {}
        if manifest is None:
            manifest = _read_json(migration_dir / "migration_manifest.json")
        observations = self.ledger.observations()
        records = self.ledger.records()
        mappings = {item["legacy_id"]: item for item in manifest["row_mappings"]}
        queue = _read_json(migration_dir / manifest["promotion_queue"]["path"])
        queue_items = {str(item["legacy_id"]): item for item in queue["items"]}
        result: dict[str, dict[str, Any]] = {}
        for line_number, raw in enumerate(journal.read_text(encoding="utf-8").splitlines(), 1):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
                event_hash = event.pop("event_sha256")
                if event_hash != _sha_bytes(_canonical(event).encode("utf-8")):
                    raise LedgerError(f"migration review hash mismatch at line {line_number}")
                legacy_id = str(event["legacy_id"])
                if event["migration_id"] != manifest["migration_id"]:
                    raise LedgerError(f"migration review identity mismatch at line {line_number}")
                if legacy_id in result:
                    raise LedgerError(f"duplicate migration review for {legacy_id}")
                if event["disposition"] not in REVIEW_DISPOSITIONS:
                    raise LedgerError(f"invalid migration review disposition at line {line_number}")
                item = queue_items.get(legacy_id)
                if item is None:
                    raise LedgerError(f"unknown migration review target at line {line_number}")
                if item["is_current_task"] and event["disposition"] != "PROMOTED":
                    raise LedgerError(f"current task was not promoted at line {line_number}")
                review_episode = observations.get(event["review_episode_id"])
                if review_episode is None or review_episode.get("source_type") not in REVIEW_SOURCE_TYPES:
                    raise LedgerError(f"invalid migration review source at line {line_number}")
                if event["disposition"] == "PROMOTED":
                    promoted = records.get(event.get("promoted_record_id"))
                    mapping = mappings.get(legacy_id)
                    if promoted is None or mapping is None:
                        raise LedgerError(f"missing promoted migration evidence at line {line_number}")
                    required = {mapping["source_episode_id"], event["review_episode_id"]}
                    if promoted.salience == "archive" or promoted.role == "note" or not required.issubset(
                        promoted.source_episode_ids
                    ):
                        raise LedgerError(f"invalid promoted migration record at line {line_number}")
                    if item["recommended_role"] != "note" and promoted.role != item["recommended_role"]:
                        raise LedgerError(f"wrong promoted migration role at line {line_number}")
                    if promoted.authority == "user_explicit" and review_episode["source_type"] != "user_confirmation":
                        raise LedgerError(f"invalid user promotion source at line {line_number}")
                    if promoted.authority == "accepted_document" and review_episode["source_type"] != "accepted_document_review":
                        raise LedgerError(f"invalid document promotion source at line {line_number}")
                elif event.get("promoted_record_id"):
                    raise LedgerError(f"non-promotion names a promoted record at line {line_number}")
                result[legacy_id] = {**event, "event_sha256": event_hash}
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise LedgerError(f"invalid migration review at line {line_number}: {exc}") from exc
        return result

    def _archive_superseded_row_versions(
        self, migration_id: str, current_mappings: list[dict[str, str]]
    ) -> None:
        """Keep changed legacy versions as evidence but remove them from active retrieval."""
        current = {item["legacy_id"]: item["ledger_id"] for item in current_mappings}
        records = self.ledger.records()
        migrations_root = self.ledger.state / "migrations"
        for path in sorted(migrations_root.glob("*/migration_manifest.json")):
            prior = _read_json(path)
            if prior.get("migration_id") == migration_id:
                continue
            for mapping in prior.get("row_mappings", []):
                legacy_id = str(mapping.get("legacy_id"))
                old_ledger_id = str(mapping.get("ledger_id"))
                if current.get(legacy_id) in (None, old_ledger_id):
                    continue
                record = records.get(old_ledger_id)
                if record is not None and record.status in {"active", "accepted"}:
                    self.ledger.append(
                        replace(record, status="archived"),
                        event="migration_row_version_archived",
                    )
                    records[old_ledger_id] = replace(record, status="archived")

    @staticmethod
    def _read_rows(database_path: Path) -> list[dict[str, Any]]:
        uri = database_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in connection.execute("SELECT * FROM memories ORDER BY id")]
        finally:
            connection.close()
        if len({str(row["id"]) for row in rows}) != len(rows):
            raise LedgerError("legacy database contains duplicate memory ids")
        return rows

    def _import_rows(
        self, rows: Iterable[dict[str, Any]], generation_id: str
    ) -> list[dict[str, str]]:
        known = self.ledger.records()
        observations = self.ledger.observations()
        mappings: list[dict[str, str]] = []
        for row in rows:
            canonical = _canonical(row)
            row_sha = _sha_bytes(canonical.encode("utf-8"))
            # Stable across rebuild-only generations, but versioned when the row changes.
            # Including legacy_id prevents distinct equal-content rows from collapsing.
            identity = f"{row['id']}\n{row_sha}"
            ledger_id = f"evos-row-{_sha_bytes(identity.encode('utf-8'))[:24]}"
            existing = known.get(ledger_id)
            if existing is None:
                episode_id = self.ledger.observe(
                    canonical,
                    source_type="evos_v2_sqlite_row",
                    source_ref=f"{generation_id}:memories:{row['id']}",
                )
                text = str(row.get("body") or row.get("summary") or canonical)
                title = str(row.get("title") or row.get("id"))
                record = MemoryRecord(
                    id=ledger_id,
                    title=title,
                    text=text,
                    memory_type="episodic",
                    role="note",
                    authority="agent_inference",
                    salience="archive",
                    status="active",
                    importance=10,
                    confidence=0.0,
                    source_episode_ids=(episode_id,),
                    tags=("migration_unreviewed", "legacy_evos_v2", f"legacy_kind:{row.get('kind') or 'none'}"),
                )
                self.ledger.append(record)
                known[ledger_id] = record
                observations = self.ledger.observations()
            else:
                if len(existing.source_episode_ids) != 1:
                    raise LedgerError(f"existing migration record has invalid provenance: {ledger_id}")
                episode_id = existing.source_episode_ids[0]
                if observations.get(episode_id, {}).get("content") != canonical:
                    raise LedgerError(f"existing migration record differs: {ledger_id}")
            mappings.append({
                "legacy_id": str(row["id"]),
                "ledger_id": ledger_id,
                "source_episode_id": episode_id,
                "row_sha256": row_sha,
            })
        return mappings

    def _source_files(self, source: dict[str, Any], rows: list[dict[str, Any]]) -> list[Path]:
        builder_path = self.project_root / "tools" / "evos_memory.py"
        paths = {
            source["pointer_path"], source["task_state_path"], source["manifest_path"],
            source["database_path"], source["context_path"], self.project_root / "AGENTS.md",
            builder_path,
        }
        paths.update((self.evos_root / "seeds").glob("*.jsonl"))
        paths.update((self.evos_root / "thread_memories").glob("*.md"))
        # Preserve every fixed source document used by the legacy builder, even if
        # chunk generation produced no surviving row for an empty or unusual file.
        if builder_path.is_file():
            try:
                tree = ast.parse(builder_path.read_text(encoding="utf-8"))
                for node in tree.body:
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "SOURCE_DOCS"
                        for target in node.targets
                    ):
                        source_docs = ast.literal_eval(node.value)
                        if not isinstance(source_docs, (list, tuple)):
                            raise LedgerError("legacy SOURCE_DOCS is not a literal list")
                        for raw in source_docs:
                            if isinstance(raw, str):
                                paths.add((self.project_root / raw).resolve())
                            else:
                                raise LedgerError("legacy SOURCE_DOCS contains a non-string entry")
            except (OSError, SyntaxError, ValueError) as exc:
                # A custom builder must be reviewed rather than executed merely for
                # discovery; claiming source-level losslessness would be unsafe.
                raise LedgerError(f"cannot enumerate legacy SOURCE_DOCS: {exc}") from exc
        for row in rows:
            raw = str(row.get("source") or "")
            candidate = (self.project_root / raw).resolve()
            try:
                candidate.relative_to(self.project_root)
            except ValueError:
                continue
            if candidate.is_file():
                paths.add(candidate)
        return sorted(path.resolve() for path in paths if path.is_file())

    def _copy_sources(self, paths: Iterable[Path], bundle_root: Path) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for path in paths:
            relative = path.relative_to(self.project_root)
            destination = bundle_root / "project" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            original_hash = _sha_file(path)
            if _sha_file(destination) != original_hash:
                raise LedgerError(f"source copy mismatch: {relative}")
            result.append({
                "source_path": str(relative).replace("\\", "/"),
                "bundle_path": str(destination.relative_to(bundle_root.parent)).replace("\\", "/"),
                "sha256": original_hash,
                "size": path.stat().st_size,
            })
        return result

    @staticmethod
    def _promotion_queue(
        rows: list[dict[str, Any]], mappings: list[dict[str, str]]
    ) -> dict[str, Any]:
        mapping_by_id = {item["legacy_id"]: item for item in mappings}
        items: list[dict[str, Any]] = []
        for row in rows:
            governing = bool(
                row.get("is_current_task")
                or int(row.get("priority") or 0) >= 90
                or str(row.get("kind") or "").lower() in {"task", "constraint", "decision"}
            )
            if row.get("is_current_task"):
                recommended_role = "current_task"
            elif str(row.get("kind") or "").lower() == "constraint":
                recommended_role = "non_negotiable"
            elif str(row.get("kind") or "").lower() == "decision":
                recommended_role = "accepted_decision"
            else:
                recommended_role = "note"
            items.append({
                "legacy_id": str(row["id"]),
                "ledger_archive_id": mapping_by_id[str(row["id"])]["ledger_id"],
                "title": row.get("title"),
                "legacy_kind": row.get("kind"),
                "legacy_status": row.get("status"),
                "legacy_priority": row.get("priority"),
                "legacy_source": row.get("source"),
                "is_current_task": bool(row.get("is_current_task")),
                "governing_candidate": governing,
                "recommended_role": recommended_role,
                "recommended_authority": "UNRESOLVED_REQUIRES_SOURCE_REVIEW",
                "disposition": "PENDING_REVIEW" if governing else "ARCHIVE_PRESERVED",
            })
        return {
            "schema_version": "salience_promotion_queue.v1",
            "policy": "no legacy summary is automatically promoted",
            "item_count": len(items),
            "pending_governing_count": sum(item["disposition"] == "PENDING_REVIEW" for item in items),
            "items": items,
        }
