# Project invariants

1. Evidence is append-only; projections are disposable and reproducible.
2. A record ID is immutable. Semantic changes require a new ID.
3. Core records require source episodes.
4. Authority and salience are independent fields.
5. User wording is never inferred from agent prose.
6. Two ambiguous user meanings require user confirmation, even when the newer statement appears
   clearer. Both original wordings must be shown.
7. Permanent rejections cannot be revived by renaming a proposal.
8. Active assertion conflicts fail closed.
9. Blocking counterexamples prevent completion.
10. Search is retrieval, not governance.
11. Expired facts stay in evidence but leave the current projection.
12. Generated bundles and their selection pointer are hash-verified.
13. Core memory has a hard size budget; making everything important is treated as a fault.
14. Public fixtures are synthetic and privacy-scanned.
