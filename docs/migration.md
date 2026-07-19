# Migration from a summary-first store

1. Keep the existing raw memories as immutable source episodes.
2. Promote only explicitly supported records into typed control records.
3. Split mixed statements into user facts, accepted decisions, proposals, and inferences.
4. Give every governing statement an `assertion_key` and canonical JSON value.
5. Materialize permanent rejections and known counterexamples before importing general notes.
6. Run `doctor`; resolve conflicts rather than combining them into prose.
7. Build and test recovery from `CORE_MEMORY.md` without using the old conversation summary.

Never publish a private store as an example dataset. Use synthetic examples only.

## Legacy EvoS v2 adapter

`salience --root PROJECT import-evos-v2 PATH_TO_JSONL` imports each legacy row as an
`archive/note` with `agent_inference` authority, zero confidence, and a
`migration_unreviewed` tag. This is intentionally conservative: old labels and summaries cannot
silently become new core truth. Promote important items only after reading their original source
and assigning the correct authority, role, assertion key, and rationale.
