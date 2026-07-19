from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..models import MemoryRecord
from ..store import Ledger


def import_jsonl(ledger: Ledger, path: str | Path) -> dict[str, int]:
    """Import legacy records as unreviewed archive notes, never as governing memory."""
    source = Path(path).resolve()
    known = ledger.records()
    imported = 0
    skipped = 0
    with source.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, 1):
            if not raw.strip():
                continue
            data: dict[str, Any] = json.loads(raw)
            canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            # Identity includes the source location. Two legacy rows may have identical
            # content while remaining distinct evidence and must never be collapsed.
            identity = f"{source}:{line_number}\n{canonical}"
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            identifier = f"legacy-{digest[:20]}"
            if identifier in known:
                skipped += 1
                continue
            episode_id = ledger.observe(
                canonical,
                source_type="legacy_evos_v2_record",
                source_ref=f"{source}:{line_number}",
            )
            title = str(data.get("title") or data.get("id") or f"Legacy record {line_number}")
            text = str(data.get("summary") or data.get("text") or canonical)
            record = MemoryRecord(
                id=identifier,
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
                tags=("migration_unreviewed", "legacy_evos_v2"),
            )
            ledger.append(record)
            known[identifier] = record
            imported += 1
    return {"imported": imported, "skipped": skipped}
