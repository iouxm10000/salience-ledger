# Migration from a summary-first store

1. Keep the existing raw memories as immutable source episodes.
2. Promote only explicitly supported records into typed control records.
3. Split mixed statements into user facts, accepted decisions, proposals, and inferences.
4. Give every governing statement an `assertion_key` and canonical JSON value.
5. Materialize permanent rejections and known counterexamples before importing general notes.
6. Run `doctor`; resolve conflicts rather than combining them into prose.
7. Build and test recovery from `CORE_MEMORY.md` without using the old conversation summary.

Never publish a private store as an example dataset. Use synthetic examples only.

## Lossless EvoS v2 snapshot migration

The JSONL adapter below is intentionally a convenience importer, not a lossless migration.
For a governed migration, run:

```bash
salience --root NEW_PROJECT migrate-evos-v2 --project-root LEGACY_PROJECT
salience --root NEW_PROJECT migration-check \
  --project-root LEGACY_PROJECT --migration-id MIGRATION_ID --current
```

The full migrator freezes the selected immutable SQLite generation, its build manifest and
context, `CURRENT.json`, `task_state.json`, every seed and thread-memory file, the legacy builder,
its literal `SOURCE_DOCS`, and project-local source files referenced by database rows. It also
exports every SQLite row canonically and maps each legacy ID to exactly one archive record and
one immutable observation. Identical contents never collapse distinct legacy identities.
Unchanged rows reuse a content-versioned archive identity across rebuild-only generations, while
a changed row under the same legacy ID creates a new immutable version. Prior versions remain
verifiable but transition to `archived`, so search does not present stale and current meanings as
equally active.

Three gates are deliberately separate:

1. **Evidence parity** verifies byte hashes, row counts, one-to-one mappings, exact observation
   contents, the migration-manifest receipt, and the current-task mapping.
2. **Semantic parity** remains `PENDING` while any governing candidate awaits source review.
   Legacy summaries are evidence, not automatically accepted truth.
3. **Cutover readiness** additionally requires that the legacy `CURRENT` semantic identity has
   not changed. A rebuild-only generation advance is allowed only when source, SQLite, and task
   state hashes remain exact. Run `migration-check --cutover`; a non-ready migration exits non-zero.

During dual-read operation, the legacy system remains authoritative and Salience Ledger is a
read-only shadow. If the legacy semantic identity changes, create a new snapshot. Do not change an
agent startup rule to Salience Ledger until evidence parity and semantic review both pass.
`--current` checks evidence and current legacy identity without pretending the review queue is
finished; `--cutover` additionally requires semantic parity.

Review evidence is itself an immutable observation. Then dispose each governing candidate with
`migration-review` as `ARCHIVE_PRESERVED`, `REJECTED`, or `PROMOTED`. Promotion requires a typed
non-archive record that cites both the exact legacy-row observation and the new review episode.
Review sources are restricted to `user_confirmation`, `accepted_document_review`, or
`coordinator_review`; an agent inference cannot approve its own migration.

## Legacy EvoS v2 adapter

`salience --root PROJECT import-evos-v2 PATH_TO_JSONL` imports each legacy row as an
`archive/note` with `agent_inference` authority, zero confidence, and a
`migration_unreviewed` tag. This is intentionally conservative: old labels and summaries cannot
silently become new core truth. Promote important items only after reading their original source
and assigning the correct authority, role, assertion key, and rationale.
