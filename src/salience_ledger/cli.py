from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import AUTHORITIES, MEMORY_TYPES, ROLES, SALIENCE, STATUSES, MemoryRecord
from .store import Ledger, LedgerError
from .adapters.evos_v2 import import_jsonl


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="salience")
    parser.add_argument("--root", default=".", help="project root (default: current directory)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="initialize an empty memory store")

    observe = commands.add_parser("observe", help="append immutable source evidence")
    observe.add_argument("--content", required=True)
    observe.add_argument("--source-type", required=True)
    observe.add_argument("--source-ref", default="")

    add = commands.add_parser("add", help="append a typed memory record")
    add.add_argument("--id", required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--text", required=True)
    add.add_argument("--type", dest="memory_type", required=True, choices=sorted(MEMORY_TYPES))
    add.add_argument("--role", required=True, choices=sorted(ROLES))
    add.add_argument("--authority", required=True, choices=sorted(AUTHORITIES))
    add.add_argument("--salience", required=True, choices=sorted(SALIENCE))
    add.add_argument("--status", default="active", choices=sorted(STATUSES))
    add.add_argument("--importance", type=int, default=50)
    add.add_argument("--confidence", type=float, default=1.0)
    add.add_argument("--assertion-key")
    values = add.add_mutually_exclusive_group()
    values.add_argument("--value", help="plain string assertion value")
    values.add_argument("--value-json", help="JSON assertion value")
    add.add_argument("--rationale", default="")
    add.add_argument("--rejection-reason", default="")
    add.add_argument("--must-read", action="store_true")
    add.add_argument("--blocks-completion", action="store_true")
    add.add_argument("--tag", action="append", default=[])
    add.add_argument("--source-episode", action="append", default=[])

    commands.add_parser("build", help="validate and publish an immutable generation")
    commands.add_parser(
        "clarifications", help="show conflicting user statements that require confirmation"
    )
    resolve = commands.add_parser(
        "resolve", help="resolve user ambiguity from a confirmed record JSON file"
    )
    resolve.add_argument("--record-json", required=True)
    resolve.add_argument("--supersede", action="append", required=True)
    doctor = commands.add_parser("doctor", help="audit contradictions and completion gates")
    doctor.add_argument("--completion", action="store_true")
    search = commands.add_parser("search", help="search non-authoritative working/archive memory")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=12)
    context = commands.add_parser("context", help="print deterministic recovery context")
    context.add_argument("query", nargs="?", default="")
    context.add_argument("--limit", type=int, default=12)
    migration = commands.add_parser(
        "import-evos-v2", help="import legacy JSONL into a non-authoritative review queue"
    )
    migration.add_argument("path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = Ledger(Path(args.root))
    try:
        if args.command == "init":
            store.init()
            print(store.state)
        elif args.command == "observe":
            print(store.observe(args.content, source_type=args.source_type, source_ref=args.source_ref))
        elif args.command == "add":
            record = MemoryRecord(
                id=args.id,
                title=args.title,
                text=args.text,
                memory_type=args.memory_type,
                role=args.role,
                authority=args.authority,
                salience=args.salience,
                status=args.status,
                importance=args.importance,
                confidence=args.confidence,
                assertion_key=args.assertion_key,
                value=(
                    args.value
                    if args.value is not None
                    else json.loads(args.value_json) if args.value_json is not None else None
                ),
                rationale=args.rationale,
                rejection_reason=args.rejection_reason,
                must_read=args.must_read,
                blocks_completion=args.blocks_completion,
                tags=tuple(args.tag),
                source_episode_ids=tuple(args.source_episode),
            )
            print(store.append(record))
        elif args.command == "build":
            print(json.dumps(store.build(), indent=2, ensure_ascii=False))
        elif args.command == "clarifications":
            print(json.dumps(store.clarification_packets(), indent=2, ensure_ascii=False))
        elif args.command == "resolve":
            data = json.loads(Path(args.record_json).read_text(encoding="utf-8"))
            confirmed = MemoryRecord.from_dict(data)
            print(store.resolve_user_ambiguity(args.supersede, confirmed))
        elif args.command == "doctor":
            issues = store.audit(completion=args.completion)
            if issues:
                print("FAIL")
                for issue in issues:
                    print(f"- {issue}")
                return 1
            print("PASS")
        elif args.command == "search":
            print(json.dumps(store.search(args.query, limit=args.limit), indent=2, ensure_ascii=False))
        elif args.command == "context":
            print(store.recovery_context(args.query, limit=args.limit), end="")
        elif args.command == "import-evos-v2":
            print(json.dumps(import_jsonl(store, args.path), indent=2, ensure_ascii=False))
    except (LedgerError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
