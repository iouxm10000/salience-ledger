# Codex integration and staged cutover

Salience Ledger is not useful if an agent only consults it after compaction. Put the recovery
contract in the repository's `AGENTS.md` so every fresh or resumed Codex task follows it before
analysis or edits.

## Shadow phase

Keep the legacy store authoritative while the migration review queue is open:

```text
1. Read the legacy task pointer and immutable generation as before.
2. Run `salience --root PROJECT context` and read all deterministic core sections.
3. Run `migration-check` for the frozen EvoS snapshot.
4. If the two systems disagree, stop. Do not select the newer wording automatically.
5. Resolve user/user ambiguity only by asking the user with both original statements visible.
6. Append important decisions to both stores until cutover is accepted.
```

Archive rows are searchable evidence, not governing instructions. A semantic promotion must read
the original source, assign a typed role and authority, and retain its source episode. Mixed source
statements should be split instead of promoted as one record.

## Cutover gate

Run:

```bash
salience --root PROJECT migration-check \
  --project-root LEGACY_PROJECT --migration-id MIGRATION_ID --cutover
```

Cutover is forbidden unless the command exits zero. This requires:

- byte-for-byte evidence parity;
- one-to-one legacy row mappings;
- no unresolved governing candidates;
- the old `CURRENT` source, SQLite, and task-state identities still matching the reviewed snapshot;
- no unresolved user ambiguity in the new ledger;
- a successful immutable Salience generation build and recovery test.

Only after explicit coordinator acceptance should `AGENTS.md` make Salience Ledger authoritative.
Retain the frozen EvoS bundle indefinitely as provenance and rollback evidence; do not delete it
after cutover.

## Long-running tasks

Install or invoke `skills/salience-supervise-task` for multi-round implementation, audit, or
migration work. Create the run contract once, append validated task events, and re-run
`task-context` after compaction or resume. A clean-context supervisor reads durable task state and
Git evidence, not the executor's conversation transcript, and sends corrections only through the
task directive channel. The skill does not grant permission to spawn agents, schedule work,
commit, push, or create a Codex goal.
