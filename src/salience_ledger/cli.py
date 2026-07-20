from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .models import AUTHORITIES, MEMORY_TYPES, ROLES, SALIENCE, STATUSES, MemoryRecord
from .store import Ledger, LedgerError
from .adapters.evos_v2 import import_jsonl
from .migration.evos_v2 import EvosV2Migrator
from .task_runs import ACTORS, EVENT_ACTORS, TaskRun


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
    full_migration = commands.add_parser(
        "migrate-evos-v2",
        help="preserve one immutable EvoS generation and create a semantic review queue",
    )
    full_migration.add_argument("--project-root", required=True)
    migration_check = commands.add_parser(
        "migration-check", help="verify evidence parity; cutover remains blocked by review"
    )
    migration_check.add_argument("--project-root", required=True)
    migration_check.add_argument(
        "--migration-id",
        help="frozen migration id; with --current, omit to resolve it from current EvoS identity",
    )
    migration_check.add_argument(
        "--current", action="store_true", help="also require current legacy semantic identity"
    )
    migration_check.add_argument("--cutover", action="store_true")
    migration_review = commands.add_parser(
        "migration-review", help="append one source-backed semantic migration disposition"
    )
    migration_review.add_argument("--project-root", required=True)
    migration_review.add_argument("--migration-id", required=True)
    migration_review.add_argument("--legacy-id", required=True)
    migration_review.add_argument(
        "--disposition", required=True, choices=["ARCHIVE_PRESERVED", "REJECTED", "PROMOTED"]
    )
    migration_review.add_argument("--review-episode", required=True)
    migration_review.add_argument("--promoted-record")

    task_init = commands.add_parser(
        "task-init", help="create an immutable long-task contract and event chain"
    )
    task_init.add_argument("--run-id", required=True)
    task_init.add_argument("--goal", required=True)
    task_init.add_argument("--gate", action="append", required=True)
    task_init.add_argument("--completion", action="append", required=True)
    task_init.add_argument("--scope", action="append", default=[])
    task_init.add_argument("--protect", action="append", default=[])
    task_init.add_argument("--red-line", action="append", default=[])
    task_init.add_argument("--commit-authorized", action="store_true")
    task_init.add_argument("--push-authorized", action="store_true")
    task_init.add_argument("--converge-every", type=int, default=5)
    task_init.add_argument("--large-change-lines", type=int, default=400)
    task_init.add_argument("--max-rounds", type=int, default=50)

    task_event = commands.add_parser(
        "task-event", help="append one validated executor/supervisor/owner event"
    )
    task_event.add_argument("--run-id", required=True)
    task_event.add_argument("--actor", required=True, choices=sorted(ACTORS))
    task_event.add_argument("--event-type", required=True, choices=sorted(EVENT_ACTORS))
    event_payload = task_event.add_mutually_exclusive_group(required=True)
    event_payload.add_argument("--payload-json")
    event_payload.add_argument("--payload-file")

    task_context = commands.add_parser(
        "task-context", help="render compaction-safe memory plus active task state"
    )
    task_context.add_argument("--run-id", required=True)
    task_context.add_argument("--query", default="")

    task_doctor = commands.add_parser(
        "task-doctor", help="verify event chain, memory ambiguity, directives and completion gates"
    )
    task_doctor.add_argument("--run-id", required=True)
    task_doctor.add_argument("--completion", action="store_true")
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
        elif args.command == "migrate-evos-v2":
            migrator = EvosV2Migrator(store, args.project_root)
            manifest = migrator.migrate()
            report = migrator.verify(manifest["migration_id"], require_current=True)
            print(json.dumps({
                "migration_id": manifest["migration_id"],
                "manifest_path": str(
                    store.state / "migrations" / manifest["migration_id"] / "migration_manifest.json"
                ),
                "legacy_generation_id": manifest["legacy_generation_id"],
                "source_file_count": len(manifest["source_files"]),
                "parity": report,
            }, indent=2, ensure_ascii=False))
        elif args.command == "migration-check":
            migrator = EvosV2Migrator(store, args.project_root)
            migration_id = args.migration_id
            if migration_id is None:
                if not args.current:
                    raise LedgerError("--migration-id is required unless --current is used")
                migration_id = migrator.current_migration_id()
            report = migrator.verify(migration_id, require_current=args.current or args.cutover)
            print(json.dumps(report, indent=2, ensure_ascii=False))
            if (
                report["evidence_parity"] != "PASS"
                or ((args.current or args.cutover) and report["cutover_issues"])
                or (args.cutover and not report["cutover_ready"])
            ):
                return 1
        elif args.command == "migration-review":
            result = EvosV2Migrator(store, args.project_root).review(
                args.migration_id,
                args.legacy_id,
                args.disposition,
                args.review_episode,
                args.promoted_record,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "task-init":
            result = TaskRun(store, args.run_id).initialize(
                goal=args.goal,
                gates=args.gate,
                completion_criteria=args.completion,
                scope_paths=args.scope,
                protected_paths=args.protect,
                red_lines=args.red_line,
                commit_authorized=args.commit_authorized,
                push_authorized=args.push_authorized,
                converge_every=args.converge_every,
                large_change_lines=args.large_change_lines,
                max_rounds=args.max_rounds,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "task-event":
            payload = json.loads(
                args.payload_json
                if args.payload_json is not None
                else Path(args.payload_file).read_text(encoding="utf-8")
            )
            result = TaskRun(store, args.run_id).append(
                actor=args.actor, event_type=args.event_type, payload=payload
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "task-context":
            print(TaskRun(store, args.run_id).render_context(args.query), end="")
        elif args.command == "task-doctor":
            issues = TaskRun(store, args.run_id).audit(completion=args.completion)
            if issues:
                print("FAIL")
                for issue in issues:
                    print(f"- {issue}")
                return 1
            print("PASS")
    except (LedgerError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
