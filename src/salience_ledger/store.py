from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from .models import CORE_ORDER, MemoryRecord, parse_timestamp, utc_now


ACTIVE_STATUSES = {"active", "accepted"}
MAX_CORE_CHARACTERS = 60_000
AUTHORITY_WEIGHT = {
    "system_constraint": 6,
    "user_explicit": 5,
    "accepted_document": 4,
    "engineering_decision": 3,
    "machine_result": 2,
    "agent_inference": 1,
}


class LedgerError(RuntimeError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Ledger:
    """Append-only memory events with deterministic, immutable projections."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.state = self.root / ".salience"
        self.events_path = self.state / "episodes.jsonl"
        self.index_root = self.state / "index"
        self.generations = self.index_root / "generations"
        self.current_path = self.index_root / "CURRENT.json"

    def init(self) -> None:
        self.generations.mkdir(parents=True, exist_ok=True)
        if not self.events_path.exists():
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            self.events_path.touch()

    def observe(self, content: str, *, source_type: str, source_ref: str = "") -> str:
        """Append immutable source evidence before deriving a governing memory record."""
        self.init()
        if not content.strip() or not source_type.strip():
            raise ValueError("observation content and source_type are required")
        episode_id = f"ep-{uuid.uuid4().hex}"
        observation = {
            "schema_version": "salience_episode.v1",
            "episode_id": episode_id,
            "event": "observation",
            "content": content,
            "content_sha256": _sha256_bytes(content.encode("utf-8")),
            "source_type": source_type,
            "source_ref": source_ref,
            "recorded_at": utc_now(),
        }
        self._append_episode(observation)
        return episode_id

    def append(self, record: MemoryRecord, *, event: str = "upsert") -> str:
        self.init()
        record.validate()
        existing = self.records().get(record.id)
        if existing is not None:
            old = existing.as_dict()
            new = record.as_dict()
            old_status, new_status = old.pop("status"), new.pop("status")
            if old != new:
                raise LedgerError(
                    f"record id {record.id} is immutable; create a new id and explicitly resolve/supersede"
                )
            allowed = {
                "active": {"resolved", "superseded", "rejected", "archived"},
                "accepted": {"resolved", "superseded", "rejected", "archived"},
            }
            if new_status not in allowed.get(old_status, set()):
                raise LedgerError(f"invalid status transition for {record.id}: {old_status} -> {new_status}")
        known_episodes = self.episode_ids()
        observations = self.observations()
        if (record.salience == "core" or record.must_read) and not record.source_episode_ids:
            raise LedgerError(f"core record {record.id} requires source_episode_ids")
        missing = sorted(set(record.source_episode_ids) - known_episodes)
        if missing:
            raise LedgerError(f"record {record.id} references missing episodes: {', '.join(missing)}")
        if record.salience == "core" or record.must_read:
            non_observations = sorted(set(record.source_episode_ids) - set(observations))
            if non_observations:
                raise LedgerError(
                    f"core record {record.id} sources must be observations: {', '.join(non_observations)}"
                )
        episode = {
            "schema_version": "salience_episode.v1",
            "episode_id": f"ep-{uuid.uuid4().hex}",
            "event": event,
            "record": record.as_dict(),
            "record_sha256": _sha256_bytes(_canonical_json(record.as_dict()).encode("utf-8")),
            "recorded_at": utc_now(),
        }
        self._append_episode(episode)
        return str(episode["episode_id"])

    def _append_episode(self, episode: dict[str, Any]) -> None:
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(_canonical_json(episode) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def supersede(self, old_id: str, replacement: MemoryRecord) -> None:
        records = self.records()
        if old_id not in records:
            raise LedgerError(f"unknown superseded id: {old_id}")
        if records[old_id].authority == "user_explicit":
            raise LedgerError(
                "user_explicit memory cannot be superseded by convenience API; "
                "use resolve_user_ambiguity with a user_confirmation episode"
            )
        replacement = replace(replacement, supersedes=tuple(set(replacement.supersedes) | {old_id}))
        old = replace(records[old_id], status="superseded")
        self.append(old)
        self.append(replacement)

    def resolve_user_ambiguity(
        self, record_ids: Iterable[str], confirmed: MemoryRecord
    ) -> str:
        """Resolve user semantic ambiguity only from a new explicit confirmation episode."""
        ids = tuple(dict.fromkeys(record_ids))
        if len(ids) < 2:
            raise LedgerError("user ambiguity resolution requires at least two prior records")
        records = self.records()
        missing = [record_id for record_id in ids if record_id not in records]
        if missing:
            raise LedgerError(f"unknown ambiguity records: {', '.join(missing)}")
        prior = [records[record_id] for record_id in ids]
        if any(item.authority != "user_explicit" for item in prior):
            raise LedgerError("all ambiguity records must have user_explicit authority")
        keys = {item.assertion_key for item in prior}
        if len(keys) != 1 or None in keys:
            raise LedgerError("ambiguity records must share one non-empty assertion_key")
        if confirmed.authority != "user_explicit" or confirmed.assertion_key not in keys:
            raise LedgerError("confirmed record must be user_explicit with the same assertion_key")
        observations = self.observations()
        confirmation_sources = [observations.get(item) for item in confirmed.source_episode_ids]
        if not any(item and item.get("source_type") == "user_confirmation" for item in confirmation_sources):
            raise LedgerError("resolution requires a source episode with source_type=user_confirmation")
        if confirmed.id in records:
            raise LedgerError(f"confirmed record id already exists: {confirmed.id}")
        confirmed = replace(confirmed, supersedes=tuple(sorted(set(confirmed.supersedes) | set(ids))))
        confirmed.validate()
        payload = confirmed.as_dict()
        episode = {
            "schema_version": "salience_episode.v1",
            "episode_id": f"ep-{uuid.uuid4().hex}",
            "event": "user_ambiguity_resolution",
            "superseded_ids": list(ids),
            "record": payload,
            "record_sha256": _sha256_bytes(_canonical_json(payload).encode("utf-8")),
            "recorded_at": utc_now(),
        }
        self._append_episode(episode)
        return str(episode["episode_id"])

    def records(self) -> dict[str, MemoryRecord]:
        self.init()
        latest: dict[str, MemoryRecord] = {}
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    episode = json.loads(raw)
                    if episode.get("event") == "observation":
                        expected_content = _sha256_bytes(str(episode["content"]).encode("utf-8"))
                        if episode.get("content_sha256") != expected_content:
                            raise LedgerError(f"observation hash mismatch at line {line_number}")
                        continue
                    if episode.get("event") == "user_ambiguity_resolution":
                        for old_id in episode["superseded_ids"]:
                            if old_id not in latest:
                                raise LedgerError(
                                    f"resolution references unknown prior record {old_id} at line {line_number}"
                                )
                            latest[old_id] = replace(latest[old_id], status="superseded")
                    record_data = episode["record"]
                    expected = _sha256_bytes(_canonical_json(record_data).encode("utf-8"))
                    if episode.get("record_sha256") != expected:
                        raise LedgerError(f"episode record hash mismatch at line {line_number}")
                    record = MemoryRecord.from_dict(record_data)
                except (KeyError, ValueError, json.JSONDecodeError) as exc:
                    raise LedgerError(f"invalid episode at line {line_number}: {exc}") from exc
                existing = latest.get(record.id)
                if existing is not None:
                    old = existing.as_dict()
                    new = record.as_dict()
                    old_status, new_status = old.pop("status"), new.pop("status")
                    if old != new:
                        raise LedgerError(
                            f"immutable record {record.id} was rewritten at line {line_number}"
                        )
                    allowed = {
                        "active": {"resolved", "superseded", "rejected", "archived"},
                        "accepted": {"resolved", "superseded", "rejected", "archived"},
                    }
                    if new_status not in allowed.get(old_status, set()):
                        raise LedgerError(
                            f"invalid projected status transition for {record.id} at line {line_number}: "
                            f"{old_status} -> {new_status}"
                        )
                latest[record.id] = record
        return latest

    def audit(self, *, completion: bool = False) -> list[str]:
        records = self.records()
        issues: list[str] = []
        now = parse_timestamp(utc_now())
        active = {
            key: value
            for key, value in records.items()
            if value.status in ACTIVE_STATUSES and self._is_effective(value, now)
        }

        by_assertion: dict[str, list[MemoryRecord]] = {}
        for record in active.values():
            if record.assertion_key:
                by_assertion.setdefault(record.assertion_key, []).append(record)
        for key, group in by_assertion.items():
            values = {_canonical_json(item.value) for item in group}
            if len(values) > 1:
                ids = ", ".join(sorted(item.id for item in group))
                user_records = [item for item in group if item.authority == "user_explicit"]
                user_values = {_canonical_json(item.value) for item in user_records}
                if len(user_records) >= 2 and len(user_values) > 1:
                    issues.append(f"USER_CONFIRMATION_REQUIRED assertion {key}: {ids}")
                elif user_records:
                    issues.append(f"lower-authority record conflicts with explicit user meaning {key}: {ids}")
                else:
                    issues.append(f"unresolved assertion conflict {key}: {ids}")

        ambiguity_pairs: set[tuple[str, str]] = set()
        for record in active.values():
            if record.authority != "user_explicit":
                continue
            for target_id in record.conflicts_with:
                target = active.get(target_id)
                if target and target.authority == "user_explicit":
                    ambiguity_pairs.add(tuple(sorted((record.id, target.id))))
        for left, right in sorted(ambiguity_pairs):
            issues.append(f"USER_CONFIRMATION_REQUIRED explicit ambiguity: {left}, {right}")

        current_tasks = [item for item in active.values() if item.role == "current_task"]
        if len(current_tasks) > 1:
            issues.append("multiple active current_task records")

        for record in active.values():
            for target in (*record.supersedes, *record.conflicts_with):
                if target not in records:
                    issues.append(f"{record.id} references missing record {target}")
            if record.source_episode_ids:
                known = self.episode_ids()
                for episode_id in record.source_episode_ids:
                    if episode_id not in known:
                        issues.append(f"{record.id} references missing episode {episode_id}")

        rejections = {
            record.assertion_key: record
            for record in active.values()
            if record.role == "permanent_rejection" and record.assertion_key
        }
        for record in active.values():
            rejected = rejections.get(record.assertion_key or "")
            if rejected and record.id != rejected.id and record.role == "engineering_proposal":
                issues.append(
                    f"proposal {record.id} revives permanent rejection {rejected.id} without resolution"
                )

        if completion:
            for record in active.values():
                if record.blocks_completion:
                    issues.append(f"completion blocked by {record.id}: {record.title}")
        core_size = len(self._render_core(active.values()))
        if core_size > MAX_CORE_CHARACTERS:
            issues.append(
                f"core memory budget exceeded: {core_size} > {MAX_CORE_CHARACTERS} characters"
            )
        return sorted(set(issues))

    @staticmethod
    def _is_effective(record: MemoryRecord, at) -> bool:
        if record.valid_from and parse_timestamp(record.valid_from) > at:
            return False
        if record.valid_to and parse_timestamp(record.valid_to) < at:
            return False
        return True

    def episode_ids(self) -> set[str]:
        self.init()
        ids: set[str] = set()
        with self.events_path.open("r", encoding="utf-8") as handle:
            for raw in handle:
                if raw.strip():
                    ids.add(str(json.loads(raw)["episode_id"]))
        return ids

    def observations(self) -> dict[str, dict[str, Any]]:
        self.init()
        result: dict[str, dict[str, Any]] = {}
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                episode = json.loads(raw)
                if episode.get("event") != "observation":
                    continue
                expected = _sha256_bytes(str(episode["content"]).encode("utf-8"))
                if episode.get("content_sha256") != expected:
                    raise LedgerError(f"observation hash mismatch at line {line_number}")
                result[str(episode["episode_id"])] = episode
        return result

    def clarification_packets(self) -> list[dict[str, Any]]:
        """Return both user wordings and sources; never choose the latest statement."""
        records = self.records()
        observations = self.observations()
        active = [
            item for item in records.values()
            if item.status in ACTIVE_STATUSES and item.authority == "user_explicit"
        ]
        groups: dict[str, list[MemoryRecord]] = {}
        for item in active:
            if item.assertion_key:
                groups.setdefault(item.assertion_key, []).append(item)
        packets: list[dict[str, Any]] = []
        for key, group in sorted(groups.items()):
            values = {_canonical_json(item.value) for item in group}
            explicit_links = any(
                target in {candidate.id for candidate in group}
                for item in group for target in item.conflicts_with
            )
            if len(values) <= 1 and not explicit_links:
                continue
            statements = []
            for item in sorted(group, key=lambda value: (value.known_at, value.id)):
                statements.append({
                    "record_id": item.id,
                    "text": item.text,
                    "value": item.value,
                    "known_at": item.known_at,
                    "sources": [
                        {
                            "episode_id": source_id,
                            "content": observations.get(source_id, {}).get("content"),
                            "source_ref": observations.get(source_id, {}).get("source_ref"),
                        }
                        for source_id in item.source_episode_ids
                    ],
                })
            packets.append({
                "assertion_key": key,
                "status": "USER_CONFIRMATION_REQUIRED",
                "statements": statements,
                "allowed_resolution": ["supersede", "scope_split", "withdraw_both"],
            })
        return packets

    def build(self) -> dict[str, Any]:
        issues = self.audit()
        if issues:
            raise LedgerError("build refused:\n- " + "\n- ".join(issues))
        records = self.records()
        source_hash = _sha256_file(self.events_path)
        generation_id = f"gen-{utc_now().replace(':', '').replace('-', '')}-{source_hash[:12]}"
        target = self.generations / generation_id
        if target.exists():
            return self._read_json(target / "manifest.json")
        target.mkdir(parents=True)

        now = parse_timestamp(utc_now())
        effective_records = [item for item in records.values() if self._is_effective(item, now)]
        core_text = self._render_core(effective_records)
        (target / "CORE_MEMORY.md").write_text(core_text, encoding="utf-8", newline="\n")
        self._write_sqlite(target / "memory.sqlite", effective_records)
        manifest = {
            "schema_version": "salience_generation.v1",
            "generation_id": generation_id,
            "built_at": utc_now(),
            "source_sha256": source_hash,
            "record_count": len(records),
            "files": {
                "CORE_MEMORY.md": _sha256_file(target / "CORE_MEMORY.md"),
                "memory.sqlite": _sha256_file(target / "memory.sqlite"),
            },
        }
        self._write_json_atomic(target / "manifest.json", manifest)
        pointer = {
            "schema_version": "salience_pointer.v1",
            "generation_id": generation_id,
            "generation_path": str(target.relative_to(self.root)).replace("\\", "/"),
            "manifest_sha256": _sha256_file(target / "manifest.json"),
        }
        self._write_json_atomic(self.current_path, pointer)
        return manifest

    def verify_current(self) -> dict[str, Any]:
        if not self.current_path.exists():
            raise LedgerError("CURRENT.json is missing; run build")
        pointer = self._read_json(self.current_path)
        target = self.root / pointer["generation_path"]
        manifest_path = target / "manifest.json"
        if _sha256_file(manifest_path) != pointer["manifest_sha256"]:
            raise LedgerError("current manifest hash mismatch")
        manifest = self._read_json(manifest_path)
        if manifest["source_sha256"] != _sha256_file(self.events_path):
            raise LedgerError("current generation is stale")
        for name, expected in manifest["files"].items():
            if _sha256_file(target / name) != expected:
                raise LedgerError(f"generation file hash mismatch: {name}")
        return pointer

    def recovery_context(self, query: str = "", *, limit: int = 12) -> str:
        pointer = self.verify_current()
        target = self.root / pointer["generation_path"]
        core = (target / "CORE_MEMORY.md").read_text(encoding="utf-8")
        if not query.strip():
            return core
        results = self.search(query, limit=limit)
        if not results:
            return core
        lines = [core.rstrip(), "", "# Relevant working/archive memory", ""]
        for item in results:
            lines.append(f"- [{item['id']}] {item['title']}: {item['text']}")
        return "\n".join(lines) + "\n"

    def search(self, query: str, *, limit: int = 12) -> list[dict[str, Any]]:
        pointer = self.verify_current()
        db_path = self.root / pointer["generation_path"] / "memory.sqlite"
        terms = [term.lower() for term in re.findall(r"[\w-]+", query, flags=re.UNICODE)]
        conn = sqlite3.connect(db_path)
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM memories WHERE status IN ('active','accepted')").fetchall()
        finally:
            conn.close()
        scored: list[tuple[int, sqlite3.Row]] = []
        for row in rows:
            haystack = " ".join(str(row[key] or "") for key in ("title", "text", "tags")).lower()
            matches = sum(term in haystack for term in terms)
            if not matches:
                continue
            score = matches * 1000 + int(row["importance"]) * 10 + AUTHORITY_WEIGHT[row["authority"]]
            if row["salience"] == "working":
                score += 100
            scored.append((score, row))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        return [dict(row) for _, row in scored[:limit]]

    def _render_core(self, records: Iterable[MemoryRecord]) -> str:
        active = [
            record
            for record in records
            if record.status in ACTIVE_STATUSES and (record.salience == "core" or record.must_read)
        ]
        role_index = {role: index for index, role in enumerate(CORE_ORDER)}
        active.sort(
            key=lambda item: (
                role_index.get(item.role, len(CORE_ORDER)),
                -AUTHORITY_WEIGHT[item.authority],
                -item.importance,
                item.id,
            )
        )
        lines = [
            "# Salience Ledger Core Memory",
            "",
            "> Deterministic recovery context. Read every section before acting.",
            "> Search summaries are navigation aids and never override these records.",
            "",
        ]
        for role in CORE_ORDER:
            group = [item for item in active if item.role == role]
            if not group:
                continue
            lines.extend([f"## {role.upper()}", ""])
            for item in group:
                metadata = f"authority={item.authority}; status={item.status}; id={item.id}"
                lines.append(f"- **{item.title}** ({metadata})")
                lines.append(f"  {item.text}")
                if item.rationale:
                    lines.append(f"  Rationale: {item.rationale}")
                if item.rejection_reason:
                    lines.append(f"  Rejection reason: {item.rejection_reason}")
                if item.blocks_completion:
                    lines.append("  Completion gate: BLOCKED until this record is resolved.")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _write_sqlite(path: Path, records: Iterable[MemoryRecord]) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.executescript(
                """
                PRAGMA journal_mode=DELETE;
                CREATE TABLE memories (
                  id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
                  memory_type TEXT NOT NULL, role TEXT NOT NULL, authority TEXT NOT NULL,
                  salience TEXT NOT NULL, status TEXT NOT NULL, importance INTEGER NOT NULL,
                  confidence REAL NOT NULL, assertion_key TEXT, value_json TEXT,
                  rationale TEXT NOT NULL, rejection_reason TEXT NOT NULL,
                  known_at TEXT NOT NULL, tags TEXT NOT NULL
                );
                """
            )
            for record in sorted(records, key=lambda item: item.id):
                conn.execute(
                    "INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.id, record.title, record.text, record.memory_type, record.role,
                        record.authority, record.salience, record.status, record.importance,
                        record.confidence, record.assertion_key, _canonical_json(record.value),
                        record.rationale, record.rejection_reason, record.known_at,
                        " ".join(record.tags),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
