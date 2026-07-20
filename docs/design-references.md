# Design references and boundaries

Salience Ledger learns from several open-source memory systems without copying their product
architecture or making them runtime dependencies.

## Concepts adopted

- [MemGPT](https://github.com/cpacker/MemGPT) and
  [Letta](https://github.com/letta-ai/letta): a small always-resident core plus larger recall and
  archival stores. Salience Ledger makes core residency deterministic rather than model-managed.
- [Graphiti](https://github.com/getzep/graphiti): provenance, event time, and validity time are
  separate concerns; later knowledge does not erase what was known earlier.
- [LangGraph](https://github.com/langchain-ai/langgraph): semantic, episodic, and procedural memory
  are different types and should not collapse into one undifferentiated vector collection.
- [agent-memory](https://github.com/axiomhq/agent-memory): hot/warm/cold disclosure, consolidation,
  and a `doctor` workflow are useful operational patterns.
- [Mem0](https://github.com/mem0ai/mem0): memory exists at multiple scopes and should remain usable
  across sessions and agents.
- [graphkit](https://github.com/levi-qiao/graphkit): separate an executor from a clean-context
  supervisor and connect them through durable, inspectable, one-way task state.
- [pi-autoresearch](https://github.com/davebcn87/pi-autoresearch): preserve append-only run history,
  rehydrate after compaction from files rather than summaries, cap unattended iterations, and
  apply correctness backpressure to metric-driven loops.

## Deliberate boundaries

- No LLM-generated summary is authoritative.
- No embedding score can evict user intent, permanent rejections, blockers, or counterexamples.
- No cloud service, graph database, or embedding model is required by the core.
- No automatic "latest user message wins" rule exists.
- No model may resolve conflicting user meanings; only a new explicit confirmation can do so.
- No completion claim is valid while a blocking counterexample remains active.
- No private conversation or proprietary project memory is shipped as an example dataset.
- graphkit and pi-autoresearch are design references, not copied code or runtime dependencies;
  Salience task events and role enforcement are an independent implementation.

These boundaries are project invariants. Future adapters may add graph or semantic discovery, but
their outputs remain non-authoritative hints behind the deterministic ledger.
