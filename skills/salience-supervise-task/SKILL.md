---
name: salience-supervise-task
description: Run or recover a long Codex coding, audit, or migration task with Salience Ledger, a tamper-evident one-item-per-round event chain, explicit user-ambiguity blocking, and clean-context external supervision. Use when work spans multiple rounds or compactions, another agent may drift or falsely claim completion, durable handoff is required, or the user asks to supervise, babysit, audit, resume, or keep a long task on-spec.
---

# Salience task supervision

Use Salience as the memory authority and task-run event chain. Do not create a second prose summary as a competing source of truth.

## Start or recover

1. Read repository instructions before acting.
2. Locate the Salience root and run `python -m salience_ledger --root <root> context`.
3. Run `clarifications` and `doctor`. If either reports a user conflict, show every original statement and source to the user and wait for explicit confirmation. Never select the newest wording.
4. For an existing run, execute `task-context --run-id <id>` after every resume, delegation, interruption, or compaction. Treat its output and the repository tree as authoritative; do not continue from a conversation summary alone.
5. For a new run, ask only about missing material choices, then create an immutable contract:

```text
python -m salience_ledger --root <root> task-init \
  --run-id <id> --goal <verifiable-goal> \
  --gate <exact-command-or-gate> --completion <criterion> \
  --scope <path> --protect <path> --red-line <rule>
```

Record commit and push authorization separately. Default both to false. Do not create a Codex `/goal` unless the user explicitly asks for one.

## Execute one item

Register one smallest verifiable item with a `task-event` of type `item_registered`. Work only on that item. Verify it in the same round, then append one `round_closed` event containing:

- the exact contract gate set with `pass`, `fail`, or `not_run`;
- durable evidence paths or command results;
- result `completed` or `blocked`;
- mode `implement` or `converge` and the net production-line delta.

Use `--payload-file` for JSON to avoid shell quoting errors. Register newly discovered work as another item; do not silently patch it on the side. A forced convergence round may remove or consolidate code but may not add net production lines.

After the event, rerun `task-doctor` and `task-context`. A test without a real production call site is not completion evidence.

## Supervise from clean context

Use a separate fresh Codex task or explicitly authorized subagent as supervisor. Give it only:

- `task-context` output;
- repository instructions;
- read-only Git status/diff and named evidence files.

Do not give it the executor's reasoning transcript or intended answer. The supervisor is read-only by default: it does not edit source, rewrite items, or accept prose as implementation. It may append only `directive_added` events describing one problem and required action. The executor resolves a directive only with durable evidence.

Create another Codex task, subagent, automation, commit, push, PR, or `/goal` only when the user has authorized that action. Clean context is a separation property, not extra authority.

## Handle owner decisions

Append `owner_blocked` with a concise question and at least two real options when the task requires a material user choice. Only the owner may append `owner_decision`, and it must cite a Salience observation whose `source_type` is `user_confirmation`. That observation must use `source_ref=task-run/<run-id>/blocker/<blocker-id>` so an unrelated or older confirmation cannot resolve the current question.

For conflicting user meanings, use Salience `clarifications` and `resolve`; do not reduce the conflict to an engineering choice.

## Finish honestly

Run `task-doctor --completion`. It must fail while any item, directive, owner decision, memory conflict, blocking counterexample, or latest gate remains open. The executor may then append `completion_claimed` with evidence for every exact contract completion criterion; a clean-context supervisor or owner may append `completion_accepted` with independently reproduced evidence for those same criteria.

Report separately:

- implemented and verified behavior;
- structural or package checks;
- unresolved semantic review or migration status;
- actions deliberately not authorized or not performed.
