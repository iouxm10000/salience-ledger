from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


MEMORY_TYPES = {"semantic", "episodic", "procedural"}
ROLES = {
    "user_intent",
    "non_negotiable",
    "permanent_rejection",
    "open_blocker",
    "accepted_decision",
    "counterexample",
    "current_task",
    "engineering_proposal",
    "source",
    "evidence",
    "note",
}
AUTHORITIES = {
    "user_explicit",
    "system_constraint",
    "accepted_document",
    "engineering_decision",
    "machine_result",
    "agent_inference",
}
SALIENCE = {"core", "working", "archive"}
STATUSES = {"active", "accepted", "resolved", "superseded", "rejected", "archived"}
CORE_ORDER = (
    "user_intent",
    "non_negotiable",
    "permanent_rejection",
    "open_blocker",
    "accepted_decision",
    "counterexample",
    "current_task",
    "engineering_proposal",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    title: str
    text: str
    memory_type: str
    role: str
    authority: str
    salience: str
    status: str = "active"
    importance: int = 50
    confidence: float = 1.0
    assertion_key: str | None = None
    value: Any = None
    rationale: str = ""
    rejection_reason: str = ""
    valid_from: str | None = None
    valid_to: str | None = None
    known_at: str = field(default_factory=utc_now)
    supersedes: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    source_episode_ids: tuple[str, ...] = ()
    must_read: bool = False
    blocks_completion: bool = False
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryRecord":
        values = dict(data)
        for key in ("supersedes", "conflicts_with", "source_episode_ids", "tags"):
            values[key] = tuple(values.get(key, ()))
        record = cls(**values)
        record.validate()
        return record

    def validate(self) -> None:
        if not self.id or not self.title or not self.text:
            raise ValueError("id, title and text are required")
        if self.memory_type not in MEMORY_TYPES:
            raise ValueError(f"invalid memory_type: {self.memory_type}")
        if self.role not in ROLES:
            raise ValueError(f"invalid role: {self.role}")
        if self.authority not in AUTHORITIES:
            raise ValueError(f"invalid authority: {self.authority}")
        if self.salience not in SALIENCE:
            raise ValueError(f"invalid salience: {self.salience}")
        if self.status not in STATUSES:
            raise ValueError(f"invalid status: {self.status}")
        if not 0 <= self.importance <= 100:
            raise ValueError("importance must be between 0 and 100")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.salience == "core" and self.role not in CORE_ORDER:
            raise ValueError(f"role {self.role} is not allowed in core memory")
        if self.authority == "agent_inference" and self.role in {
            "user_intent", "non_negotiable", "permanent_rejection"
        }:
            raise ValueError("agent inference cannot impersonate user/system authority")
        if self.role == "permanent_rejection" and not self.rejection_reason:
            raise ValueError("permanent_rejection requires rejection_reason")
        if self.role in {"accepted_decision", "engineering_proposal"} and not self.rationale:
            raise ValueError(f"{self.role} requires rationale")
        if self.blocks_completion and self.role not in {"open_blocker", "counterexample"}:
            raise ValueError("only blocker/counterexample may block completion")
        parse_timestamp(self.known_at)
        if self.valid_from:
            parse_timestamp(self.valid_from)
        if self.valid_to:
            parse_timestamp(self.valid_to)
        if self.valid_from and self.valid_to:
            if parse_timestamp(self.valid_from) > parse_timestamp(self.valid_to):
                raise ValueError("valid_from must not be after valid_to")

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "text": self.text,
            "memory_type": self.memory_type,
            "role": self.role,
            "authority": self.authority,
            "salience": self.salience,
            "status": self.status,
            "importance": self.importance,
            "confidence": self.confidence,
            "assertion_key": self.assertion_key,
            "value": self.value,
            "rationale": self.rationale,
            "rejection_reason": self.rejection_reason,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "known_at": self.known_at,
            "supersedes": list(self.supersedes),
            "conflicts_with": list(self.conflicts_with),
            "source_episode_ids": list(self.source_episode_ids),
            "must_read": self.must_read,
            "blocks_completion": self.blocks_completion,
            "tags": list(self.tags),
        }
