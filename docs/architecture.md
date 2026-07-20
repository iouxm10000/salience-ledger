# Architecture

## Two planes

The evidence plane is `.salience/episodes.jsonl`, an append-only journal. The control plane is an
immutable generation containing a SQLite projection, a deterministic `CORE_MEMORY.md`, and a
hash manifest. `CURRENT.json` atomically selects one verified generation.

An LLM may propose derived records, but it cannot rewrite evidence or promote its own inference
to user authority. Derived records must preserve provenance to episodes.

## Salience is not similarity

`core` means always present after recovery. `working` means relevant during the current effort.
`archive` means retained and searchable. This is deliberately separate from importance and
semantic similarity: a short permanent rejection can be more governing than a long, highly
similar historical note.

## Conflict semantics

Active records sharing an `assertion_key` must have one canonical value. Different values cause
the build to fail until an explicit resolution supersedes or resolves the older record. The
system never asks a language model to blend incompatible rules.

User-authored conflicts are stricter: they emit `USER_CONFIRMATION_REQUIRED` and a clarification
packet containing both original statements and their evidence. Recency is not authority. A new
`user_confirmation` episode must explicitly supersede, scope-split, or withdraw the old meanings.

## Temporal semantics

Records distinguish `known_at` from optional `valid_from` / `valid_to`. This permits later
corrections without pretending the corrected fact was known earlier. Superseded records remain
in the journal.

## Recovery

Recovery loads the fixed core sections first, then performs deterministic lexical retrieval over
working and archive memory. Optional embedding or graph adapters may suggest additional records,
but those results remain discovery hints and may not override the core.

## Long-task supervision plane

`.salience/task-runs/<run-id>/contract.json` freezes the goal, gates, completion criteria, scope,
red lines, and mutation authorization. `events.jsonl` is a hash-chained handoff log shared by an
executor and an independent clean-context supervisor. The executor closes one item per round; the
supervisor can only append one-way corrective directives. Neither plane replaces memory evidence.

`task-context` deterministically combines verified core memory with open task items, directives,
owner blockers, and the last two verified rounds. It refuses to render when governing memory is
ambiguous, so compaction recovery cannot silently choose a newer user statement.
