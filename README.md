# Salience Ledger

Salience Ledger is a deterministic, provenance-first memory control plane for long-running AI agents.
It is designed for the failure mode that ordinary chat summaries do not solve: after context
compaction, an agent can remember many details while forgetting the few rules, rejections,
counterexamples, and blockers that actually govern the work.

## What is different

- Raw episodes are append-only ground truth. Generated summaries are never authoritative.
- Core memory has a fixed recovery order and is always loaded before relevance search.
- User intent, non-negotiable rules, permanent rejections, blockers, accepted decisions,
  counterexamples, and the current task are distinct record roles.
- Every important record carries authority, salience, rationale, temporal identity, and provenance.
- Conflicting active values fail closed instead of being silently merged by a summary.
- Conflicting user statements produce a clarification packet with both original wordings; the
  latest statement never wins automatically, because users can also forget prior decisions.
- A permanent rejection cannot be revived by an engineering proposal.
- Unresolved blockers and regression counterexamples can prevent a false `DONE`.
- Builds publish immutable, hashed generations through an atomic `CURRENT.json` pointer.
- The core is local and dependency-free; semantic retrieval is an optional discovery layer.

## Quick start

```bash
python -m pip install -e .
salience --root demo init

SOURCE_EPISODE=$(salience --root demo observe \
  --source-type user_message --content "Explicit user constraints must survive compaction.")

salience --root demo add \
  --id intent-001 --title "Protect user constraints" \
  --text "Explicit user constraints must survive compaction." \
  --type semantic --role user_intent --authority user_explicit \
  --salience core --importance 100 --must-read --source-episode "$SOURCE_EPISODE" \
  --assertion-key memory.user_constraints --value preserve

salience --root demo build
salience --root demo context
salience --root demo doctor --completion
```

## Recovery contract

Every resumed agent reads this order:

1. user intent;
2. non-negotiable rules;
3. permanent rejections;
4. open blockers;
5. accepted decisions;
6. counterexample regression set;
7. current task;
8. engineering proposals;
9. relevant working/archive records.

The first eight sections are deterministic. Search cannot evict or outrank them.

## User ambiguity contract

When two explicit user statements disagree on one `assertion_key`, `build` fails with
`USER_CONFIRMATION_REQUIRED`. Run `salience clarifications` to show both wordings, timestamps,
and source episodes. Resolution requires a new observation whose `source_type` is exactly
`user_confirmation`; neither an agent inference nor an ordinary newer message can silently erase
the older meaning.

## Lossless legacy migration

`migrate-evos-v2` preserves one immutable legacy generation and every SQLite row before any
semantic promotion. Evidence parity, semantic parity, and cutover readiness are independent
gates; the old store remains authoritative while review is pending. See the
[migration guide](docs/migration.md).
Codex projects should use the staged [integration contract](docs/codex-integration.md) so the
legacy authority remains intact until review and cutover checks pass.

## Status

`0.2.0` is an alpha reference implementation evolved from an internal predecessor named EvoS.
It intentionally favors auditability and
fail-closed behavior over autonomous LLM summarization. See [the architecture](docs/architecture.md)
and [migration guide](docs/migration.md). The [design references](docs/design-references.md) state
which external concepts were adopted, while the [project invariants](docs/invariants.md) prevent
the system from drifting into summary-first or vector-search-first memory.

## License

Apache-2.0.
