# Bounded novel memory

Long novels create a different context problem than short-form generation: the durable continuity ledger grows chapter after chapter, while local models still have a finite useful attention budget. Sending the entire accumulated ledger into every chapter-level prompt eventually spends more context on old resolved history than on the facts that matter for the current scene.

Novel Generator keeps the full continuity ledger as the canonical stored state. The worker now compiles a read-only memory packet for chapter-level planning, drafting, critique, revision, expansion, developmental revision, publication polishing, and final editing once the serialized ledger exceeds a configured size.

The compiler prioritizes current world state, chapter-relevant character state, open promises and threads, trust fractures, emotional loops, entity and system state, recent timeline events, system transitions, side-character decisions, civilian pressure, and a small amount of resolved history. Selection is deterministic: lexical relevance to the current chapter plan/outline and recent context is combined with recency. The canonical ledger is never truncated or mutated.

The continuity-update stage intentionally still receives the complete ledger. This preserves the cumulative state machine and prevents a compact prompt view from accidentally becoming the persisted source of truth.

## Whole-manuscript developmental context

Developmental rewrite planning has a different scaling problem: it needs a view of every chapter, but it does not need every sentence of every chapter. Passing a complete 80,000–150,000-word draft through one local-model request can overwhelm useful attention even when the model technically accepts the context length.

For that stage, the worker builds a bounded chapter-capsule map. Every chapter remains represented, but full prose is replaced with the chapter summary, outline summary, irreversible story turn, selected QA signals, and—when the budget allows—short opening and closing excerpts. If the book is too large for the detailed form, the compiler degrades detail uniformly across all chapters before dropping any structural information. This keeps whole-book coverage while reserving context for diagnosis and rewrite planning.

Saved chapter prose is never changed by this process. The capsules are a temporary prompt view only.

## Ollama context window

The native Ollama chat API accepts model runtime options including `num_ctx`. Novel Generator now sends an explicit context size on Ollama generation calls instead of depending on the server or model default. The default is 32,768 tokens, which is large enough for the bounded chapter and developmental contexts used by this pipeline while remaining configurable for machines with tighter RAM or VRAM limits.

If a selected model supports less context than the configured value, or the machine cannot comfortably run that context size, lower `OLLAMA_NUM_CTX`. If the model and hardware support substantially more context, it can be raised up to the application validation limit.

## Configuration

```env
OLLAMA_NUM_CTX=32768
NOVEL_MEMORY_ENABLED=1
NOVEL_MEMORY_MAX_CHARS=14000
NOVEL_MANUSCRIPT_CONTEXT_MAX_CHARS=70000
```

`NOVEL_MEMORY_ENABLED=0` disables the runtime context compiler. `NOVEL_MEMORY_MAX_CHARS` is the approximate compact-JSON character budget for the continuity portion of chapter-level prompts and is clamped to 4,000–50,000 characters. `NOVEL_MANUSCRIPT_CONTEXT_MAX_CHARS` controls the whole-book chapter-capsule payload used for developmental rewrite planning and is clamped to 30,000–160,000 characters. `OLLAMA_NUM_CTX` is validated from 2,048–262,144 tokens.

Small continuity ledgers pass through unchanged. Compaction starts only after the ledger exceeds the configured budget. The runtime integration is fail-open: if a future prompt builder changes serialization format and the compiler cannot safely replace a context block, the original prompt is used instead of failing the generation run.

For local 8B–20B models, the default 14,000-character continuity budget is intended to leave more attention for the chapter outline, plan, prose, style profile, and current scene while retaining the facts most likely to prevent character, canon, timeline, and unresolved-thread drift. The default 70,000-character manuscript budget gives the developmental planner broad whole-book coverage without asking it to reason over the raw prose of an entire novel in a single call.